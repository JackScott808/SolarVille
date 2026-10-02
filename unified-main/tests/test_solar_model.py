# Branch: unified-main
# File: tests/test_solar_model.py
"""Tests for the London solar model and PVGIS loader."""

import calendar
import glob
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from core.config import ConfigManager, ConfigurationError, ProsumerConfig
from hardware.solar_manager import SolarMonitor
from simulation.solar_model import (PvgisSeries, SolarModel, clear_sky_ghi, create_solar_source,
                                    diffuse_fraction, solar_position)

LONDON = (51.5074, -0.1278)


def month_energy(model: SolarModel, year: int, month: int) -> float:
    """Total kWh produced in a month."""
    t, total = datetime(year, month, 1), 0.0
    while t.month == month:
        total += model.energy_kwh(t, 3600)
        t += timedelta(hours=1)
    return total


class TestAstronomy(unittest.TestCase):
    def test_sun_is_due_south_and_highest_at_solar_noon(self):
        # London solar noon on the summer solstice is ~12:00 UTC; the sun is ~62 degrees up
        cos_zen, azimuth = solar_position(datetime(2012, 6, 21, 12, 2), *LONDON)
        altitude = 90 - __import__("math").degrees(__import__("math").acos(cos_zen))
        self.assertAlmostEqual(altitude, 62.0, delta=1.0)
        self.assertAlmostEqual(__import__("math").degrees(azimuth), 180, delta=3)

    def test_winter_noon_sun_is_much_lower(self):
        import math
        cos_zen, _ = solar_position(datetime(2012, 12, 21, 12, 0), *LONDON)
        self.assertAlmostEqual(90 - math.degrees(math.acos(cos_zen)), 15.0, delta=1.0)

    def test_day_length_matches_london_sunrise_and_sunset(self):
        # Known London times (UTC): 21 Jun sunrise 03:43 / sunset 20:21; 21 Dec 08:04 / 15:53
        def above_horizon(ts):
            return solar_position(ts, *LONDON)[0] > 0
        self.assertFalse(above_horizon(datetime(2012, 6, 21, 3, 20)))
        self.assertTrue(above_horizon(datetime(2012, 6, 21, 4, 10)))
        self.assertTrue(above_horizon(datetime(2012, 6, 21, 20, 0)))
        self.assertFalse(above_horizon(datetime(2012, 6, 21, 20, 45)))
        self.assertFalse(above_horizon(datetime(2012, 12, 21, 7, 40)))
        self.assertTrue(above_horizon(datetime(2012, 12, 21, 8, 30)))
        self.assertTrue(above_horizon(datetime(2012, 12, 21, 15, 30)))
        self.assertFalse(above_horizon(datetime(2012, 12, 21, 16, 20)))

    def test_clear_sky_and_diffuse_bounds(self):
        self.assertEqual(clear_sky_ghi(-0.1), 0.0)
        self.assertLess(clear_sky_ghi(1.0), 1100)
        for kt in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
            self.assertTrue(0.0 < diffuse_fraction(kt) <= 1.0)


class TestSolarModel(unittest.TestCase):
    def setUp(self):
        self.model = SolarModel(system_kwp=4.0)

    def test_no_generation_at_night(self):
        for month in range(1, 13):
            for hour in (0, 1, 2, 22, 23):
                self.assertEqual(self.model.power_kw(datetime(2012, month, 10, hour)), 0.0)

    def test_summer_far_outproduces_winter(self):
        # Short, cloudy winter days: real PVGIS data for London has July at ~3.3x December
        summer = month_energy(self.model, 2012, 6)
        winter = month_energy(self.model, 2012, 12)
        self.assertGreater(summer, 2.2 * winter)
        self.assertLess(summer, 6 * winter)  # ...and not wildly more than that

    def test_clear_midday_is_stronger_in_summer_than_winter(self):
        self.assertGreater(max(self.model.power_kw(datetime(2012, 6, d, 12)) for d in range(1, 15)),
                           1.5 * max(self.model.power_kw(datetime(2012, 12, d, 12)) for d in range(1, 15)))

    def test_power_never_exceeds_system_rating(self):
        t = datetime(2012, 1, 1)
        peak = 0.0
        while t.year == 2012:
            peak = max(peak, self.model.power_kw(t))
            t += timedelta(minutes=30)
        self.assertLess(peak, 4.0)
        self.assertGreater(peak, 2.0)  # but a good day does get well into the rating

    def test_annual_yield_is_typical_for_london(self):
        # PVGIS gives ~950-1050 kWh/kWp for a 35 degree south-facing system in London
        one_kwp = SolarModel(system_kwp=1.0)
        total, t = 0.0, datetime(2013, 1, 1)
        while t.year == 2013:
            total += one_kwp.energy_kwh(t, 3600)
            t += timedelta(hours=1)
        self.assertTrue(880 < total < 1100, f"annual yield {total:.0f} kWh/kWp")

    def test_weather_is_deterministic(self):
        a, b = SolarModel(), SolarModel()
        ts = datetime(2012, 5, 3, 11, 30)
        self.assertEqual(a.power_kw(ts), b.power_kw(ts))
        self.assertEqual(a.energy_kwh(ts), b.energy_kwh(ts))

    def test_days_differ(self):
        noon = [self.model.power_kw(datetime(2012, 7, d, 12)) for d in range(1, 15)]
        self.assertGreater(max(noon) - min(noon), 0.5)  # sunny and gloomy days both occur

    def test_energy_scales_with_system_size(self):
        small, big = SolarModel(system_kwp=2.0), SolarModel(system_kwp=4.0)
        ts = datetime(2012, 6, 20, 12)
        self.assertAlmostEqual(big.energy_kwh(ts) / small.energy_kwh(ts), 2.0, places=6)

    def test_energy_matches_power_times_time(self):
        ts = datetime(2012, 6, 20, 12)
        self.assertAlmostEqual(self.model.energy_kwh(ts, 600), self.model.power_kw(ts + timedelta(minutes=5)) / 6,
                               places=6)


PVGIS_SAMPLE = """Latitude (decimal degrees):\t51.507
Longitude (decimal degrees):\t-0.128
Elevation (m):\t11

Slope: 35 deg. (optimum at 36 deg.)
Azimuth: 0 deg.

time,P,G(i),H_sun,T2m,WS10m,Int
20120624:1110,500.0,700.1,60.2,18.1,3.0,0.0
20120624:1210,800.0,900.1,61.2,19.1,3.1,0.0
20120624:1310,0.0,10.0,40.0,19.0,3.0,0.0

P: PV system power (W)
Int: 1 means solar radiation values are reconstructed
"""


class TestPvgis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "pvgis_51.51_-0.13_2012.csv")
        with open(self.path, "w") as f:
            f.write(PVGIS_SAMPLE)

    def test_parses_pvgis_csv_and_scales_to_system_size(self):
        series = PvgisSeries.from_files([self.path], system_kwp=4.0)
        self.assertEqual(series.years, [2012])
        self.assertAlmostEqual(series.power_kw(datetime(2012, 6, 24, 12, 10)), 3.2)  # 800 W/kWp x 4, at the sample
        self.assertTrue(series.covers(datetime(2012, 6, 24, 11, 0)))
        self.assertFalse(series.covers(datetime(2012, 6, 25, 11, 0)))

    def test_values_are_interpolated_between_the_hh10_samples(self):
        series = PvgisSeries.from_files([self.path], system_kwp=1.0)
        # samples: 11:10 = 500 W, 12:10 = 800 W, 13:10 = 0 W
        self.assertAlmostEqual(series.power_kw(datetime(2012, 6, 24, 11, 40)), 0.65)   # halfway 500 -> 800
        self.assertAlmostEqual(series.power_kw(datetime(2012, 6, 24, 12, 40)), 0.40)   # halfway 800 -> 0
        self.assertAlmostEqual(series.power_kw(datetime(2012, 6, 24, 12, 5)),          # before the 12:10 sample
                               0.5 * 500 / 1000 * 0 + (500 + (800 - 500) * (55 / 60)) / 1000)

    def test_unusable_file_is_skipped(self):
        bad = os.path.join(self.tmp.name, "pvgis_bad.csv")
        with open(bad, "w") as f:
            f.write("<html>rate limited</html>")
        self.assertIsNone(PvgisSeries.from_files([bad], 4.0))

    def test_auto_uses_real_data_where_it_exists_and_model_elsewhere(self):
        source = create_solar_source(4.0, 35, 180, *LONDON, source="auto", data_dir=self.tmp.name)
        self.assertAlmostEqual(source.power_kw(datetime(2012, 6, 24, 12, 10)), 3.2)  # real data
        model_value = SolarModel(4.0).power_kw(datetime(2012, 6, 25, 12, 30))
        self.assertAlmostEqual(source.power_kw(datetime(2012, 6, 25, 12, 30)), model_value)  # fallback

    def test_model_source_ignores_files(self):
        source = create_solar_source(4.0, 35, 180, *LONDON, source="model", data_dir=self.tmp.name)
        self.assertIsNone(source.series)

    def test_pvgis_source_requires_files(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(FileNotFoundError):
                create_solar_source(4.0, 35, 180, *LONDON, source="pvgis", data_dir=empty)

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            create_solar_source(4.0, 35, 180, *LONDON, source="magic", data_dir=self.tmp.name)


REAL_FILES = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                           "dataset", "pvgis_*.csv")))


@unittest.skipUnless(REAL_FILES, "no real PVGIS files in dataset/ (run scripts/fetch_pvgis.py)")
class TestRealPvgisData(unittest.TestCase):
    """Checks against the actual PVGIS-SARAH2 London files, and the model against them."""

    @classmethod
    def setUpClass(cls):
        cls.series = PvgisSeries.from_files(REAL_FILES, system_kwp=1.0)

    def test_files_parse_completely(self):
        self.assertIsNotNone(self.series)
        for year in self.series.years:
            hours = sum(1 for k in self.series._hourly if k[0] == year)
            self.assertEqual(hours, 8784 if calendar.isleap(year) else 8760, year)

    def test_covers_the_whole_demand_dataset_period(self):
        if not {2012, 2013, 2014} <= set(self.series.years):
            self.skipTest("needs 2012-2014")
        t = datetime(2012, 10, 12)
        while t < datetime(2014, 3, 1):
            self.assertTrue(self.series.covers(t), t)
            t += timedelta(hours=1)

    def test_values_are_physically_plausible(self):
        values = list(self.series._hourly.values())
        self.assertGreaterEqual(min(values), 0.0)
        self.assertLess(max(values), 1000.0)  # W per kWp
        self.assertEqual(self.series._hourly[(self.series.years[0], 6, 21, 2)], 0.0)  # 02:10 UTC in June: dark

    def test_real_seasons(self):
        def month_total(year, month):
            return sum(v for (y, m, d, h), v in self.series._hourly.items() if y == year and m == month) / 1000
        year = self.series.years[0]
        self.assertGreater(month_total(year, 7), 2.5 * month_total(year, 12))

    def test_model_annual_total_matches_real_data(self):
        model = SolarModel(system_kwp=1.0)
        real_total = sum(self.series._hourly.values()) / 1000 / len(self.series.years)
        model_total = 0.0
        for year in range(2005, 2021):
            t = datetime(year, 1, 1, 0, 15)  # midpoints of half-hour slots
            while t.year == year:
                model_total += model.power_kw(t) * 0.5
                t += timedelta(minutes=30)
        model_total /= 16
        self.assertAlmostEqual(model_total / real_total, 1.0, delta=0.06,
                               msg=f"model {model_total:.0f} vs real {real_total:.0f} kWh/kWp/year")

    def test_model_sun_matches_pvgis_sun_height(self):
        """The model's astronomy agrees with PVGIS's own H_sun column (also validates UTC timestamps)."""
        import math
        errors = []
        with open(REAL_FILES[0]) as f:
            for line in f:
                if len(line) > 9 and line[:8].isdigit() and line[8] == ":":
                    parts = line.split(",")
                    height = float(parts[3])
                    if height > 0:
                        ts = datetime.strptime(parts[0], "%Y%m%d:%H%M")
                        cos_zen, _ = solar_position(ts, 51.507, -0.128)
                        errors.append(abs(height - (90 - math.degrees(math.acos(cos_zen)))))
        self.assertGreater(len(errors), 3000)
        self.assertLess(sum(errors) / len(errors), 0.3)

    def test_auto_source_uses_the_real_files(self):
        source = create_solar_source(4.0, 35, 180, *LONDON, source="auto", data_dir=os.path.dirname(REAL_FILES[0]))
        ts = datetime(self.series.years[0], 6, 21, 11, 10)
        self.assertAlmostEqual(source.power_kw(ts), 4 * self.series.power_kw(ts))


class TestSolarMonitorWithModel(unittest.TestCase):
    def test_readings_follow_the_sun(self):
        monitor = SolarMonitor(mock_mode=True, solar_source=create_solar_source(4.0, 35, 180, *LONDON, "model"),
                               interval_seconds=1800)
        night = monitor.get_readings(datetime(2012, 10, 24, 2, 0))
        noon = max(monitor.get_readings(datetime(2012, 10, 24, 11, 0))["solar_energy"],
                   monitor.get_readings(datetime(2012, 10, 24, 12, 0))["solar_energy"])
        self.assertEqual(night["solar_energy"], 0.0)
        self.assertEqual(night["solar_power"], 0.0)
        self.assertGreater(noon, 0.0)

    def test_power_and_energy_are_consistent(self):
        monitor = SolarMonitor(mock_mode=True, solar_source=create_solar_source(4.0, 35, 180, *LONDON, "model"),
                               interval_seconds=1800)
        r = monitor.get_readings(datetime(2012, 6, 21, 12, 0))
        self.assertAlmostEqual(r["solar_power"] * 0.5 / 1000, r["solar_energy"], places=9)  # W over half an hour

    def test_without_timestamp_falls_back_to_random_mock(self):
        monitor = SolarMonitor(mock_mode=True)
        self.assertGreater(monitor.get_readings()["solar_power"], 0)


class TestProsumerConfig(unittest.TestCase):
    def test_defaults_are_a_typical_london_home(self):
        p = ProsumerConfig()
        self.assertEqual((p.system_kwp, p.solar_source), (4.0, "auto"))
        self.assertAlmostEqual(p.latitude, 51.5, delta=0.1)

    def test_validation_rejects_nonsense(self):
        for bad in ({"system_kwp": 0}, {"tilt_deg": 120}, {"solar_source": "x"}, {"latitude": 95},
                    {"storage_capacity_kwh": -1}):
            config = ConfigManager("config")
            config.prosumer_config = ProsumerConfig(**bad)
            with self.assertRaises(ConfigurationError, msg=str(bad)):
                config._validate_prosumer()


if __name__ == "__main__":
    unittest.main()
