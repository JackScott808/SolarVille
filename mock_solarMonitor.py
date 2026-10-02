# Mock solar monitor for testing on non-Raspberry Pi platforms
# Same interface as solarMonitor.py
import math
import time


def get_current_readings():
    # Smooth fake sun curve, peaking at ~120 mW
    power_mw = max(0.0, 120 * math.sin(time.time() / 30))
    current_ma = power_mw / 5.0
    return {
        'solar_current_ma': current_ma,
        'solar_current_a': current_ma / 1000,
        'solar_power_mw': power_mw,
        'solar_power_kwh': power_mw / 1000 / 2,
        'battery_voltage': 3.7,
        'battery_current_ma': 0.0,
        'battery_current_a': 0.0,
    }
