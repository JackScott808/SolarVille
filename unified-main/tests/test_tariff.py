# Branch: unified-main
# File: tests/test_tariff.py
"""Tests for the time-of-use tariff and peer-to-peer price formation."""

import unittest
from datetime import datetime

from core.tariff import ImportBand, Tariff


class TestImportPrice(unittest.TestCase):
    def setUp(self):
        self.tariff = Tariff()

    def test_default_bands_by_time_of_day(self):
        # January: London local time == UTC
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 3, 0)), 0.12)    # off-peak
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 12, 0)), 0.27)   # standard
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 17, 30)), 0.36)  # evening peak
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 22, 0)), 0.27)

    def test_band_boundaries_are_start_inclusive_end_exclusive(self):
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 0, 0)), 0.27)    # before 00:30
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 0, 30)), 0.12)
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 5, 30)), 0.27)   # off-peak has ended
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 16, 0)), 0.36)
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 19, 0)), 0.27)

    def test_bands_follow_london_clock_in_summer(self):
        # In July 15:30 UTC is 16:30 BST, i.e. already in the evening peak
        self.assertEqual(self.tariff.import_price(datetime(2013, 7, 15, 15, 30)), 0.36)
        self.assertEqual(self.tariff.import_price(datetime(2013, 1, 15, 15, 30)), 0.27)
        # and 23:30 UTC in July is 00:30 BST: off-peak has just begun
        self.assertEqual(self.tariff.import_price(datetime(2013, 7, 15, 23, 30)), 0.12)

    def test_export_is_far_below_import(self):
        for hour in range(24):
            ts = datetime(2013, 4, 10, hour)
            self.assertLess(self.tariff.export_price_at(ts), self.tariff.import_price(ts))

    def test_flat_tariff(self):
        flat = Tariff.flat(0.25, 0.05)
        for hour in range(24):
            self.assertEqual(flat.import_price(datetime(2013, 1, 1, hour)), 0.25)
            self.assertEqual(flat.export_price_at(datetime(2013, 1, 1, hour)), 0.05)


class TestP2PPrice(unittest.TestCase):
    def setUp(self):
        self.tariff = Tariff()
        self.ts = datetime(2013, 1, 15, 12, 0)  # standard: import 0.27, export 0.08
        self.low, self.high = 0.08, 0.27

    def test_balanced_market_is_mid_market(self):
        self.assertAlmostEqual(self.tariff.p2p_price(1.0, 1.0, self.ts), (self.low + self.high) / 2)

    def test_plentiful_supply_pushes_price_towards_export(self):
        self.assertLess(self.tariff.p2p_price(100.0, 1.0, self.ts), self.low + 0.01)

    def test_scarce_supply_pushes_price_towards_import(self):
        self.assertGreater(self.tariff.p2p_price(1.0, 100.0, self.ts), self.high - 0.01)

    def test_price_rises_as_supply_gets_scarcer(self):
        prices = [self.tariff.p2p_price(supply, 1.0, self.ts) for supply in (10, 4, 2, 1, 0.5, 0.25, 0.1)]
        self.assertEqual(prices, sorted(prices))
        self.assertLess(prices[0], prices[-1])

    def test_always_between_export_and_import(self):
        for supply in (0.001, 0.1, 1, 10, 1000):
            for demand in (0.001, 0.1, 1, 10, 1000):
                for hour in (3, 12, 17):
                    ts = datetime(2013, 1, 15, hour)
                    price = self.tariff.p2p_price(supply, demand, ts)
                    self.assertGreaterEqual(price, self.tariff.export_price_at(ts) - 1e-12)
                    self.assertLessEqual(price, self.tariff.import_price(ts) + 1e-12)

    def test_no_supply_or_demand_falls_back_to_mid_market(self):
        mid = (self.low + self.high) / 2
        self.assertAlmostEqual(self.tariff.p2p_price(0, 1, self.ts), mid)
        self.assertAlmostEqual(self.tariff.p2p_price(1, 0, self.ts), mid)

    def test_mid_market_mode_ignores_scarcity(self):
        t = Tariff(p2p_pricing="mid_market")
        self.assertAlmostEqual(t.p2p_price(100, 1, self.ts), t.p2p_price(1, 100, self.ts))

    def test_peak_widens_the_band(self):
        peak, standard = datetime(2013, 1, 15, 17, 0), datetime(2013, 1, 15, 12, 0)
        self.assertGreater(self.tariff.p2p_price(1, 1, peak), self.tariff.p2p_price(1, 1, standard))


class TestValidationAndParsing(unittest.TestCase):
    def test_from_dict(self):
        t = Tariff.from_dict({
            "export_price": 0.15, "timezone": "UTC", "p2p_pricing": "mid_market",
            "import_bands": [{"name": "day", "start": "00:00", "end": "24:00", "price": 0.30}],
        })
        self.assertEqual(t.import_price(datetime(2013, 1, 1, 12)), 0.30)
        self.assertEqual(t.export_price, 0.15)

    def test_none_gives_defaults(self):
        self.assertEqual(Tariff.from_dict(None).export_price, 0.08)

    def test_rejects_gaps_in_the_day(self):
        with self.assertRaisesRegex(ValueError, "without a price"):
            Tariff(import_bands=[ImportBand("a", 0, 600, 0.2)])

    def test_rejects_import_below_export(self):
        with self.assertRaisesRegex(ValueError, "below"):
            Tariff(export_price=0.30)

    def test_rejects_unknown_mode_setting_and_bad_times(self):
        with self.assertRaises(ValueError):
            Tariff(p2p_pricing="auction")
        with self.assertRaisesRegex(ValueError, "Unknown tariff settings"):
            Tariff.from_dict({"export": 0.1})
        with self.assertRaises(ValueError):
            Tariff.from_dict({"import_bands": [{"start": "25:00", "end": "26:00", "price": 0.3}]})

    def test_works_with_pandas_timestamps(self):
        import pandas as pd
        self.assertEqual(Tariff().import_price(pd.Timestamp("2013-01-15 17:30:00")), 0.36)


if __name__ == "__main__":
    unittest.main()
