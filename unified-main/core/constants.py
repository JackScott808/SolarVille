# Branch: unified-main
# File: constants.py

# Network Constants
DEFAULT_PORT = 5000
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
