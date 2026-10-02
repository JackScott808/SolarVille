# Branch: unified-main
# File: constants.py

# Network Constants
# Every device's server listens on this port (override with `port:` in network_topology.yml or --port).
# Not 5000: on macOS the AirPlay Receiver service holds 5000 on all interfaces.
DEFAULT_PORT = 5050
RETRY_ATTEMPTS = 3
TIMEOUT_SECONDS = 5

# Energy Constants
# Flat fallback prices, used only where no tariff is configured. Real prices come from the
# time-of-use `tariff:` section of config/simulation.yml (see core/tariff.py).
GRID_BUY_PRICE = 0.25  # £/kWh
GRID_SELL_PRICE = 0.05  # £/kWh

# Hardware Constants
LCD_ROWS = 2
LCD_COLS = 16
