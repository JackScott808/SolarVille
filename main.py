import argparse
import time
import pandas as pd # type: ignore
from multiprocessing import Process, Queue, Event
import threading
import logging
import platform
import requests
import random
import numpy as np
from flask import request
from trading import calculate_price
from dataAnalysis import load_data, calculate_end_date, simulate_generation, update_plot_separate, update_plot_same
from config import LOCAL_IP, PEER_IP

max_battery_charge = 1.0
min_battery_charge = 0.0

# Conditionally import the correct modules based on the platform
if platform.system() == 'Darwin':  # MacOS
    from mock_batteryControl import update_battery_charge, read_battery_charge
    from mock_lcdControlTest import display_message
else:  # Raspberry Pi
    from batteryControl import update_battery_charge, read_battery_charge
    from lcdControlTest import display_message

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# This function is the main driver of the simulation
def start_simulation_local():
    if not synchronize_start(): # Calls the function to synchronize the start of the simulation
        logging.error('Failed to start simulation') # Logs an error message if the simulation fails to start
        return
    
    # Wait for the simulation to start
    response = requests.get('http://localhost:5000/wait_for_start')
    if response.status_code != 200:
        logging.error('Failed to start simulation')
        return
    
    start_time = time.time() # Get the current time

    df = load_data(args.file_path, args.household, args.start_date, args.timescale) # Load the data
    if df.empty:
        logging.error("No data loaded. Exiting simulation.") # Logs an error message if the data fails to load
        return
    df = simulate_generation(df, mean=0.5, std=0.2) # Simulate the generation data
    end_date = calculate_end_date(args.start_date, args.timescale) # Calculate the end date of the simulation
    logging.info(f"Data loaded in {time.time() - start_time:.2f} seconds") # Logs a message with the time taken to load the data
    
    queue = Queue() # Create a queue for communication between the main thread and the plotting process
    ready_event = Event() # Create an event to signal when the plot is ready
    plot_process = Process(target=plot_data, args=(df, args.start_date, end_date, args.timescale, args.separate, queue, ready_event)) # Create a process for plotting the data
    plot_process.start() # Start the plotting process
    
    # Wait for the plotting process to signal that it is ready
    ready_event.wait()
    logging.info("Plot initialized, starting simulation...")

    df['balance'] = df['generation'] - df['energy']  # Calculate the balance for each row
    df['currency'] = 100.0  # Initialize the currency column to 100
    df['battery_charge'] = 0.5  # Assume 50% initial charge
    logging.info("Dataframe for balance, currency and battery charge is created.")
    
    # Main simulation loop
    try:
        for timestamp in df.index: # Iterate over each timestamp in the index
            current_time = time.time() # Get the current time
            elapsed_time = current_time - start_time # Calculate the elapsed time
            
            # Calculate the expected elapsed time based on the simulation speed
            expected_elapsed_time = (timestamp - df.index[0]).total_seconds() * (6 / 3600)  # 6 seconds per hour
            
            # If we're ahead of schedule, wait
            if elapsed_time < expected_elapsed_time:
                time.sleep(expected_elapsed_time - elapsed_time)
            
            current_data = df.loc[timestamp] # Get the current data for the timestamp
            
            if not current_data.empty: # Check if the current data is not empty
                df = process_trading_and_lcd(df, timestamp, current_data, current_data['battery_charge']) # Process trading and update the LCD display
                
                # Update the plot by putting the timestamp in the queue to signal the plotting process
                queue.put(timestamp)

    # Handle keyboard interrupt
    except KeyboardInterrupt:
        logging.info("Simulation interrupted.")
    finally:
        queue.put("done") # Signal the plotting process to finish
        plot_process.join() # Wait for the plotting process to finish

# This function synchronizes the start of the simulation between the two Raspberry Pis
def synchronize_start():
    current_time = time.time() # Get the current time
    start_time = current_time + 10  # Start 10 seconds from now
    
    try:
        peers = [LOCAL_IP, PEER_IP]  # List of both IPs
        
        # Set start time on both Pis
        response = requests.post('http://localhost:5000/sync_start', json={"start_time": start_time, "peers": peers})
        peer_response = requests.post(f'http://{PEER_IP}:5000/sync_start', json={"start_time": start_time, "peers": peers})
        
        if response.status_code == 200 and peer_response.status_code == 200: # Check if both responses are successful
            logging.info(f"Simulation will start at {time.ctime(start_time)}")
            
            # Set a fixed seed for random number generation
            random.seed(42)
            np.random.seed(42)
            
            # Wait until it's time to start
            wait_time = start_time - time.time()
            if wait_time > 0:
                logging.info(f"Waiting for {wait_time:.2f} seconds before starting simulation")
                time.sleep(wait_time)
            else:
                logging.warning("Simulation start time has passed. Starting simulation immediately.")
            
            # Start the simulation
            simulation_start_time = time.time() # Get the current time
            requests.post('http://localhost:5000/start_simulation', json={'start_time': simulation_start_time}) # Make a POST request to start the simulation on the local Pi
            requests.post(f'http://{PEER_IP}:5000/start_simulation', json={'start_time': simulation_start_time}) # Make a POST request to start the simulation on the peer Pi
            
            logging.info("Starting simulation now")
            return True # Return True if the simulation starts successfully
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error during synchronization: {e}")
    
    logging.error("Failed to start simulation")
    return False

# This function calls the update_plot_separate function if the separate flag is set to True, otherwise it calls the update_plot_same function
def plot_data(df, start_date, end_date, timescale, separate, queue, ready_event):
    if separate:
        update_plot_separate(df, start_date, end_date, timescale, queue, ready_event)
    else:
        update_plot_same(df, start_date, end_date, timescale, queue, ready_event)

# This function processes the trading and updates the LCD display
def process_trading_and_lcd(df, timestamp, current_data, battery_charge):
    demand = current_data['energy'] # Get the energy demand for the current timestamp
    generation = current_data['generation'] # Get the energy generation for the current timestamp
    balance = generation - demand # Calculate the energy balance, which is the difference between generation and demand
    df.loc[timestamp, 'demand'] = demand # Update the demand column in the dataframe
    df.loc[timestamp, 'generation'] = generation # Update the generation column in the dataframe
    df.loc[timestamp, 'balance'] = balance # Update the balance column in the dataframe
    
    battery_charge = update_battery_charge(generation, demand) # Calls the function to update the battery charge based on generation and demand
    df.loc[timestamp, 'battery_charge'] = battery_charge # Update the battery charge column in the dataframe

    # Send updates to Flask server
    update_data = { # Create a dictionary with the update data
        'demand': demand,
        'generation': generation,
        'balance': balance,
        'battery_charge': battery_charge
    }
    make_api_call(f'http://{PEER_IP}:5000/update_peer_data', update_data) # Make an API call to update the peer data

    # Get peer data for trading
    peer_data_response = requests.get(f'http://{PEER_IP}:5000/get_peer_data') # Make a GET request to get the peer data
    if peer_data_response.status_code == 200: # Checks if the response was successful
        peer_data = peer_data_response.json() # Set the peer data to the JSON response
        
        # Get peer balance with error checking
        peer_balance = peer_data.get(PEER_IP, {}).get('balance')
        if peer_balance is None:
            logging.warning(f"No balance data available for peer {PEER_IP}")
        else:
            # Perform trading
            if balance > 0 and peer_balance < 0: # Checks if local energy is in a surplus and peer energy is in a deficit
                # This household has excess energy to sell
                trade_amount = min(balance, abs(peer_balance)) # Calculate the trade amount
                price = calculate_price(balance, abs(peer_balance)) # Sets the price by calling the calculate_price function
                df.loc[timestamp, 'balance'] -= trade_amount # Update the balance column in the dataframe after the trade is completed
                df.loc[timestamp, 'currency'] += trade_amount * price # Update the currency column in the dataframe after the trade is completed
                logging.info(f"Sold {trade_amount:.2f} kWh at {price:.2f} £/kWh") # Logs a message with the amount sold and the price
            elif balance < 0 and peer_balance > 0: # Checks if local energy is in a deficit and peer energy is in a surplus
                # This household needs to buy energy
                trade_amount = min(abs(balance), peer_balance) # Calculate the trade amount
                price = calculate_price(peer_balance, abs(balance)) # Sets the price by calling the calculate_price function
                df.loc[timestamp, 'balance'] += trade_amount # Update the balance column in the dataframe after the trade is completed
                df.loc[timestamp, 'currency'] -= trade_amount * price # Update the currency column in the dataframe after the trade is completed
                logging.info(f"Bought {trade_amount:.2f} kWh at {price:.2f} £/kWh") # Logs a message with the amount bought and the price
    else:
        logging.error("Failed to get peer data for trading")

    # Update LCD display
    display_message(f"Gen: {generation:.2f}W\nDem: {demand:.2f}W\nBat: {battery_charge * 100:.2f}%")
    
    # Log the important readings at that timestamp
    logging.info(
        f"At {timestamp} - Generation: {generation:.2f}W, "
        f"Demand: {demand:.2f}W, Battery: {battery_charge * 100:.2f}%, "
        f"Balance: {df.loc[timestamp, 'balance']:.2f}, "
        f"Currency: {df.loc[timestamp, 'currency']:.2f}, "
        f"LCD updated"
    )

    return df # Return the updated dataframe

# This function makes API calls with retry logic
# It is called by the process_trading_and_lcd function to update the peer data
def make_api_call(url, data, max_retries=3): # Takes the URL, data and max_retries as arguments
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, timeout=5)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logging.error(f"API call failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                logging.error(f"Max retries reached for {url}")
    return None

# This function initializes the simulation by loading the data and simulating the generation
# It is called by the main function
def initialize_simulation():
    global df, end_date # Declare the global variables df and end_date to be used in the start_simulation_local function
    logging.info("Loading data, please wait...")
    start_time = time.time() # Get the current time
    df = load_data(args.file_path, args.household, args.start_date, args.timescale) # Load the data
    if df.empty: # Checks if the dataframe is empty
        logging.error("No data loaded. Exiting simulation.")
        return
    df = simulate_generation(df, mean=0.5, std=0.2) # Simulate the generation data
    end_date = calculate_end_date(args.start_date, args.timescale) # Calculate the end date of the simulation
    logging.info(f"Data loaded in {time.time() - start_time:.2f} seconds") # Logs a message with the time taken to load the data

# This block of code is executed when the script is run
# It parses the command line arguments and calls the initialize_simulation function
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Smart Grid Simulation')
    parser.add_argument('--file_path', type=str, required=True, help='Path to the CSV file')
    parser.add_argument('--household', type=str, required=True, help='Household ID for the data')
    parser.add_argument('--start_date', type=str, required=True, help='Start date for the simulation')
    parser.add_argument('--timescale', type=str, required=True, choices=['d', 'w', 'm', 'y'], help='Timescale: d for day, w for week, m for month, y for year')
    parser.add_argument('--separate', action='store_true', help='Flag to plot data in separate subplots')

    args = parser.parse_args()  # Parse the arguments
    initialize_simulation() # Initialize the simulation

    from server import app # Import the Flask app
    # Start the server and simulation in separate threads to run concurrently
    server_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000})
    server_thread.start() # Start the server thread
    
    time.sleep(2)  # Give the server a moment to start
    
    simulation_thread = threading.Thread(target=start_simulation_local) # Create a thread for the simulation
    simulation_thread.start() # Start the simulation thread
    
    simulation_thread.join() # Wait for the simulation thread to finish
    server_thread.join() # Wait for the server thread to finish