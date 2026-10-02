import csv
from datetime import datetime
import time
from solarMonitor import get_current_readings

def init_csv_file():
    csv_filename = f"solar_battery_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = [
        "Timestamp",
        "Solar Current (A)", "Solar Power (mW)",
        "Battery Voltage (V)", "Battery Current (A)"
    ]
    with open(csv_filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(headers)
    return csv_filename

def log_data(csv_filename, data):
    with open(csv_filename, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(data)

def main():
    csv_filename = init_csv_file()
    print("Press CTRL+C to stop logging")
    try:
        while True:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            readings = get_current_readings()
            
            solar_data = readings['solar_current_a'], readings['solar_power_mw']
            battery_data = readings['battery_voltage'], readings['battery_current_a']
            
            log_data(csv_filename, [
                timestamp,
                *solar_data,
                *battery_data
            ])
            
            print(f"Solar:   {solar_data[0]*1000:.3f} mA, {solar_data[1]:.3f} mW")
            print(f"Battery: {battery_data[0]:.3f} V, {battery_data[1]*1000:.3f} mA")
            print("------------------------")
            
            time.sleep(3)  # Log every 3 seconds
    except KeyboardInterrupt:
        print("\nLogging stopped by user")
    finally:
        print(f"Data saved to {csv_filename}")

if __name__ == "__main__":
    main()