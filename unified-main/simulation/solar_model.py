# Branch: unified-main
# File: solar_model.py

"""Realistic solar generation for a household PV system.

Two sources, one interface (``energy_kwh(timestamp, interval_seconds)``):

``SolarModel``
    A physical model: where the sun is (day of year + time of day + latitude), clear-sky
    irradiance, how much of it clouds let through, split into beam/diffuse light and
    projected onto a tilted panel. London's average monthly sunshine is baked in, so winter
    produces a fraction of summer. Cloud cover varies day to day, but is *deterministic per
    date*, so every node in the network (and every re-run) sees the same sky.

``PvgisSeries``
    Real hourly PV output downloaded from PVGIS (``scripts/fetch_pvgis.py``). Used instead of
    the model automatically when a file covering the simulated date is present.

Timestamps are treated as UTC (GMT). The model's accuracy is that of a clear-sky + climatology
model: right shape and the right order of magnitude (about 3.6 MWh/year for 4 kWp), not a
forecast of any particular day. Use PVGIS data when you need the real weather.
"""

import calendar
import glob
import logging
import math
import os
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SOLAR_CONSTANT = 1361.0  # W/m2

# Mean daily global horizontal irradiation for London, kWh/m2/day, by month.
# APPROXIMATE typical-year values (about 990 kWh/m2/year), consistent with published UK
# climatology but not copied from a specific dataset. Replace by real PVGIS data for accuracy.
LONDON_MEAN_DAILY_GHI = (0.65, 1.25, 2.25, 3.6, 4.7, 5.0, 4.9, 4.1, 2.9, 1.7, 0.85, 0.5)

DAY_VARIABILITY = 2.5  # Beta concentration for day-to-day clearness: lower = more extreme days
PERFORMANCE_RATIO = 0.80  # inverter, wiring, temperature and soiling losses
ALBEDO = 0.2


# --------------------------------------------------------------------------- astronomy

def solar_position(ts: datetime, latitude: float, longitude: float) -> Tuple[float, float]:
    """Return (cos of zenith angle, azimuth in radians clockwise from north) for a UTC time."""
    n = ts.timetuple().tm_yday
    hour = ts.hour + ts.minute / 60 + ts.second / 3600
    g = 2 * math.pi / 365 * (n - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))  # minutes
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    true_solar_minutes = hour * 60 + eqtime + 4 * longitude  # longitude east-positive, UTC
    hour_angle = math.radians(true_solar_minutes / 4 - 180)
    lat = math.radians(latitude)

    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    azimuth = math.atan2(math.sin(hour_angle),
                         math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat)) + math.pi
    return cos_zen, azimuth


def extraterrestrial_normal(ts: datetime) -> float:
    """Sun's irradiance on a surface facing it at the top of the atmosphere (W/m2)."""
    n = ts.timetuple().tm_yday
    return SOLAR_CONSTANT * (1 + 0.033 * math.cos(2 * math.pi * n / 365))


def clear_sky_ghi(cos_zen: float) -> float:
    """Haurwitz clear-sky global horizontal irradiance (W/m2)."""
    if cos_zen <= 0.0:
        return 0.0
    return 1098.0 * cos_zen * math.exp(-0.057 / cos_zen)


def diffuse_fraction(kt: float) -> float:
    """Erbs et al. (1982): share of horizontal irradiance that is diffuse, from clearness kt."""
    if kt <= 0.22:
        return 1.0 - 0.09 * kt
    if kt <= 0.80:
        return 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    return 0.165


# ------------------------------------------------------------------------------ model

@lru_cache(maxsize=None)
def _monthly_clear_sky_index(latitude: float, longitude: float) -> Tuple[float, ...]:
    """Mean cloud transmission per month, so the model's monthly totals match the climatology."""
    indices = []
    for month in range(1, 13):
        mid = datetime(2011, month, 15)  # a non-leap year; mid-month is representative
        total = 0.0
        for step in range(288):  # 5 minute steps
            cos_zen, _ = solar_position(mid + timedelta(minutes=5 * step), latitude, longitude)
            total += clear_sky_ghi(cos_zen) * (5 / 60) / 1000  # kWh/m2
        indices.append(min(LONDON_MEAN_DAILY_GHI[month - 1] / total, 1.0))
    return tuple(indices)


class SolarModel:
    """Physical + climatological model of a rooftop PV system."""

    def __init__(self, system_kwp: float = 4.0, tilt_deg: float = 35.0, azimuth_deg: float = 180.0,
                 latitude: float = 51.5074, longitude: float = -0.1278,
                 performance_ratio: float = PERFORMANCE_RATIO):
        """
        Args:
            system_kwp: Installed peak power (a typical UK home has 3-4 kWp)
            tilt_deg: Panel tilt from horizontal
            azimuth_deg: Panel direction, clockwise from north (180 = due south)
            latitude: Degrees north
            longitude: Degrees east (London is slightly west, so negative)
            performance_ratio: Overall system efficiency (0-1)
        """
        self.system_kwp = system_kwp
        self.tilt = math.radians(tilt_deg)
        self.panel_azimuth = math.radians(azimuth_deg)
        self.latitude = latitude
        self.longitude = longitude
        self.performance_ratio = performance_ratio
        self.description = (f"clear-sky model, {system_kwp:g} kWp at lat {latitude:.2f}, "
                            f"tilt {tilt_deg:g} deg, azimuth {azimuth_deg:g} deg")

    # -- weather ---------------------------------------------------------------------

    def _day_clearness(self, day: date) -> float:
        """Cloud transmission for a whole day: mean = the month's climatology, widely spread."""
        month_mean = _monthly_clear_sky_index(self.latitude, self.longitude)[day.month - 1]
        rng = random.Random(f"{self.latitude:.2f},{self.longitude:.2f},{day.toordinal()}")
        a, b = month_mean * DAY_VARIABILITY, (1 - month_mean) * DAY_VARIABILITY
        return max(rng.betavariate(a, b), 0.05)

    @staticmethod
    @lru_cache(maxsize=4096)
    def _flicker_phases(day_ordinal: int):
        rng = random.Random(day_ordinal * 7919)
        return [(rng.uniform(0.6, 4.0), rng.uniform(0, 2 * math.pi)) for _ in range(3)]  # (cycles/hour, phase)

    def _clear_sky_index(self, ts: datetime) -> float:
        """Cloud transmission at an instant: the day's level plus passing clouds."""
        k_day = self._day_clearness(ts.date())
        hours = ts.hour + ts.minute / 60
        wobble = sum(math.sin(2 * math.pi * f * hours + p) for f, p in self._flicker_phases(ts.toordinal())) / 3
        # Intermittent cloud (mid-range days) flickers the most; clear and overcast days are steady.
        return min(max(k_day * (1 + 0.8 * k_day * (1 - k_day) * wobble * 2), 0.03), 1.0)

    # -- irradiance and power ----------------------------------------------------------

    def ghi(self, ts: datetime) -> float:
        """Global horizontal irradiance (W/m2)."""
        cos_zen, _ = solar_position(ts, self.latitude, self.longitude)
        return clear_sky_ghi(cos_zen) * self._clear_sky_index(ts)

    def plane_of_array(self, ts: datetime) -> float:
        """Irradiance on the tilted panel (W/m2): beam + sky diffuse + ground reflection."""
        cos_zen, azimuth = solar_position(ts, self.latitude, self.longitude)
        if cos_zen < 0.02:  # sun on or below the horizon
            return 0.0
        ghi = clear_sky_ghi(cos_zen) * self._clear_sky_index(ts)
        kt = min(ghi / (extraterrestrial_normal(ts) * cos_zen), 1.0)
        dhi = diffuse_fraction(kt) * ghi
        dni = min((ghi - dhi) / cos_zen, extraterrestrial_normal(ts))

        sin_zen = math.sqrt(max(1 - cos_zen ** 2, 0.0))
        cos_incidence = (cos_zen * math.cos(self.tilt)
                         + sin_zen * math.sin(self.tilt) * math.cos(azimuth - self.panel_azimuth))
        beam = dni * max(cos_incidence, 0.0)
        sky = dhi * (1 + math.cos(self.tilt)) / 2
        ground = ghi * ALBEDO * (1 - math.cos(self.tilt)) / 2
        return beam + sky + ground

    def power_kw(self, ts: datetime) -> float:
        """AC power of the system at an instant (kW)."""
        return self.system_kwp * self.plane_of_array(ts) / 1000 * self.performance_ratio

    def energy_kwh(self, ts: datetime, interval_seconds: float = 1800) -> float:
        """Energy produced over [ts, ts + interval) in kWh."""
        return _integrate(self.power_kw, ts, interval_seconds)


def _integrate(power_kw, ts: datetime, interval_seconds: float) -> float:
    """Midpoint-rule integral of a power function over an interval (kWh)."""
    steps = max(1, math.ceil(interval_seconds / 600))
    dt = interval_seconds / steps
    total = sum(power_kw(ts + timedelta(seconds=dt * (i + 0.5))) for i in range(steps))
    return total * dt / 3600


# -------------------------------------------------------------------------- real data

_DATA_LINE = re.compile(r"^(\d{8}):(\d{4}),")


class PvgisSeries:
    """Hourly PV output from a PVGIS ``seriescalc`` CSV (generated for 1 kWp), scaled to the system size."""

    def __init__(self, hourly_w_per_kwp: Dict[Tuple[int, int, int, int], float], system_kwp: float,
                 source: str = "PVGIS"):
        self._hourly = hourly_w_per_kwp
        self.system_kwp = system_kwp
        years = sorted({k[0] for k in hourly_w_per_kwp})
        self.years = years
        self.description = f"real {source} hourly data for {', '.join(map(str, years))}, scaled to {system_kwp:g} kWp"

    @classmethod
    def from_files(cls, paths, system_kwp: float) -> Optional["PvgisSeries"]:
        """Parse one or more PVGIS CSV files. Returns None if nothing usable was found."""
        hourly: Dict[Tuple[int, int, int, int], float] = {}
        for path in paths:
            try:
                hourly.update(cls._parse(path))
            except (OSError, ValueError) as e:
                logger.warning(f"Skipping unreadable PVGIS file {path}: {e}")
        return cls(hourly, system_kwp) if hourly else None

    @staticmethod
    def _parse(path: str) -> Dict[Tuple[int, int, int, int], float]:
        power_col = None
        out: Dict[Tuple[int, int, int, int], float] = {}
        with open(path, newline="") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("time,"):
                    power_col = [c.strip() for c in line.split(",")].index("P")
                    continue
                m = _DATA_LINE.match(line)
                if not m or power_col is None:
                    continue
                stamp = m.group(1)
                hour = int(m.group(2)[:2])
                key = (int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]), hour)
                out[key] = float(line.split(",")[power_col])  # W for a 1 kWp system
        if not out:
            raise ValueError("no PVGIS data rows found")
        return out

    def covers(self, ts: datetime) -> bool:
        return (ts.year, ts.month, ts.day, ts.hour) in self._hourly

    def power_kw(self, ts: datetime) -> float:
        w = self._hourly.get((ts.year, ts.month, ts.day, ts.hour), 0.0)
        return w / 1000 * self.system_kwp

    def energy_kwh(self, ts: datetime, interval_seconds: float = 1800) -> float:
        return _integrate(self.power_kw, ts, interval_seconds)


class SolarSource:
    """Picks real PVGIS data for a timestamp when available, otherwise the model."""

    def __init__(self, model: SolarModel, series: Optional[PvgisSeries] = None):
        self.model = model
        self.series = series
        self.description = series.description + f" (falls back to {model.description})" if series else model.description

    def power_kw(self, ts: datetime) -> float:
        if self.series is not None and self.series.covers(ts):
            return self.series.power_kw(ts)
        return self.model.power_kw(ts)

    def energy_kwh(self, ts: datetime, interval_seconds: float = 1800) -> float:
        return _integrate(self.power_kw, ts, interval_seconds)


def create_solar_source(system_kwp: float, tilt_deg: float, azimuth_deg: float, latitude: float,
                        longitude: float, source: str = "auto", data_dir: str = "dataset") -> SolarSource:
    """Build a SolarSource.

    Args:
        source: 'auto' (use PVGIS files in data_dir if present, else the model),
                'model' (always the model) or 'pvgis' (require PVGIS files)
    """
    if source not in ("auto", "model", "pvgis"):
        raise ValueError(f"Unknown solar source '{source}' (expected auto, model or pvgis)")
    model = SolarModel(system_kwp, tilt_deg, azimuth_deg, latitude, longitude)
    series = None
    if source != "model":
        files = sorted(glob.glob(os.path.join(data_dir, "pvgis_*.csv")))
        series = PvgisSeries.from_files(files, system_kwp)
        if series is None and source == "pvgis":
            raise FileNotFoundError(
                f"solar source is 'pvgis' but no pvgis_*.csv files were found in {data_dir}. "
                "Run scripts/fetch_pvgis.py to download them."
            )
    return SolarSource(model, series)
