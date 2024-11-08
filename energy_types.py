# Branch: consumerJack
# File: energy_types.py

from dataclasses import dataclass
from datetime import datetime

@dataclass
class EnergyReading:
    """Base class for energy readings - used by consumer"""
    timestamp: datetime
    demand: float      # kWh - energy demanded in this timestep
    balance: float     # kWh - net energy balance (always negative for consumer)

@dataclass
class TradeData:
    """Class for storing trade information"""
    amount: float         # kWh - amount of energy to trade
    price: float         # £/kWh - price per unit
    grid_buy_price: float = 0.25   # Default grid buying price
    grid_sell_price: float = 0.05  # Default grid selling price