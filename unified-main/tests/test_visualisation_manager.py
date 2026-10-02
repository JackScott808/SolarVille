# Branch: unified-main
# File: tests/test_visualisation_manager.py
"""Tests for plot recording/saving and the live-window loop (run headless with Agg)."""

import csv
import os
import queue
import tempfile
import unittest
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")

from core.energy_types import EnergyReading, ProsumerReading
from simulation.plotting import ROW_KEYS
from simulation.visualisation_manager import VisualisationManager, _live_plot_process, display_available


def readings(prosumer: bool, n: int = 6):
    start = datetime(2012, 10, 24)
    for i in range(n):
        ts = start + timedelta(minutes=30 * i)
        if prosumer:
            yield ProsumerReading(ts, demand=0.2, balance=0.1 * i - 0.2, generation=0.3,
                                  storage_level=50 + i, storage_power=0.0, solar_power=1.0)
        else:
            yield EnergyReading(ts, demand=0.2, balance=-0.2)


class TestVisualisationManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, prosumer: bool) -> VisualisationManager:
        return VisualisationManager("2012-10-24", "d", "pi1" if prosumer else "pi2",
                                    is_prosumer=prosumer, output_dir=self.tmp.name, live=False)

    def test_update_records_rows_with_trade_summary(self):
        vis = self.make(True)
        r = next(readings(True))
        vis.update(r, currency=101.5, p2p_sold=0.2, grid_sold=0.1)
        row = vis.rows[0]
        self.assertEqual(set(row), set(ROW_KEYS))
        self.assertEqual(row["currency"], 101.5)
        self.assertEqual(row["generation"], 0.3)
        self.assertEqual(row["p2p_bought"], 0.0)  # missing trade keys default to 0

    def test_consumer_rows_have_no_generation_or_storage(self):
        vis = self.make(False)
        vis.update(next(readings(False)))
        self.assertEqual(vis.rows[0]["generation"], 0.0)
        self.assertEqual(vis.rows[0]["storage_level"], 0.0)

    def test_save_writes_png_and_csv(self):
        for prosumer in (True, False):
            vis = self.make(prosumer)
            for r in readings(prosumer):
                vis.update(r, currency=100.0)
            png = vis.save()
            self.assertTrue(os.path.getsize(png) > 5000)
            with open(png[:-4] + ".csv") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 6)

    def test_save_with_nothing_recorded_does_nothing(self):
        self.assertIsNone(self.make(True).save())
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_single_reading_does_not_crash(self):
        vis = self.make(True)
        vis.update(next(readings(True)), currency=100.0)
        self.assertIsNotNone(vis.save())

    def test_live_disabled_never_starts_a_process(self):
        vis = self.make(True)
        vis.start()
        self.assertFalse(vis.is_live())
        vis.stop()  # saves, and is safe with no process

    def test_live_loop_draws_and_exits_on_sentinel(self):
        q = queue.Queue()
        vis = self.make(True)
        for r in readings(True):
            vis.update(r, currency=100.0)
            q.put(vis.rows[-1])
        q.put(None)
        # Agg's plt.show() is a no-op, so this returns once it sees the sentinel
        _live_plot_process(q, vis.title, "d", True, "light")

    def test_display_available_follows_environment_on_linux(self):
        import sys
        if sys.platform.startswith("linux"):
            saved = {k: os.environ.pop(k, None) for k in ("DISPLAY", "WAYLAND_DISPLAY")}
            try:
                self.assertFalse(display_available())
                os.environ["DISPLAY"] = ":0"
                self.assertTrue(display_available())
            finally:
                os.environ.pop("DISPLAY", None)
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
