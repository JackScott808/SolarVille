# Prosumer
# mock_solarMonitor.py
import math
import time
from datetime import datetime
import random

class MockSolarData:
    def __init__(self):
        self.start_time = time.time()
        
    def _simulate_solar_output(self):
        """Simulate solar panel output based on time of day"""
        # Get current time and convert to hours
        current_time = datetime.now()
        hour = current_time.hour + current_time.minute / 60.0
        
        # Simulate a bell curve for solar output centered at noon
        if 6 <= hour <= 18:  # Daylight hours
            # Create a bell curve from 6am to 6pm
            peak = 1.0  # Peak output at noon
            spread = 6.0  # Spread of the curve
            x = (hour - 12.0) / spread  # Center at noon
            output = peak * math.exp(-x * x)  # Bell curve formula
        else:
            output = 0.0  # No output at night
            
        # Add some random variation (±10%)
        variation = (1.0 + (random.random() - 0.5) * 0.2)
        return output * variation

def get_current_readings():
    """Mock implementation of get_current_readings"""
    solar_data = MockSolarData()
    solar_output = solar_data._simulate_solar_output()
    
    # Simulate realistic values
    return {
        'solar_current': solar_output * 0.5,  # Simulate current in Amperes
        'solar_power': solar_output * 5.0,    # Simulate power in Watts
        'battery_voltage': 3.7,               # Nominal battery voltage
        'battery_current': solar_output * 0.3  # Simulate battery current in Amperes
    }

def read_ina219(sensor):
    """Mock implementation of read_ina219"""
    solar_data = MockSolarData()
    output = solar_data._simulate_solar_output()
    
    bus_voltage = 5.0  # Simulate 5V bus
    shunt_voltage = 0.01 * output  # Small voltage drop across shunt
    current = output * 0.5  # Current in A
    power = bus_voltage * current * 1000  # Power in mW
    
    return bus_voltage, shunt_voltage, current, power

def print_readings(bus_voltage, shunt_voltage, current, power, label):
    """Mock implementation of print_readings"""
    print(f"{label} Bus Voltage:    {bus_voltage:.3f} V")
    print(f"{label} Shunt Voltage:  {shunt_voltage:.6f} V")
    print(f"{label} Total Voltage:  {bus_voltage + shunt_voltage:.3f} V")
    print(f"{label} Current:        {current*1000:.3f} mA")
    print(f"{label} Power:          {power:.3f} mW")
    print("------------------------")