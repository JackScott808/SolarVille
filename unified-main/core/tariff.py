# Branch: unified-main
# File: tariff.py

"""Electricity prices: a time-of-use grid tariff and peer-to-peer price formation.

Replaces the old flat constants (buy 25p / sell 5p). The default values are *representative* of
UK domestic time-of-use import tariffs and the Smart Export Guarantee (export rates vary widely by
supplier, roughly 1-15p/kWh); they are not a specific supplier's rates. Edit ``tariff:`` in
``config/simulation.yml`` to model another one.

What makes it realistic:
- **Import price depends on the time of day** (cheap at night, expensive at the 16:00-19:00 evening
  peak), in local London time, so it correctly shifts by an hour in British Summer Time.
- **Export is paid far less than import costs**, which is the whole reason to trade locally.
- **Peer-to-peer prices live between the two**: a seller will not accept less than the export rate
  (they could sell to the grid) and a buyer will not pay more than the import price. Where in that
  band the price lands depends on scarcity: plentiful local supply pushes it towards the export
  rate, scarce supply towards the import price, and a balanced market sits at the mid-market rate.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

logger = logging.getLogger(__name__)

P2P_PRICING_MODES = ("supply_demand", "mid_market")
MINUTES_PER_DAY = 24 * 60


def _parse_clock(text) -> int:
    """'HH:MM' (or '24:00') -> minutes since midnight."""
    try:
        hours, minutes = str(text).split(":")
        value = int(hours) * 60 + int(minutes)
    except ValueError:
        raise ValueError(f"Invalid time '{text}', expected HH:MM")
    if not 0 <= value <= MINUTES_PER_DAY:
        raise ValueError(f"Time '{text}' is outside 00:00-24:00")
    return value


@dataclass
class ImportBand:
    """A grid import price that applies from ``start`` up to (not including) ``end``, local time."""
    name: str
    start: int   # minutes since local midnight
    end: int
    price: float  # £/kWh

    @classmethod
    def from_dict(cls, data: Dict) -> "ImportBand":
        return cls(name=str(data.get("name", "band")), start=_parse_clock(data["start"]),
                   end=_parse_clock(data["end"]), price=float(data["price"]))


def _default_bands() -> List[ImportBand]:
    return [
        ImportBand("off-peak", _parse_clock("00:30"), _parse_clock("05:30"), 0.12),
        ImportBand("peak", _parse_clock("16:00"), _parse_clock("19:00"), 0.36),
        ImportBand("standard", _parse_clock("00:00"), _parse_clock("24:00"), 0.27),
    ]


@dataclass
class Tariff:
    """Grid prices and the rule for peer-to-peer prices."""
    export_price: float = 0.08                        # £/kWh paid for exports (flat, Smart Export Guarantee)
    import_bands: List[ImportBand] = field(default_factory=_default_bands)  # first match wins
    timezone: str = "Europe/London"                   # the bands are in this local time
    p2p_pricing: str = "supply_demand"

    def __post_init__(self):
        self._tz = None
        self._warned_tz = False
        self.validate()

    # ---- construction ---------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "Tariff":
        data = dict(data or {})
        bands = data.pop("import_bands", None)
        kwargs = {k: data[k] for k in ("export_price", "timezone", "p2p_pricing") if k in data}
        unknown = set(data) - set(kwargs)
        if unknown:
            raise ValueError(f"Unknown tariff settings: {sorted(unknown)}")
        if bands is not None:
            kwargs["import_bands"] = [ImportBand.from_dict(b) for b in bands]
        return cls(**kwargs)

    @classmethod
    def flat(cls, import_price: float, export_price: float) -> "Tariff":
        """A tariff with one all-day import price (used when no tariff is configured)."""
        return cls(export_price=export_price,
                   import_bands=[ImportBand("flat", 0, MINUTES_PER_DAY, import_price)])

    def validate(self) -> None:
        if self.p2p_pricing not in P2P_PRICING_MODES:
            raise ValueError(f"p2p_pricing must be one of {P2P_PRICING_MODES}, got '{self.p2p_pricing}'")
        if self.export_price < 0:
            raise ValueError("export_price cannot be negative")
        if not self.import_bands:
            raise ValueError("at least one import band is required")
        for band in self.import_bands:
            if band.start >= band.end:
                raise ValueError(f"Band '{band.name}' must end after it starts")
            if band.price < self.export_price:
                raise ValueError(f"Band '{band.name}' imports at £{band.price}, below the £{self.export_price} "
                                 "export price; peer-to-peer trading would make no sense")
        for minute in range(MINUTES_PER_DAY):
            if self._band_at(minute) is None:
                raise ValueError(f"Import bands leave {minute // 60:02d}:{minute % 60:02d} without a price; "
                                 "add a band covering 00:00-24:00 as the last entry")

    # ---- time -----------------------------------------------------------------------------

    def local_minute(self, ts: datetime) -> int:
        """Minutes since local midnight for a UTC timestamp."""
        plain = datetime(ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second, tzinfo=timezone.utc)
        if ZoneInfo is not None:
            if self._tz is None:
                try:
                    self._tz = ZoneInfo(self.timezone)
                except (ZoneInfoNotFoundError, KeyError):
                    if not self._warned_tz:
                        logger.warning(f"Timezone '{self.timezone}' unavailable (install the 'tzdata' package); "
                                       "treating timestamps as local time")
                        self._warned_tz = True
                    self._tz = timezone.utc
            plain = plain.astimezone(self._tz)
        return plain.hour * 60 + plain.minute

    def _band_at(self, minute: int) -> Optional[ImportBand]:
        for band in self.import_bands:
            if band.start <= minute < band.end:
                return band
        return None

    # ---- prices ---------------------------------------------------------------------------

    def import_band(self, ts: datetime) -> ImportBand:
        return self._band_at(self.local_minute(ts))

    def import_price(self, ts: datetime) -> float:
        """What buying from the grid costs at this time (£/kWh)."""
        return self.import_band(ts).price

    def export_price_at(self, ts: datetime) -> float:
        """What the grid pays for exports at this time (£/kWh). Flat today; takes ``ts`` so a
        time-varying export tariff can be added without touching callers."""
        return self.export_price

    def p2p_price(self, supply: float, demand: float, ts: datetime) -> float:
        """Price for energy traded locally, always between the export and import price.

        ``supply_demand``: ``export + (import - export) * r / (1 + r)`` with ``r = demand / supply``.
        Plentiful supply (r -> 0) gives the export price, scarce supply (r -> infinity) the import
        price, and a balanced market (r = 1) the mid-market rate.
        ``mid_market``: always halfway between the two.
        """
        low, high = self.export_price_at(ts), self.import_price(ts)
        if self.p2p_pricing == "mid_market" or supply <= 0 or demand <= 0:
            return (low + high) / 2
        ratio = demand / supply
        return low + (high - low) * ratio / (1 + ratio)
