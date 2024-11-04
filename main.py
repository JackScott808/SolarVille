# Branch: consumerJack
# File: main.py

import argparse
import time
import pandas as pd # type: ignore
from multiprocessing import Process, Queue, Event
import threading
import logging
import platform
import requests
from io import StringIO
from pricing import calculate_price
from dataAnalysis import load_data, calculate_end_date, update_plot_separate, update_plot_same
from config import LOCAL_IP, PEER_IP
from battery_energy_management import battery_charging, battery_supply
from lcdControlTest import display_message

SOLAR_SCALE_FACTOR = 4000  # Adjust this value as needed

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def start_simulation_local(args):
    start_time = time.time()
    logging.info("Starting simulation...")
    df = load_data(args.file_path, args.household, args.start_date, args.timescale)
    if df.empty:
        logging.error("No data loaded. Exiting simulation.")
        return

    end_date = calculate_end_date(args.start_date, args.timescale)
    queue = Queue()
    ready_event = Event()

    plot_process = Process(target=update_plot_same, args=(df, args.start_date, end_date, args.timescale, queue, ready_event))
    plot_process.start()

    ready_event.wait()  # Wait for the plot to be initialized
    logging.info("Plot initialized, waiting for simulation to start...")

    try:
        while True:
            response = requests.get(f'http://{PEER_IP}:5000/simulation_status')
            if response.status_code == 200:
                status = response.json()
                if status['status'] == 'completed':
                    logging.info("Simulation completed.")
                    break
                elif status['status'] == 'in_progress':
                    timestamp = pd.Timestamp(status['current_timestamp'])
                    if timestamp in df.index:
                        current_data = df.loc[timestamp]
                        df = process_trading_and_lcd(df, timestamp, current_data, queue)
                        logging.info(f"Processed timestamp: {timestamp}")
                    else:
                        logging.warning(f"Timestamp {timestamp} not found in local DataFrame")
                elif status['status'] == 'not_started':
                    logging.info("Waiting for simulation to start...")
                else:
                    logging.info(f"Unknown simulation status: {status['status']}")
            else:
                logging.error("Failed to get simulation status from prosumer")
            
            time.sleep(1)  # Adjust as needed

    except KeyboardInterrupt:
        logging.info("Simulation interrupted.")
    finally:
        queue.put("done")
        plot_process.join()


# reading the dataframe
def fetch_dataframe():
    try:
        response = requests.get(f'http://{PEER_IP}:5000/get_dataframe', timeout=3)
        if response.status_code == 200:
            return pd.read_json(StringIO(response.text), orient='split')
        else:
            logging.error(f"Failed to fetch DataFrame. Status code: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Error fetching DataFrame: {e}")
        return None
    
def process_trading_and_lcd(df, timestamp, current_data, queue):

    trade_amount = 0
    demand = current_data['energy'] # unit kWh
    
    # Calculate balance unit: kWh
    balance = - demand
    
    # Update dataframe
    df.loc[timestamp, [ 'demand', 'balance']] = [demand, balance]

    # Put data in queue for plotting
    queue.put({
        'timestamp': timestamp,
        'balance': balance
    })
    print(demand, balance)
    # Send updates to Flask server
    update_data_1 = {
        'demand': demand,
        'balance': balance
        # 'demand': 1,
        # 'balance': -1
    }
    make_api_call(f'http://{PEER_IP}:5000/update_peer_data', update_data_1)
    
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        logging.info(f"Retry attempt {retry_count + 1}")
        df_peer = fetch_dataframe()
    
        if df_peer is None or df_peer.empty:
            logging.warning("Received empty DataFrame from prosumer")
            retry_count += 1
            time.sleep(1)
            continue

        if timestamp in df_peer.index:
            if 'Enable' in df_peer.columns:
                # Check the Enable value for the current timestamp
                enable = df_peer.loc[timestamp, 'Enable']
        
                # Start the trading for consumer after the prosumer provides trade amount
                if enable == 1:
                    peer_data_response = requests.get(f'http://{LOCAL_IP}:5000/get_peer_data')
                    if peer_data_response.status_code == 200:
                        peer_data = peer_data_response.json()
                        
                        peer_price = peer_data.get('peer_price')
                        buy_grid_price = peer_data.get('buy_grid_price')
                        trade_amount = peer_data.get('trade_amount', 0)# unit: kWh

                        if trade_amount is None:
                            logging.warning(f"No trading data available for peer {PEER_IP}")
                        
                        # Perform trading (now in kilo Watt-hours) 
                        buy_from_grid = abs(balance) - trade_amount
                        df.loc[timestamp, ['balance', 'currency', 'trade_amount']] = [
                            df.loc[timestamp, 'balance'] - balance,  # update balance
                            df.loc[timestamp, 'currency'] - trade_amount * peer_price - buy_from_grid * buy_grid_price,  # update currency
                            trade_amount  # update trade_amountge
                        ]
                            
                        logging.info(f"Bought {trade_amount*1000:.2f} Wh from peer at {peer_price:.2f} ￡/kWh" # unit
                                    f"and the remaining {buy_from_grid*1000:.2f} Wh to the grid at {buy_grid_price:.2f} ￡/kWh") # unit
                        break
                    else:
                        logging.error("Waiting for prosumer to enable trading.")
                else:
                    logging.info("'Enable' column not found in peer DataFrame.")    
                
            else:
                logging.error("'Enable' column not found in peer DataFrame")
        else:
            logging.error(f"Timestamp {timestamp} not found in peer DataFrame")

        retry_count += 1
        if retry_count >= max_retries:
            logging.error("Max retries reached, skipping this timestamp")
            break
        time.sleep(1)

        # Update LCD display
        display_message(f"Dem:{demand*1000:.0f}Wh Tra:{trade_amount*1000:.0f}Wh") # unit
        
        logging.info(
            f"At {timestamp} , "
            f"Demand: {demand*1000:.2f}Wh, " # unit
            f"Balance: {df.loc[timestamp, 'balance']:.6f}Wh, "
            f"Currency: {df.loc[timestamp, 'currency']:.2f}￡, "
            f"LCD updated"
        )
        
    return df

def make_api_call(url, data, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, timeout=5)
            if response.status_code == 200:
                print("Data sent successfully!")
            else:
                print(f"Failed to send data. Status code: {response.status_code}")
        
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logging.error(f"API call failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                logging.error(f"Max retries reached for {url}")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Smart Grid Simulation')
    parser.add_argument('--file_path', type=str, required=True, help='Path to the CSV file')
    parser.add_argument('--household', type=str, required=True, help='Household ID for the data')
    parser.add_argument('--start_date', type=str, required=True, help='Start date for the simulation')
    parser.add_argument('--timescale', type=str, required=True, choices=['d', 'w', 'm', 'y'], help='Timescale: d for day, w for week, m for month, y for year')


    args = parser.parse_args()  # Parse the arguments
    

    from server import app
    server_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000})
    server_thread.start()
    
    time.sleep(2)  # Give the server a moment to start
    
    simulation_thread = threading.Thread(target=start_simulation_local, args=(args,))
    simulation_thread.start()
    
    simulation_thread.join()
    server_thread.join()
