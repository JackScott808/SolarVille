# Branch: consumerJack
# File: dataAnalysis.py

import pandas as pd # type: ignore
import numpy as np # type: ignore
import matplotlib.pyplot as plt # type: ignore
import matplotlib.dates as mdates # type: ignore
from matplotlib import ticker
from datetime import datetime, timedelta
import calendar
import logging
import time
import os
from typing import Any
from multiprocessing import Process, Queue, Event as MPEvent
from queue import Empty
from config import SIMULATION_SPEEDUP

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
            
        # Get file size
        file_size = os.path.getsize(file_path)
        logging.info(f"File size: {file_size/1024/1024:.2f} MB")
        
        # Read first few lines to check CSV structure
        with open(file_path, 'r') as f:
            header = f.readline()
            logging.info(f"CSV header: {header.strip()}")
            first_line = f.readline()
            logging.info(f"First data line: {first_line.strip()}")
    
        filtered_chunks = []
        chunks_with_data = 0
        total_chunks = 0
        
        # Read the data in chunks
        for chunk in pd.read_csv(file_path, chunksize=chunk_size):
            total_chunks += 1
            logging.info(f"Processing chunk {total_chunks}...")
            
            # Filter by household
            chunk = chunk[chunk["LCLid"] == household].copy()
            if not chunk.empty:
                # Convert energy values to numeric, replacing errors with NaN
                chunk['energy(kWh/hh)'] = pd.to_numeric(chunk['energy(kWh/hh)'], errors='coerce')
                
                # Drop any rows where conversion failed
                chunk = chunk.dropna(subset=['energy(kWh/hh)'])
                
                # Convert timestamp and filter by date range
                chunk['datetime'] = pd.to_datetime(chunk['tstp'])
                chunk = chunk[(chunk['datetime'] >= start_date_obj) & 
                             (chunk['datetime'] < end_date_obj)]
                
                if not chunk.empty:
                    chunks_with_data += 1
                    filtered_chunks.append(chunk)
                    logging.info(f"Added {len(chunk)} rows from chunk {total_chunks}")

        if chunks_with_data > 0:
            df = pd.concat(filtered_chunks)
            df.set_index("datetime", inplace=True)
            logging.info(f"Successfully loaded {len(df)} rows in {time.time() - start_time:.2f} seconds")
            
            # Verify data types
            logging.info(f"Data types: {df.dtypes}")
            logging.info(f"Sample of energy values: {df['energy(kWh/hh)'].head()}")
            
            return df
        else:
            logging.error(f"No data found for household {household} in date range")
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

def calculate_sleep_time(current_timestamp: datetime, start_time: float) -> float:
    """Calculate sleep time to maintain correct simulation speed."""
    current_time = time.time()
    elapsed_real_time = current_time - start_time
    
    simulation_start = current_timestamp.replace(hour=0, minute=0, second=0)
    simulated_elapsed_time = (current_timestamp - simulation_start).total_seconds()
    
    target_elapsed_time = simulated_elapsed_time / SIMULATION_SPEEDUP
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
    
    locator, format_str = interval_formats.get(interval, (ticker.AutoLocator(), '%Y-%m-%d'))
    
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter(format_str))
    plt.xticks(rotation=45)
    plt.tight_layout()

def update_plot_same(df: pd.DataFrame, start_date: str, end_date: str, 
                    interval: str, queue: Queue, ready_event: MPEvent) -> None:
    """Create and update real-time plot with combined lines."""
    try:
        logging.info("Initializing plot...")
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
        
        while True:
            try:
                data = queue.get(timeout=1)  # Add 1 second timeout
                if data == "done":
                    break

                timestamp = data['timestamp']
                demand = data.get('demand', 0)
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
                
            except Empty:
                plt.pause(0.01)  # Keep the plot responsive
                continue

    except Exception as e:
        logging.error(f"Error in plot update loop: {e}", exc_info=True)
    finally:
        plt.close()