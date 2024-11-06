# Branch: prosumerJack
# File: dataAnalysis.py

import pandas as pd # type: ignore
import numpy as np # type: ignore
import matplotlib.pyplot as plt # type: ignore
import matplotlib.dates as mdates # type: ignore
from datetime import datetime, timedelta
import calendar
import logging
import time
from config import SIMULATION_SPEEDUP
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_data(file_path: str, household: str, start_date: str, timescale: str, chunk_size: int = 10000) -> pd.DataFrame:
    """
    Load and preprocess energy data from CSV file.
    """
    start_time = time.time()
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_obj = calculate_end_date(start_date, timescale)
    
    logging.info(f"Loading data from {file_path}")
    logging.info(f"Looking for household: {household}")
    logging.info(f"Date range: {start_date} to {end_date_obj}")
    
    try:
        # First check if file exists
        if not os.path.exists(file_path):
            logging.error(f"File not found: {file_path}")
            return pd.DataFrame()
        
        # Read first few lines to check CSV structure
        with open(file_path, 'r') as f:
            header = f.readline()
            logging.info(f"CSV header: {header.strip()}")
            first_line = f.readline()
            logging.info(f"First data line: {first_line.strip()}")
    
        filtered_chunks = []
        chunks_with_data = 0
        total_chunks = 0
        
        for chunk in pd.read_csv(file_path, chunksize=chunk_size):
            total_chunks += 1
            
            # Log the unique households in each chunk
            unique_households = chunk["LCLid"].unique()
            logging.debug(f"Chunk {total_chunks} contains households: {unique_households}")
            
            # Filter by household and convert timestamp
            chunk = chunk[chunk["LCLid"] == household]
            if not chunk.empty:
                logging.debug(f"Found data for household {household} in chunk {total_chunks}")
                chunk['datetime'] = pd.to_datetime(chunk['tstp'])
                chunk = chunk[(chunk['datetime'] >= start_date_obj) & 
                             (chunk['datetime'] < end_date_obj)]
                
                if not chunk.empty:
                    chunks_with_data += 1
                    
                    # Add time-related columns
                    chunk['date'] = chunk['datetime'].dt.date
                    chunk['month'] = chunk['datetime'].dt.strftime("%B")
                    chunk['day_of_month'] = chunk['datetime'].dt.strftime("%d")
                    chunk['time'] = chunk['datetime'].dt.strftime('%X')
                    chunk['weekday'] = chunk['datetime'].dt.strftime('%A')
                    chunk['day_seconds'] = (chunk['datetime'] - 
                                          chunk['datetime'].dt.normalize()).dt.total_seconds()

                    # Set up categorical data for proper ordering
                    chunk['weekday'] = pd.Categorical(
                        chunk['weekday'],
                        categories=['Monday', 'Tuesday', 'Wednesday', 'Thursday', 
                                   'Friday', 'Saturday', 'Sunday'],
                        ordered=True
                    )
                    chunk['month'] = pd.Categorical(
                        chunk['month'],
                        categories=calendar.month_name[1:],
                        ordered=True
                    )

                    # Clean and process energy data
                    chunk = chunk[chunk["energy(kWh/hh)"] != "Null"]
                    chunk["energy"] = chunk["energy(kWh/hh)"].astype("float64")
                    chunk["cumulative_sum"] = chunk.groupby('date')["energy"].cumsum()
                    
                    filtered_chunks.append(chunk)
                else:
                    logging.debug(f"No data in date range for chunk {total_chunks}")
            else:
                logging.debug(f"No data for household {household} in chunk {total_chunks}")

        if chunks_with_data > 0:
            logging.info(f"Data found in {chunks_with_data} out of {total_chunks} chunks")
            df = pd.concat(filtered_chunks)
            df.set_index("datetime", inplace=True)
            logging.info(f"Data loaded in {time.time() - start_time:.2f} seconds. Total rows: {len(df)}")
            return df
        else:
            logging.error(f"No data found for household {household} in date range {start_date} to {end_date_obj}")
            return pd.DataFrame()
            
    except Exception as e:
        logging.error(f"Error loading data: {str(e)}", exc_info=True)
        return pd.DataFrame()

def calculate_end_date(start_date: str, timescale: str) -> str:
    """Calculate end date based on start date and timescale."""
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    
    timescale_deltas = {
        'd': timedelta(days=1),
        'w': timedelta(weeks=1),
        'm': timedelta(days=30),
        'y': timedelta(days=365)
    }
    
    if timescale not in timescale_deltas:
        raise ValueError("Invalid timescale. Use 'd' for day, 'w' for week, "
                        "'m' for month, or 'y' for year.")
    
    end_date_obj = start_date_obj + timescale_deltas[timescale]
    return end_date_obj.strftime("%Y-%m-%d %H:%M:%S")

# Add these functions to dataAnalysis.py

def get_current_readings():
    """
    Get current readings from hardware sensors.
    Returns dictionary with solar power, battery voltage, and battery current.
    """
    try:
        # Import hardware-specific libraries only when running on Raspberry Pi
        try:
            import board # type: ignore
            import busio # type: ignore
            from adafruit_ina219 import INA219_I2C # type: ignore
            
            # Initialize I2C bus and sensor
            i2c = busio.I2C(board.SCL, board.SDA)
            ina219 = INA219_I2C(i2c)
            
            # Get readings
            return {
                'solar_power': ina219.power,  # in watts
                'battery_voltage': ina219.bus_voltage,  # in volts
                'battery_current': ina219.current  # in mA
            }
        except ImportError:
            # Return mock values when not running on Raspberry Pi
            return {
                'solar_power': 0.5,  # 0.5W mock reading
                'battery_voltage': 3.7,  # 3.7V mock reading
                'battery_current': 100  # 100mA mock reading
            }
    except Exception as e:
        logging.error(f"Error getting hardware readings: {e}")
        return {
            'solar_power': 0,
            'battery_voltage': 0,
            'battery_current': 0
        }

def battery_charging(excess_energy, battery_soc, battery_capacity):
    """
    Calculate new battery state of charge when charging.
    
    Args:
        excess_energy (float): Energy available for charging (kWh)
        battery_soc (float): Current battery state of charge (0-1)
        battery_capacity (float): Total battery capacity (kWh)
    
    Returns:
        tuple: (new_soc, energy_to_grid)
    """
    max_charge = battery_capacity * (1 - battery_soc)  # Available capacity
    energy_to_battery = min(excess_energy, max_charge)
    energy_to_grid = excess_energy - energy_to_battery
    new_soc = battery_soc + (energy_to_battery / battery_capacity)
    
    return new_soc, energy_to_grid

def battery_supply(energy_needed, battery_soc, battery_capacity, depth_of_discharge):
    """
    Calculate new battery state of charge when discharging.
    
    Args:
        energy_needed (float): Energy requested from battery (kWh)
        battery_soc (float): Current battery state of charge (0-1)
        battery_capacity (float): Total battery capacity (kWh)
        depth_of_discharge (float): Maximum allowed discharge depth (0-1)
    
    Returns:
        tuple: (new_soc, energy_from_grid)
    """
    min_soc = 1 - depth_of_discharge
    available_energy = (battery_soc - min_soc) * battery_capacity
    energy_from_battery = min(energy_needed, available_energy)
    energy_from_grid = energy_needed - energy_from_battery
    new_soc = battery_soc - (energy_from_battery / battery_capacity)
    
    return new_soc, energy_from_grid

def calculate_sleep_time(current_timestamp: datetime, start_time: float) -> float:
    """
    Calculate sleep time to maintain correct simulation speed.
    
    Args:
        current_timestamp: Current timestamp in simulation
        start_time: Time when simulation started (in seconds since epoch)
    
    Returns:
        float: Time to sleep in seconds
    """
    current_time = time.time()
    elapsed_real_time = current_time - start_time
    
    # Calculate how far we are into the simulation (in seconds)
    simulation_start = current_timestamp.replace(hour=0, minute=0, second=0)
    simulated_elapsed_time = (current_timestamp - simulation_start).total_seconds()
    
    # Calculate target elapsed time based on speedup factor
    target_elapsed_time = simulated_elapsed_time / SIMULATION_SPEEDUP
    
    # Calculate how long we should sleep
    sleep_time = max(0, target_elapsed_time - elapsed_real_time)
    
    logging.debug(f"Sleep calculation: target={target_elapsed_time:.2f}s, "
                 f"elapsed={elapsed_real_time:.2f}s, sleep={sleep_time:.2f}s")
    
    return sleep_time

def setup_plot_formatting(ax, interval: str):
    """Set up plot formatting based on time interval."""
    interval_formats = {
        'd': (mdates.HourLocator(interval=1), '%H:%M'),
        'w': (mdates.DayLocator(interval=1), '%Y-%m-%d'),
        'm': (mdates.WeekdayLocator(interval=1), '%Y-%m-%d'),
        'y': (mdates.MonthLocator(interval=1), '%Y-%m')
    }
    
    locator, format_str = interval_formats.get(interval, (mdates.AutoLocator(), '%Y-%m-%d'))
    
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter(format_str))
    plt.xticks(rotation=45)
    plt.tight_layout()

def update_plot_same(df: pd.DataFrame, start_date: str, end_date: str, 
                    interval: str, queue, ready_event):
    """Create and update real-time plot with combined lines."""
    fig, ax = plt.subplots(figsize=(15, 6))
    
    # Initialize plot lines
    demand_line, = ax.plot([], [], label='Energy Demand (kWh)', 
                          color='red', marker='o', linestyle='-')
    generation_line, = ax.plot([], [], label='Energy Generation (kWh)', 
                             color='green', marker='o', linestyle='-')
    net_line, = ax.plot([], [], label='Net Energy (kWh)', 
                       color='blue', linestyle='--', marker='o')
    
    ax.legend()
    ax.set_xlabel('Time')
    ax.set_ylabel('Energy (kWh)')
    ax.set_title(f'Real-Time Energy Data for {start_date[:10]}')
    
    setup_plot_formatting(ax, interval)
    ready_event.set()  # Signal plot is initialized

    times, demands, generations, nets = [], [], [], []
    
    try:
        while True:
            data = queue.get()
            if data == "done":
                break

            timestamp = data['timestamp']
            demand = df.loc[timestamp, 'energy']
            generation = data.get('generation', 0)

            times.append(timestamp)
            demands.append(demand)
            generations.append(generation)
            nets.append(generation - demand)

            # Update plot data
            demand_line.set_data(times, demands)
            generation_line.set_data(times, generations)
            net_line.set_data(times, nets)
            
            # Rescale plot
            ax.relim()
            ax.autoscale_view()
            plt.draw()
            plt.pause(0.01)

    except Exception as e:
        logging.error(f"Error updating plot: {e}")
    finally:
        plt.show()