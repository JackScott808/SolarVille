# Branch: consumerJack
# File: trading_integration.py

import requests
import logging
from typing import Optional, Dict
from energy_types import TradeData, EnergyReading
from config import PEER_IP

class TradingIntegration:
    """Handles communication between trading system and server"""
    
    def __init__(self, server_url: str = f"http://{PEER_IP}:5000"):
        self.server_url = server_url
        self.session = requests.Session()  # Use session for better performance

    def get_peer_data(self) -> Optional[Dict]:
        """Get current peer status from server"""
        try:
            response = self.session.get(f"{self.server_url}/get_peer_data")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logging.error(f"Failed to get peer data: {e}")
            return None

    def update_peer_data(self, reading: EnergyReading) -> bool:
        """Send current status to peer"""
        try:
            data = {
                'demand': reading.demand,
                'balance': reading.balance
            }
            
            # Add prosumer-specific data if available
            if hasattr(reading, 'generation'):
                data.update({
                    'generation': reading.generation,
                    'battery_soc': getattr(reading, 'battery_soc', None)
                })

            response = self.session.post(
                f"{self.server_url}/update_peer_data",
                json=data
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to update peer data: {e}")
            return False

    def update_trade_data(self, trade: TradeData) -> bool:
        """Send trade information to peer"""
        try:
            data = {
                'trade_amount': trade.amount,
                'price': trade.price,
                'grid_buy_price': trade.grid_buy_price,
                'grid_sell_price': trade.grid_sell_price
            }
            
            response = self.session.post(
                f"{self.server_url}/update_trade_data",
                json=data
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to update trade data: {e}")
            return False

    def sync_timestamp(self, timestamp: str) -> bool:
        """Synchronize timestamp with peer"""
        try:
            response = self.session.post(
                f"{self.server_url}/sync",
                json={'timestamp': timestamp}
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to sync timestamp: {e}")
            return False