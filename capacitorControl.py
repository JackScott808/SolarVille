# Prosumer
MOCK_HARDWARE = True

import time
import math
import logging

if not MOCK_HARDWARE:
    import board
    import busio
    from adafruit_ina219 import INA219

logging.basicConfig(level=logging.INFO)

# Capacitor specifications
CAPACITANCE = 1.5  # Farads
MAX_VOLTAGE = 5.5  # V
MIN_VOLTAGE = 0.5  # V
INTERNAL_RESISTANCE = 100  # ohms

class MockCapacitor:
    def __init__(self):
        self.voltage = 2.75  # Starting at half max voltage
        self.last_update = time.time()
        
    def calculate_energy(self):
        """Calculate stored energy in Joules"""
        return 0.5 * CAPACITANCE * (self.voltage ** 2)
    
    def get_state_of_charge(self):
        """Calculate state of charge as a percentage"""
        current_energy = self.calculate_energy()
        max_energy = 0.5 * CAPACITANCE * (MAX_VOLTAGE ** 2)
        min_energy = 0.5 * CAPACITANCE * (MIN_VOLTAGE ** 2)
        usable_energy = max_energy - min_energy
        current_usable_energy = current_energy - min_energy
        return max(0.0, min(1.0, current_usable_energy / usable_energy))

    def update_voltage(self, power, duration):
        """Update capacitor voltage based on power flow"""
        energy_change = power * duration
        current_energy = self.calculate_energy()
        new_energy = current_energy + energy_change
        
        try:
            new_voltage = math.sqrt((2 * new_energy) / CAPACITANCE)
        except ValueError:
            new_voltage = MIN_VOLTAGE
            
        self.voltage = max(MIN_VOLTAGE, min(MAX_VOLTAGE, new_voltage))
        return self.voltage

def update_capacitor_charge(solar_power, demand_power, time_step=1.0):
    """Mock update function"""
    net_power = solar_power - demand_power
    capacitor = MockCapacitor()
    capacitor.update_voltage(net_power, time_step)
    soc = capacitor.get_state_of_charge()
    efficiency = 0.95 if net_power > 0 else 0.90
    
    logging.info(f"Mock Capacitor - Voltage: {capacitor.voltage:.2f}V, "
                 f"SoC: {soc*100:.2f}%, Power flow: {net_power:.2f}W")
    
    return soc, efficiency

def read_capacitor_charge():
    """Mock read function"""
    capacitor = MockCapacitor()
    return capacitor.get_state_of_charge() * 100