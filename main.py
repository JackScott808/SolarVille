# Branch: ProsumerJack
# File: main.py
import argparse
import time
import pandas as pd # type: ignore
from multiprocessing import Process, Queue, Event
import threading
import logging
import platform
import requests
from pricing import calculate_price
from dataAnalysis import load_data, calculate_end_date, update_plot_separate, update_plot_same
from config import LOCAL_IP, PEER_IP
from solarMonitor import get_current_readings
from battery_energy_management import battery_charging, battery_supply
from lcdControlTest import display_message
from io import StringIO

SOLAR_SCALE_FACTOR = 8000  # Adjust this value as needed
trade_amount = 0
battery_soc = 0.5

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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

def start_simulation_local(args):
    start_time = time.time()
    logging.info("Starting simulation...")
    df = load_data(args.file_path, args.household, args.start_date, args.timescale)
    if df.empty:
        logging.error("No data loaded. Exiting simulation.")
        return
    
    global trade_amount
    global battery_soc
    global current_timestamp
    
    # Initialize the DataFrame with the loaded data
    df['generation'] = 0.0
    df['balance'] = 0.0
    df['currency'] = 0
    df['battery_charge'] = 0.5
    df['Enable'] = 0
    
    end_date = calculate_end_date(args.start_date, args.timescale)
    total_simulation_time = (df.index[-1] - df.index[0]).total_seconds()
    simulation_speed = 30 * 60 / 6  # 30 minutes of data in 6 seconds of simulation

    queue = Queue()
    ready_event = Event()

    plot_process = Process(target=update_plot_same, args=(df, args.start_date, end_date, args.timescale, queue, ready_event))
    plot_process.start()

    ready_event.wait()  # Wait for the plot to be initialized

    # Signal that the simulation is starting
    current_timestamp = 'START'
    requests.post(f'http://{PEER_IP}:5000/sync', json={'timestamp': 'START'})

    try:
        for timestamp in df.index:
            current_time = time.time()
            elapsed_time = current_time - start_time
            simulated_elapsed_time = elapsed_time * simulation_speed

            if simulated_elapsed_time >= total_simulation_time:
                logging.info("Simulation completed.")
                break

            current_data = df.loc[timestamp]

            logging.info(f"Processing timestamp {timestamp}")
            logging.info(f"Elapsed time: {elapsed_time:.2f}, Current data: {current_data}")

            if not current_data.empty:
                df = process_trading_and_lcd(df, timestamp, current_data, queue)
                logging.info("Processed data and LCD update")
            else:
                logging.warning("Empty current_data, skipping processing")

            # Update current_timestamp and sync with consumer
            current_timestamp = str(timestamp)
            requests.post(f'http://{PEER_IP}:5000/sync', json={'timestamp': current_timestamp})

            # Calculate sleep time to maintain simulation speed
            sleep_time = max(0, (30 * 60 / simulation_speed) - (time.time() - current_time))
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logging.info("Simulation interrupted.")
    except Exception as e:
        logging.error(f"An error occurred during simulation: {e}")
    finally:
        queue.put("done")
        plot_process.join()
        logging.info("Simulation ended.")

    # Send final sync to indicate simulation completion
    current_timestamp = 'END'
    requests.post(f'http://{PEER_IP}:5000/sync', json={'timestamp': 'END'})

def process_trading_and_lcd(df, timestamp, current_data, queue):
    try:
        readings = get_current_readings()
        solar_power = readings['solar_power'] * SOLAR_SCALE_FACTOR  # unit: W
        # Assume the solar power remains the same in every half hour, convert W to kW
        solar_energy = solar_power * 0.5 / 1000  # unit: kWh
    except Exception as e:
        logging.error(f"Failed to get solar data: {e}")
        solar_power = 0
        solar_energy = 0

    global trade_amount
    global battery_soc
    
    demand = current_data['energy']  # unit kWh
    
    # Calculate balance unit kWh
    balance = solar_energy - demand
    
    # Update dataframe
    df.loc[timestamp, ['generation', 'demand', 'balance']] = [solar_energy, demand, balance]

    # Put data in queue for plotting
    queue.put({
        'timestamp': timestamp,
        'generation': solar_energy
    })

    # Send updates to Flask server
    update_data_1 = {
        'demand': demand,
        'generation': solar_energy,
        'balance': balance,
        'battery SoC': battery_soc
    }
    make_api_call(f'http://{PEER_IP}:5000/update_peer_data', update_data_1)

    # Get peer data for trading
    peer_data_response = requests.get(f'http://{LOCAL_IP}:5000/get_peer_data')
    if peer_data_response.status_code == 200:
        peer_data = peer_data_response.json()
        # Get peer demand with error checking
        peer_demand = peer_data.get('demand', 0)
        peer_balance = peer_data.get('balance', 0)
        logging.info(f"consumer demand:{peer_demand} kWh, "
                     f"consumer balance: {peer_balance} kWh")
    else:
        logging.error("Failed to get peer demand and balance")
        peer_demand = 0
        peer_balance = 0

    total_demand = demand + peer_demand
    total_supply = solar_energy

    sell_grid_price = 0.05  # unit: ￡/kWh
    buy_grid_price = 0.25  # unit: ￡/kWh
    peer_price = calculate_price(total_demand, total_supply, buy_grid_price=buy_grid_price, sell_grid_price=sell_grid_price)

    # Log the calculated prices
    logging.info(f"Calculated prices - Sell Grid Price: {sell_grid_price:.2f} ￡/kWh, "
                 f"Peer Price: {peer_price:.2f} ￡/kWh, "
                 f"Buy Grid Price: {buy_grid_price:.2f} ￡/kWh")

    # Perform trading (now in kilo Watt-hours)
    if balance >= 0:
        # The household has excess energy
        if peer_balance >= 0:
            trade_amount = 0
            battery_soc, sell_to_grid = battery_charging(excess_energy=balance, battery_soc=battery_soc, battery_capacity=5)
            # the other household has excess energy too, this household energy can sell to grid
            df.loc[timestamp, ['balance', 'currency', 'battery_charge']] = [
                df.loc[timestamp, 'balance'] - balance,  # update balance
                df.loc[timestamp, 'currency'] + sell_to_grid * sell_grid_price,  # update currency
                battery_soc  # update battery_charge
            ]
            logging.info(f"Sold {balance*1000:.2f} Wh to the grid at {sell_grid_price:.2f} ￡/kWh")
        elif peer_balance < 0:
            # the other household needs energy
            if balance > abs(peer_balance):
                # energy is enough to supply the other household
                trade_amount = abs(peer_balance)
                remaining_balance = balance - trade_amount
                battery_soc, sell_to_grid = battery_charging(excess_energy=remaining_balance, battery_soc=battery_soc, battery_capacity=5)
                df.loc[timestamp, ['balance', 'currency', 'battery_charge']] = [
                    df.loc[timestamp, 'balance'] - balance,  # update balance 
                    df.loc[timestamp, 'currency'] + (trade_amount * peer_price) + (sell_to_grid * sell_grid_price), # update currency
                    battery_soc  # update battery_charge
                ]
                logging.info(f"Sold {trade_amount*1000:.2f} Wh to peer at {peer_price:.2f} ￡/kWh and the remaining {sell_to_grid*1000:.2f} Wh to the grid at {sell_grid_price:.2f} ￡/kWh")
            else:
                # energy can only supply part of the need of the other household
                trade_amount = balance
                df.loc[timestamp, ['balance', 'currency', 'battery_charge']] = [
                    df.loc[timestamp, 'balance'] - balance,  # update balance
                    df.loc[timestamp, 'currency'] + trade_amount * peer_price, # update currency
                    battery_soc  # update battery_charge
                ]
                logging.info(f"Sold {trade_amount*1000:.2f} Wh to peer at {peer_price:.2f} ￡/kWh")
    elif balance < 0:
        logging.info(f"need electricity")
        trade_amount = 0
        # the household needs energy
        battery_soc, buy_from_grid = battery_supply(excess_energy=balance, battery_soc=battery_soc, battery_capacity=5, depth_of_discharge=0.8)
        
        logging.info(f"Updating DataFrame at timestamp: {timestamp}, Current balance: {df.loc[timestamp, 'balance']},"
                     f" Current currency: {df.loc[timestamp, 'currency']},"
                     f"buy_from_grid: {buy_from_grid},buy_grid_price:{buy_grid_price}")
        df.loc[timestamp, ['balance', 'currency', 'battery_charge']] = [
            df.loc[timestamp, 'balance'] - balance,  # update balance
            df.loc[timestamp, 'currency'] - buy_from_grid * buy_grid_price, # update currency
            battery_soc  # update battery_charge
        ]
        logging.info(f"Bought {buy_from_grid*1000:.2f} Wh from grid at {buy_grid_price:.2f} ￡/kWh")
    
    # Update LCD display
    display_message(f"Bat:{battery_soc*100:.0f}% Gen:{solar_power:.0f}W")
    
    logging.info(
        f"At {timestamp} - Generation: {solar_power:.6f} W, "
        f"Demand: {demand:.2f} kWh, Battery: {battery_soc*100:.2f}%, "
        f"Balance: {df.loc[timestamp, 'balance']:.6f} kWh, "
        f"Currency: {df.loc[timestamp, 'currency']:.2f}, "
        f"Trade amount: {trade_amount} kWh, "
        f"LCD updated"
    )
    
    # Set Enable = 1 for the current timestamp
    df.loc[timestamp, 'Enable'] = 1

    # Convert the DataFrame to JSON format
    df_json = df.to_json(orient='split')
    requests.post(f'http://{PEER_IP}:5000/update_dataframe', json={'df': df_json, 'timestamp': str(timestamp)})
    
    # Send updates to Flask server
    update_data_2 = {
        'battery_charge': battery_soc,
        'trade_amount': trade_amount,
        'buy_grid_price': buy_grid_price,
        'peer_price': peer_price,
        'df': df_json 
    }
    make_api_call(f'http://{PEER_IP}:5000/update_trade_data', update_data_2)

    return df

def make_api_call(url, data, max_retries=3):
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
