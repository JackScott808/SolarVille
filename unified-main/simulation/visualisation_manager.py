# Branch: unified-main
# File: visualisation_manager.py

"""Visualisation manager for SolarVille: live plot window plus saved results."""

import csv
import logging
import multiprocessing
import os
import queue as queue_module
import sys
from typing import Dict, List, Optional

from core.energy_types import EnergyReading, ProsumerReading
from simulation.plotting import ROW_KEYS, EnergyFigure


def display_available() -> bool:
    """True if a GUI window can plausibly be opened on this machine."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _live_plot_process(q, title: str, timescale: str, is_prosumer: bool, theme: str) -> None:
    """Entry point of the plot window process (module level so it can be spawned).

    A separate process because GUI toolkits (macOS especially) must own their
    main thread, which the simulation loop is already using.
    """
    import matplotlib.pyplot as plt

    fig = EnergyFigure(title, timescale, is_prosumer, theme)
    plt.ion()
    plt.show(block=False)
    rows: List[Dict] = []
    finished = False
    while plt.fignum_exists(fig.fig.number):
        changed = False
        try:
            while True:
                item = q.get_nowait()
                if item is None:
                    finished = True
                    break
                rows.append(item)
                changed = True
        except queue_module.Empty:
            pass
        if changed:
            fig.draw(rows)
            fig.fig.canvas.draw_idle()
        if finished:
            break
        plt.pause(0.1)
    if finished and plt.fignum_exists(fig.fig.number):
        plt.ioff()
        plt.show()  # keep the final plot open until the user closes it


class VisualisationManager:
    """Collects a reading per interval, shows them live and saves the result.

    - ``update()`` records a row (and feeds the live window if one is open).
    - ``stop()`` always writes ``<output_dir>/<device>_<start>_<scale>.png`` and ``.csv``,
      so a headless run (a Pi over SSH, CI, no display) still produces the plot.
    """

    def __init__(self, start_date: str, timescale: str, device_name: str = "device",
                 is_prosumer: bool = False, output_dir: str = "output",
                 live: bool = True, theme: str = "light"):
        """
        Args:
            start_date: Start date of the simulation (used in titles/filenames)
            timescale: d/w/m/y
            device_name: Name of this device (e.g. pi1)
            is_prosumer: Prosumers also plot generation and storage
            output_dir: Where the PNG/CSV are written
            live: Open a live window if a display is available
            theme: 'light' or 'dark'
        """
        self.logger = logging.getLogger(__name__)
        self.start_date = str(start_date)[:10]
        self.timescale = timescale
        self.device_name = device_name
        self.is_prosumer = is_prosumer
        self.output_dir = output_dir
        self.theme = theme
        self.want_live = live
        self.rows: List[Dict] = []
        self._queue = None
        self._process: Optional[multiprocessing.Process] = None
        self.logger.debug("VisualisationManager initialized")

    @property
    def title(self) -> str:
        role = "prosumer" if self.is_prosumer else "consumer"
        return f"{self.device_name} ({role}) - {self.start_date}"

    def start(self, data=None) -> None:
        """Open the live window if enabled and a display exists."""
        if not self.want_live:
            return
        if not display_available():
            self.logger.info("No display available; the plot will be saved to a file instead of shown live")
            return
        ctx = multiprocessing.get_context("spawn")
        self._queue = ctx.Queue()
        self._process = ctx.Process(
            target=_live_plot_process,
            args=(self._queue, self.title, self.timescale, self.is_prosumer, self.theme),
            daemon=True,
        )
        self._process.start()
        self.logger.info("Live plot started")

    def update(self, reading: EnergyReading, **trade) -> None:
        """Record one interval.

        Args:
            reading: The interval's EnergyReading/ProsumerReading
            **trade: Optional settle_interval() summary (currency, p2p_*, grid_*)
        """
        row = {
            "timestamp": reading.timestamp,
            "demand": reading.demand,
            "generation": getattr(reading, "generation", 0.0),
            "balance": reading.balance,
            "storage_level": getattr(reading, "storage_level", 0.0),
            "currency": trade.get("currency", 0.0),
            "p2p_sold": trade.get("p2p_sold", 0.0),
            "p2p_bought": trade.get("p2p_bought", 0.0),
            "grid_sold": trade.get("grid_sold", 0.0),
            "grid_bought": trade.get("grid_bought", 0.0),
            "import_price": trade.get("import_price"),
            "export_price": trade.get("export_price"),
            "p2p_price": trade.get("p2p_price"),
        }
        self.rows.append(row)
        if self._queue is not None and self._process is not None and self._process.is_alive():
            self._queue.put(row)

    def is_live(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def wait_until_closed(self) -> None:
        """Tell the live window the run is over and block until the user closes it."""
        if self._process is None:
            return
        if self._process.is_alive():
            self._queue.put(None)
            self.logger.info("Simulation finished - close the plot window to exit")
            self._process.join()

    def save(self) -> Optional[str]:
        """Write the PNG and CSV for everything recorded. Returns the PNG path."""
        if not self.rows:
            return None
        os.makedirs(self.output_dir, exist_ok=True)
        stem = os.path.join(self.output_dir, f"{self.device_name}_{self.start_date}_{self.timescale}")

        with open(stem + ".csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ROW_KEYS)
            writer.writeheader()
            writer.writerows(self.rows)

        import matplotlib
        previous = matplotlib.get_backend()
        matplotlib.use("Agg", force=True)  # saving never needs a window
        try:
            fig = EnergyFigure(self.title, self.timescale, self.is_prosumer, self.theme)
            fig.draw(self.rows)
            fig.save(stem + ".png")
            fig.close()
        finally:
            try:
                matplotlib.use(previous, force=True)
            except Exception:
                pass
        self.logger.info(f"Saved plot and data to {stem}.png / .csv")
        return stem + ".png"

    def stop(self) -> None:
        """Save results and close the live window (if still open)."""
        try:
            self.save()
        except Exception as e:
            self.logger.error(f"Could not save plot: {e}")
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._process = None

    def cleanup(self) -> None:
        self.stop()
