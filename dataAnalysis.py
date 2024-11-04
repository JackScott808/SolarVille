# Branch: consumerJack
# File: dataAnalysis.py

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import calendar
import logging
import time
from config import SIMULATION_SPEEDUP

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_data(file_path: str, household: str, start_date: str, timescale: str, chunk_size: int = 10000) -> pd.DataFrame:
    """
    Load and preprocess energy data from CSV file.
    
    Args:
        file_path: Path to CSV file
        household: Household ID
        start_date: Start date in YYYY-MM-DD format
        timescale: 'd' for day, 'w' for week, 'm' for month, 'y' for year
        chunk_size: Size of chunks for reading large CSV files
    """
    start_time = time.time()
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_obj = calculate_end_date(start_date, timescale)
    
    filtered_chunks = []
    chunks_with_data = 0
    total_chunks = 0
    
    for chunk in pd.read_csv(file_path, chunksize=chunk_size):
        total_chunks += 1
        
        # Filter by household and convert timestamp
        chunk = chunk[chunk["LCLid"] == household]
        chunk['datetime'] = pd.to_datetime(chunk['tstp'].str.replace('.0000000', ''))
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
            logging.debug(f"No data found in chunk {total_chunks} for household {household}")

    if chunks_with_data > 0:
        logging.info(f"Data found in {chunks_with_data} out of {total_chunks} chunks")
        df = pd.concat(filtered_chunks)
        df.set_index("datetime", inplace=True)
        logging.info(f"Data loaded in {time.time() - start_time:.2f} seconds. "
                    f"Total rows: {len(df)}")
        return df
    else:
        logging.error(f"No data loaded for household {household}")
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