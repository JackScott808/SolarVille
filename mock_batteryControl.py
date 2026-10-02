# Mock battery module for testing on non-Raspberry Pi platforms
# Same interface as batteryControl.py
import logging


def update_battery_charge(solar_current, solar_power, demand):
    # Mock update logic: returns (state of charge 0-1, charging efficiency)
    soc = min(1.0, max(0.0, 0.5 + (solar_power - demand) / 100.0))
    logging.info(f"Mock updated battery charge: {soc * 100:.2f}%")
    return soc, 1.0


def read_battery_charge():
    # Mock read logic
    return 50.0  # Return a constant mock value for demonstration
