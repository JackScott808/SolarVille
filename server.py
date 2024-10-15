# Prosumer
from flask import Flask, request, jsonify
import logging
import time
import threading
from config import PEER_IP, LOCAL_IP
import pandas as pd
from io import StringIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

peers = []
peer_ready = {}
simulation_started = threading.Event()
peer_data = {}

# Shared data for the example
energy_data = {
    "balance": 0,
    "currency": 100.0,
    "demand": 0,
    "generation": 0,
    "battery_charge": 0,
}

df = pd.DataFrame()

@app.route('/get_dataframe', methods=['GET'])
def get_dataframe():
    global df
    try:
        if df.empty:
            logging.warning("Datafram is empty. Returning empty DataFrame.")
            return df.to_json(orient='split'), 200
        return df.to_json(orient='split'), 200
    except Exception as e:
        logging.error(f"Error serving DataFrame: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/ready', methods=['POST'])
def ready():
    data = request.json
    peer_ip = request.remote_addr
    if peer_ip in peers:
        peer_ready[peer_ip] = True
        logging.info(f"Peer {peer_ip} is ready")
        return jsonify({"status": "ready"})
    else:
        logging.warning(f"Peer {peer_ip} not recognized")
        return jsonify({"status": "peer not recognized"}), 400

@app.route('/update_peer_data', methods=['POST'])
def update_peer_data():
    data = request.json
    print("server received data from consumer:", data)
    peer_ip = request.remote_addr
    if peer_ip not in peer_data:
        peer_data[peer_ip] = {}
    peer_data[peer_ip].update(data)
    
    demand = data.get('demand', 'N/A')
    balance = data.get('balance', 'N/A')
    
    # Safely format the logging message with checks for numeric types
    logging.info(
    f"Updated server peer data for {peer_ip}: "
    f"Demand: {float(demand):.2f} kWh, " if isinstance(demand, (int, float)) else "Demand: N/A, "
    f"Balance: {float(balance):.2f} kWh, " if isinstance(balance, (int, float)) else "Balance: N/A"
    )
    return jsonify({"status": "updated"})

@app.route('/update_trade_data', methods=['POST'])
def update_trade_data():
    global df
    data = request.json
    peer_ip = request.remote_addr
    if peer_ip not in peer_data:
        peer_data[peer_ip] = {}
    peer_data[peer_ip].update(data)
    
<<<<<<< HEAD
    df_json = data.get('df')
    if df_json:
        try:
            df = pd.read_json(StringIO(df_json), orient='split')
            logging.info(f"Received updated DataFrame with Enable column updated.")
        except ValueError as e:
            logging.error(f"Error parsing DataFrame JSON: {e}")
=======
    # Update the global DataFrame
    df_json = data.get('df')
    if df_json:
        df = pd.read_json(df_json, orient='split')
        logging.info(f"Updated global DataFrame with new data.")
>>>>>>> df08fb9d7c080d4b8c3e147dfbe19fd846a50c59
        
    trade_amount = data.get('trade_amount', 'N/A')
    buy_grid_price = data.get('buy_grid_price', 'N/A')
    peer_price = data.get('peer_price', 'N/A')
    battery_charge = data.get('battery_charge', 'N/A')

    # Safely format the logging message with checks for numeric types
    logging.info(
        f"Updated server trade data for {peer_ip}: "
        f"Trade amount: {float(trade_amount):.2f} kWh, " if isinstance(trade_amount, (int, float)) else "Trade amount: N/A, "
        f"Buy grid price: {float(buy_grid_price):.2f} pound/kWh, " if isinstance(buy_grid_price, (int, float)) else "Buy grid price: N/A, "
        f"Peer price: {float(peer_price):.2f} pound/kWh, " if isinstance(peer_price, (int, float)) else "Peer price: N/A, "
        f"Battery Charge: {float(battery_charge) * 100:.2f}% " if isinstance(battery_charge, (int, float)) else "Battery Charge: N/A"
    )
    return jsonify({"status": "updated"}) 


@app.route('/start', methods=['POST'])
def start():
    global peers, peer_ready
    data = request.json
    peers = data.get('peers', peers)  # Use existing peers if not provided
    
    # Initialize peer_ready dictionary
    for peer in peers:
        if peer not in peer_ready:
            peer_ready[peer] = False

    timeout = time.time() + 60  # 60 second timeout
    while not all(peer_ready.get(peer, False) for peer in peers):
        if time.time() > timeout:
            return jsonify({"status": "Timeout waiting for peers"}), 408
        time.sleep(0.1)
    simulation_started.set()
    return jsonify({"status": "Simulation started"})

start_time = None

# remove this in the realTime version
@app.route('/sync_start', methods=['POST'])
def sync_start():
    global start_time, peers
    data = request.json
    start_time = data.get('start_time')
    peers = data.get('peers', [])
    if start_time and peers:
        logging.info(f"Sync start received. Start time: {start_time}, Peers: {peers}")
        return jsonify({"status": "start time and peers set", "start_time": start_time, "peers": peers})
    else:
        logging.warning("Invalid start time or peers in sync_start request")
        return jsonify({"error": "Invalid start time or peers"}), 400

simulation_start_time = None

@app.route('/start_simulation', methods=['POST'])
def start_simulation():
    global simulation_start_time
    data = request.json
    simulation_start_time = data.get('start_time')
    if simulation_start_time:
        simulation_started.set()
        return jsonify({"status": "Simulation started"})
    else:
        return jsonify({"error": "Invalid start time"}), 400

@app.route('/get_data', methods=['GET'])
def get_data():
    global energy_data
    try:
        return jsonify(energy_data)
    except Exception as e:
        logging.error(f"Error getting data: {e}")
        return jsonify({"error": str(e)}), 400

@app.route('/get_peer_data', methods=['GET'])
def get_peer_data():
    return jsonify(peer_data)

@app.route('/wait_for_start', methods=['GET'])
def wait_for_start():
    if simulation_started.wait(timeout=30):  # Wait up to 30 seconds
        return jsonify({"status": "Simulation started"})
    else:
        return jsonify({"status": "Timeout waiting for simulation to start"}), 408

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)

