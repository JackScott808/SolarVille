# Branch: unified-main
# File: plotting.py

"""Figure construction and drawing for the SolarVille energy plots.

Pure matplotlib, no multiprocessing or I/O, so the same code draws the live
window and the saved PNG. Colours follow the entity, not the panel: demand is
always blue, generation orange, storage aqua, money violet. A surplus (generation
exceeding demand) is shaded orange and a deficit blue, so the balance panel reads
in the same colours as the lines it is derived from.
"""

from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.dates as mdates
from matplotlib.ticker import FormatStrFormatter

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e4e3df",
        "demand": "#2a78d6", "generation": "#eb6834", "storage": "#1baf7a", "money": "#4a3aa7",
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "grid": "#33332f",
        "demand": "#3987e5", "generation": "#d95926", "storage": "#199e70", "money": "#9085e9",
    },
}

# Row keys the plot reads; VisualisationManager guarantees all are present.
ROW_KEYS = ("timestamp", "demand", "generation", "balance", "storage_level", "currency",
            "p2p_sold", "p2p_bought", "grid_sold", "grid_bought")


def _x_axis(ax, timescale: str) -> None:
    """Tick spacing and format appropriate to the simulated span."""
    if timescale == "d":
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif timescale == "w":
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
    elif timescale == "m":
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))


class EnergyFigure:
    """A stacked set of panels sharing one time axis.

    Prosumers get four panels (demand vs generation, storage, balance, money);
    consumers three (no generation or storage).
    """

    def __init__(self, title: str, timescale: str, is_prosumer: bool, theme: str = "light"):
        import matplotlib.pyplot as plt  # imported late so callers can pick a backend first

        self.c = THEMES[theme]
        self.is_prosumer = is_prosumer
        self.timescale = timescale
        self.title = title

        names = ["energy", "storage", "balance", "money"] if is_prosumer else ["energy", "balance", "money"]
        heights = {"energy": 3, "storage": 1.6, "balance": 2, "money": 1.8}
        self.fig, axes = plt.subplots(
            len(names), 1, sharex=True, figsize=(11, 2.3 * len(names) + 1.2),
            gridspec_kw={"height_ratios": [heights[n] for n in names], "hspace": 0.42},
        )
        self.axes: Dict[str, object] = dict(zip(names, list(axes) if len(names) > 1 else [axes]))
        self.fig.patch.set_facecolor(self.c["surface"])
        self.fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="bold", color=self.c["ink"])
        for ax in self.axes.values():
            self._style(ax)
        _x_axis(self.axes[names[-1]], timescale)
        self.axes[names[-1]].set_xlabel("Simulated time", color=self.c["ink2"])
        self.fig.subplots_adjust(left=0.08, right=0.86, top=0.92, bottom=0.09)

    def _style(self, ax) -> None:
        c = self.c
        ax.set_facecolor(c["surface"])
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(c["grid"])
        ax.grid(True, axis="y", color=c["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=c["ink2"], labelsize=9, length=0)

    def draw(self, rows: Sequence[Dict]) -> None:
        """Redraw every panel from the full history (cheap at this data size)."""
        if not rows:
            return
        c = self.c
        for ax in self.axes.values():
            ax.cla()
            self._style(ax)
        t = [r["timestamp"] for r in rows]
        demand = [r["demand"] for r in rows]
        balance = [r["balance"] for r in rows]

        # --- Demand (and generation) ---------------------------------------------------
        ax = self.axes["energy"]
        ax.plot(t, demand, color=c["demand"], linewidth=2, label="Demand", solid_capstyle="round")
        self._end_label(ax, t[-1], demand[-1], "Demand", c["demand"])
        if self.is_prosumer:
            gen = [r["generation"] for r in rows]
            ax.plot(t, gen, color=c["generation"], linewidth=2, label="Generation", solid_capstyle="round")
            self._end_label(ax, t[-1], gen[-1], "Generation", c["generation"])
            self._legend(ax)
        ax.set_ylabel("kWh per interval", color=c["ink2"], fontsize=9)
        ax.set_ylim(bottom=0)

        # --- Storage -------------------------------------------------------------------
        if self.is_prosumer:
            ax = self.axes["storage"]
            soc = [r["storage_level"] for r in rows]
            ax.plot(t, soc, color=c["storage"], linewidth=2, solid_capstyle="round")
            ax.set_ylim(0, 105)
            ax.set_ylabel("Storage %", color=c["ink2"], fontsize=9)
            self._end_label(ax, t[-1], soc[-1], f"{soc[-1]:.0f}%", c["storage"])

        # --- Balance: surplus above zero (generation colour), deficit below (demand colour)
        ax = self.axes["balance"]
        ax.axhline(0, color=c["ink2"], linewidth=1)
        width = self._bar_width(t)
        if self.is_prosumer:  # a consumer can never have a surplus
            ax.bar(t, [max(b, 0) for b in balance], width=width, color=c["generation"], label="Surplus")
        ax.bar(t, [min(b, 0) for b in balance], width=width, color=c["demand"], label="Deficit")
        ax.set_ylabel("Balance kWh", color=c["ink2"], fontsize=9)
        if self.is_prosumer:  # one series needs no legend; the panel title carries it
            self._legend(ax)

        # --- Money ---------------------------------------------------------------------
        ax = self.axes["money"]
        money = [r["currency"] for r in rows]
        ax.plot(t, money, color=c["money"], linewidth=2, solid_capstyle="round")
        ax.set_ylabel("Balance £", color=c["ink2"], fontsize=9)
        self._end_label(ax, t[-1], money[-1], f"£{money[-1]:.2f}", c["money"])
        lo, hi = min(money), max(money)
        pad = max((hi - lo) * 0.2, 0.05)
        ax.set_ylim(lo - pad, hi + pad)
        ax.ticklabel_format(axis="y", useOffset=False)  # plain £ values, not matplotlib's "+1e2" offset
        ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))

        last = self.axes[list(self.axes)[-1]]
        _x_axis(last, self.timescale)
        step = (t[1] - t[0]) if len(t) > 1 else None
        if step is not None and step.total_seconds() > 0:
            last.set_xlim(t[0] - step / 2, t[-1] + step / 2)  # shared, so applies to every panel
        for a in self.axes.values():
            a.tick_params(colors=c["ink2"], labelsize=9, length=0)
        for label in self.axes[list(self.axes)[-1]].get_xticklabels():
            label.set_rotation(0)
        self.axes[list(self.axes)[-1]].set_xlabel("Simulated time", color=c["ink2"], fontsize=9)

    def _legend(self, ax) -> None:
        """Legend above the panel, outside the plot area so it never covers data."""
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9,
                  labelcolor=self.c["ink2"], ncol=2, borderaxespad=0.2, handlelength=1.6)

    def _end_label(self, ax, x, y, text: str, color: str) -> None:
        """Direct label just past the last point; text stays in ink, a dot carries the colour."""
        ax.plot([x], [y], marker="o", markersize=7, color=color, markeredgecolor=self.c["surface"],
                markeredgewidth=2, linestyle="none", clip_on=False)
        ax.annotate(text, (x, y), xytext=(8, 0), textcoords="offset points", va="center",
                    fontsize=9, color=self.c["ink"], annotation_clip=False)

    @staticmethod
    def _bar_width(t: Sequence) -> float:
        """~80% of the spacing between readings, in matplotlib date units (days)."""
        if len(t) > 1:
            span = mdates.date2num(t[1]) - mdates.date2num(t[0])
            if span > 0:
                return span * 0.8
        return 1 / 48 * 0.8  # a 30 minute interval

    def save(self, path: str) -> None:
        self.fig.savefig(path, dpi=130, facecolor=self.fig.get_facecolor())

    def close(self) -> None:
        import matplotlib.pyplot as plt
        plt.close(self.fig)
