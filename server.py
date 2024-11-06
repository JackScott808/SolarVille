# server.py
from flask import Flask, request, jsonify
import logging
from threading import Event
import os

app = Flask(__name__)

# Global state storage
peer_data = {}
current_timestamp = None
simulation_ended = Event()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint"""
    return jsonify({"status": "healthy"})

@app.route('/get_peer_data', methods=['GET'])
def get_peer_data():
    """
    Endpoint for getting peer's current status
    """
    try:
        return jsonify(peer_data)
    except Exception as e:
        logging.error(f"Error serving peer data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/update_peer_data', methods=['POST'])
def update_peer_data():
    """
    Endpoint for receiving peer's current status
    """
    try:
        data = request.json
        peer_ip = request.remote_addr
        
        if peer_ip not in peer_data:
            peer_data[peer_ip] = {}
        peer_data[peer_ip].update(data)
        
        logging.info(f"Updated peer data from {peer_ip}: "
                    f"Demand: {data.get('demand', 'N/A')}kWh, "
                    f"Balance: {data.get('balance', 'N/A')}kWh")
        
        return jsonify({"status": "updated"})
    except Exception as e:
        logging.error(f"Error updating peer data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/update_trade_data', methods=['POST'])
def update_trade_data():
    """
    Endpoint for receiving trade information
    """
    try:
        data = request.json
        peer_ip = request.remote_addr
        
        if peer_ip not in peer_data:
            peer_data[peer_ip] = {}
        peer_data[peer_ip].update(data)
        
        logging.info(
            f"Trade data from {peer_ip}: "
            f"Amount: {data.get('trade_amount', 'N/A')} kWh, "
            f"Price: £{data.get('price', 'N/A')}/kWh"
        )
        
        return jsonify({"status": "updated"})
    except Exception as e:
        logging.error(f"Error updating trade data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/sync', methods=['POST'])
def sync():
    """
    Endpoint for synchronizing timestamps between peers
    """
    global current_timestamp
    try:
        data = request.json
        current_timestamp = data['timestamp']
        
        if current_timestamp == 'END':
            simulation_ended.set()
            logging.info("Received END signal. Simulation completed.")
        elif current_timestamp == 'START':
            logging.info("Received START signal. Simulation beginning.")
        else:
            logging.info(f"Synced to timestamp: {current_timestamp}")
        
        return jsonify({"status": "synced"})
    except Exception as e:
        logging.error(f"Error syncing timestamp: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Get port from environment variable or use default
    port = int(os.environ.get('FLASK_PORT', 5000))
    logging.info(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)