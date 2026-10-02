# Branch: unified-main
# File: solar_manager.py

"""Solar panel monitoring for SolarVille system."""

import logging
from datetime import datetime
from typing import Dict, Optional
import random

class SolarMonitor:
    """Monitors solar panel voltage and current using INA219 sensor."""

    NOMINAL_VOLTAGE = 36.0  # V, a typical string of residential panels

    def __init__(self, mock_mode: bool = True, solar_source=None, interval_seconds: float = 1800):
        """
        Initialize solar monitor.

        Args:
            mock_mode: If True, generate mock solar readings
            solar_source: simulation.solar_model source (SolarSource/SolarModel). If given, mock
                readings follow real London sun for the simulated time instead of random numbers.
            interval_seconds: Length of one simulation interval
        """
        self.mock_mode = mock_mode
        self.solar_source = solar_source
        self.interval_seconds = interval_seconds
        self.logger = logging.getLogger(__name__)
        self._initialize_sensors()

    def _initialize_sensors(self):
        """Initialize INA219 sensor or mock."""
        if self.mock_mode:
            self.logger.info("Solar monitor initialized in mock mode")
            self.sensor = None
        else:
            # TODO: Initialize real INA219 sensor
            # import board
            # import busio
            # from adafruit_ina219 import INA219
            # i2c = busio.I2C(board.SCL, board.SDA)
            # self.sensor = INA219(i2c)
            self.logger.info("Solar sensor hardware initialization not implemented")
            self.sensor = None

    def get_readings(self, timestamp: Optional[datetime] = None) -> Dict[str, float]:
        """
        Get current solar readings.

        Args:
            timestamp: Simulated time of the interval start. In mock mode with a solar source,
                this decides the output (time of day and year). Ignored for real hardware.

        Returns:
            Dictionary with solar_power (W), solar_voltage (V), solar_current (mA),
            and solar_energy (kWh for 30min interval)
        """
        if self.mock_mode and self.solar_source is not None and timestamp is not None:
            energy = self.solar_source.energy_kwh(timestamp, self.interval_seconds)  # kWh
            power = energy / (self.interval_seconds / 3600) * 1000  # average W over the interval
            voltage = self.NOMINAL_VOLTAGE if power > 0 else 0.0
            return {
                'solar_power': power,
                'solar_voltage': voltage,
                'solar_current': power / voltage * 1000 if voltage else 0.0,  # mA
                'solar_energy': energy
            }

        if self.mock_mode:
            # Mock solar readings - simulate varying solar generation
            solar_power = random.uniform(0.2, 1.5)  # 0.2-1.5W
            solar_voltage = random.uniform(4.5, 5.5)  # 4.5-5.5V
            solar_current = solar_power / solar_voltage * 1000  # mA

            # Convert power to energy over 30 min interval
            # Power (W) * Time (hours) = Energy (Wh)
            # 30 minutes = 0.5 hours
            solar_energy = (solar_power * 0.5) / 1000  # kWh

            return {
                'solar_power': solar_power,
                'solar_voltage': solar_voltage,
                'solar_current': solar_current,
                'solar_energy': solar_energy
            }
        else:
            if self.sensor:
                power = self.sensor.power / 1000  # Convert mW to W
                voltage = self.sensor.bus_voltage + self.sensor.shunt_voltage
                current = self.sensor.current
                energy = (power * 0.5) / 1000  # kWh for 30 min

                return {
                    'solar_power': power,
                    'solar_voltage': voltage,
                    'solar_current': current,
                    'solar_energy': energy
                }
            else:
                return {
                    'solar_power': 0,
                    'solar_voltage': 0,
                    'solar_current': 0,
                    'solar_energy': 0
                }

    def cleanup(self):
        """Cleanup sensor resources."""
        pass  # INA219 doesn't need explicit cleanup