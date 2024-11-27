# Prosumer
# capacitorControl.py
import time
import math
import logging
import board # type: ignore
import busio # type: ignore
from adafruit_ina219 import INA219 # type: ignore

logging.basicConfig(level=logging.INFO)

# Capacitor specifications
CAPACITANCE = 1.5  # Farads
MAX_VOLTAGE = 5.5  # V
MIN_VOLTAGE = 0.5  # V
INTERNAL_RESISTANCE = 100  # ohms

class CapacitorController:
    def __init__(self):
        # I2C setup
        i2c = busio.I2C(board.SCL, board.SDA)
        
        # INA219 setup for capacitor monitoring
        self.ina219 = INA219(i2c, addr=0x41)  # Using the battery address
        
        # Configure INA219 for higher precision
        self.ina219.bus_adc_resolution = INA219.ADC_RESOLUTION_12BIT_32S
        self.ina219.shunt_adc_resolution = INA219.ADC_RESOLUTION_12BIT_32S
        
        self.last_update = time.time()
        self.last_voltage = self.ina219.bus_voltage
        
    def calculate_energy(self, voltage):
        """Calculate stored energy in Joules"""
        return 0.5 * CAPACITANCE * (voltage ** 2)
    
    def get_state_of_charge(self, voltage):
        """Calculate state of charge as a percentage"""
        current_energy = self.calculate_energy(voltage)
        max_energy = 0.5 * CAPACITANCE * (MAX_VOLTAGE ** 2)
        min_energy = 0.5 * CAPACITANCE * (MIN_VOLTAGE ** 2)
        usable_energy = max_energy - min_energy
        current_usable_energy = current_energy - min_energy
        return max(0.0, min(1.0, current_usable_energy / usable_energy))
    
    def read_measurements(self):
        """Read current measurements from INA219"""
        bus_voltage = self.ina219.bus_voltage
        current = self.ina219.current / 1000.0  # Convert to A
        power = bus_voltage * current  # Power in Watts
        
        return bus_voltage, current, power
    
    def update_charge_state(self, solar_power):
        """Update capacitor state based on solar power input"""
        current_time = time.time()
        time_delta = current_time - self.last_update
        
        # Read current state
        voltage, current, power = self.read_measurements()
        
        # Calculate energy change
        energy_change = power * time_delta
        
        # Calculate efficiency based on power flow direction
        efficiency = 0.95 if power > 0 else 0.90
        
        # Calculate state of charge
        soc = self.get_state_of_charge(voltage)
        
        # Update last values
        self.last_update = current_time
        self.last_voltage = voltage
        
        logging.info(f"Capacitor Update - Voltage: {voltage:.2f}V, "
                    f"Current: {current:.3f}A, Power: {power:.2f}W, "
                    f"SoC: {soc*100:.2f}%")
        
        return soc, efficiency

# Global controller instance
controller = CapacitorController()

def update_capacitor_charge(solar_power, demand_power):
    """Main update function to be called from other modules"""
    return controller.update_charge_state(solar_power)

def read_capacitor_charge():
    """Read current capacitor charge state"""
    voltage, _, _ = controller.read_measurements()
    return controller.get_state_of_charge(voltage) * 100  # Return as percentage