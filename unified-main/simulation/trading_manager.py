# Branch: unified-main
# File: trading_manager.py
"""Trading manager for SolarVille system.
Handles energy trading between prosumers and consumers with a queue-based approach.
"""

import logging
import asyncio
import itertools
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

from core.config import ConfigManager
from core.device_types import PiDevice
from core.energy_types import EnergyReading, ProsumerReading
from core.trade_types import TradeOffer, TradeRequest, TradeMatch
from network.network_manager import NetworkManager
from network.trading_integration import TradingIntegration
from simulation.trade_verification import TradeVerificationSystem

INITIAL_CURRENCY = 100.0       # £ each device starts with
AMOUNT_EPSILON = 1e-9          # kWh below which an offer/request counts as used up
RETRY_BACKOFF_SECONDS = 2.0    # wait before retrying a pair whose counterparty was unreachable

class TradingManager:
    """
    Manages energy trading between devices in the SolarVille network.
    Implements a queue-based processing system for reliable trade execution.
    """
    
    def __init__(self, config_manager: ConfigManager, network_manager: NetworkManager):
        """
        Initialize the Trading Manager.
        
        Args:
            config_manager: Configuration manager
            network_manager: Network manager for communication
        """
        self.config = config_manager
        self.network = network_manager
        self.logger = logging.getLogger(__name__)
        
        # Device info
        self.local_device = self.config.get_local_device()
        self.is_prosumer = self.local_device.is_prosumer if self.local_device else False
        
        # Trade storage
        self.active_offers = {}     # id -> TradeOffer
        self.active_requests = {}   # id -> TradeRequest
        self.trade_matches = {}     # id -> TradeMatch
        self.completed_trades = []  # List of completed TradeMatch objects
        
        # Trade processing queues
        self.offer_queue = asyncio.Queue()    # Queue of new offers
        self.request_queue = asyncio.Queue()  # Queue of new requests  
        self.match_queue = asyncio.Queue()    # Queue of matched trades to process
        
        # Integration with other components
        self.trading_integration = TradingIntegration(self.network)
        self.verification_system = TradeVerificationSystem(self)
        
        # Ledger: what this device has bought/sold and what it has in the bank.
        # Updated from the trade loop thread, the Flask thread and the main loop.
        self._lock = threading.RLock()
        self.currency = INITIAL_CURRENCY          # £
        self.energy_sold = 0.0                    # kWh sold peer-to-peer (lifetime)
        self.energy_bought = 0.0                  # kWh bought peer-to-peer (lifetime)
        self._interval_sold = 0.0                 # kWh sold since the last settle_interval()
        self._interval_bought = 0.0               # kWh bought since the last settle_interval()
        self._applied_matches = set()             # match ids already applied locally (idempotency)
        self._match_counter = itertools.count(1)
        self._retry_after = {}                    # (offer_id, request_id) -> monotonic time
        
        # Trade processing flags
        self.processing_active = False
        self.processing_task = None
        
        # Trading settings
        self.grid_buy_price = self.config.grid_buy_price
        self.grid_sell_price = self.config.grid_sell_price
        
        self.logger.info("Trading Manager initialized")
    
    async def start_processing(self):
        """Start the trade processing loops."""
        if self.processing_active:
            self.logger.warning("Trade processing already active")
            return
            
        self.processing_active = True
        self.processing_task = asyncio.create_task(self._process_trades())
        self.logger.info("Trade processing started")
        
    async def stop_processing(self):
        """Stop the trade processing loops."""
        if not self.processing_active:
            return
            
        self.processing_active = False
        if self.processing_task:
            await self.processing_task
            self.processing_task = None
        self.logger.info("Trade processing stopped")
        
    async def _process_trades(self):
        """Main trade processing loop."""
        try:
            while self.processing_active:
                # Process any new offers and requests
                await self._process_offers()
                await self._process_requests()
                
                # Match offers with requests
                await self._match_trades()
                
                # Process matched trades
                await self._process_matched_trades()
                
                # Short delay to avoid CPU spinning
                await asyncio.sleep(0.1)
        except Exception as e:
            self.logger.error(f"Error in trade processing loop: {e}", exc_info=True)
            self.processing_active = False
    
    async def create_offer(self, amount: float, min_price: float = None, ttl: float = 30.0):
        """
        Create a new offer to sell energy.
        
        Args:
            amount: Amount of energy to sell in kWh
            min_price: Minimum acceptable price per kWh
            ttl: Seconds before the offer expires
            
        Returns:
            Offer ID
        """
        if amount <= 0:
            raise ValueError("Offer amount must be positive")
            
        # Use default price if none specified
        if min_price is None:
            min_price = self.grid_sell_price * 1.1  # 10% above grid sell price
            
        # Create offer with expiration
        expiry = datetime.now() + timedelta(seconds=ttl)
        
        offer = TradeOffer(
            timestamp=datetime.now(),
            seller_id=self.local_device.name,
            amount=amount,
            min_price=min_price,
            expiry=expiry
        )
        
        # Generate unique ID
        offer_id = f"offer_{offer.seller_id}_{int(offer.timestamp.timestamp() * 1000)}"
        self.active_offers[offer_id] = offer
        
        # Queue for processing
        await self.offer_queue.put((offer_id, offer))
        
        self.logger.info(f"Created offer {offer_id}: {amount} kWh at £{min_price}/kWh")
        return offer_id
        
    async def create_request(self, amount: float, max_price: float = None, priority: int = 0,
                             ttl: float = None):
        """
        Create a new request to buy energy.
        
        Args:
            amount: Amount of energy to buy in kWh
            max_price: Maximum acceptable price per kWh
            priority: Priority level (higher = more urgent)
            ttl: Seconds before the request lapses (None = never)
            
        Returns:
            Request ID
        """
        if amount <= 0:
            raise ValueError("Request amount must be positive")
            
        # Use default price if none specified
        if max_price is None:
            max_price = self.grid_buy_price * 0.9  # 10% below grid buy price
            
        request = TradeRequest(
            timestamp=datetime.now(),
            buyer_id=self.local_device.name,
            amount=amount,
            max_price=max_price,
            priority=priority,
            expiry=datetime.now() + timedelta(seconds=ttl) if ttl is not None else None
        )
        
        # Generate unique ID
        request_id = f"request_{request.buyer_id}_{int(request.timestamp.timestamp() * 1000)}"
        self.active_requests[request_id] = request
        
        # Queue for processing
        await self.request_queue.put((request_id, request))
        
        self.logger.info(f"Created request {request_id}: {amount} kWh at max £{max_price}/kWh")
        return request_id
        
    async def handle_peer_offer(self, offer_id: str, offer: TradeOffer):
        """
        Handle an offer received from a peer.
        
        Args:
            offer_id: Offer ID
            offer: Trade offer object
        """
        # Store the offer
        self.active_offers[offer_id] = offer
        
        # Queue for processing
        await self.offer_queue.put((offer_id, offer))
        
        self.logger.debug(f"Received peer offer {offer_id}: {offer.amount} kWh at £{offer.min_price}/kWh")
        
    async def handle_peer_request(self, request_id: str, request: TradeRequest):
        """
        Handle a request received from a peer.
        
        Args:
            request_id: Request ID
            request: Trade request object
        """
        # Store the request
        self.active_requests[request_id] = request
        
        # Queue for processing
        await self.request_queue.put((request_id, request))
        
        self.logger.debug(f"Received peer request {request_id}: {request.amount} kWh at £{request.max_price}/kWh")
    
    async def _process_offers(self):
        """Process new offers in the queue."""
        try:
            while not self.offer_queue.empty():
                offer_id, offer = await self.offer_queue.get()
                
                # Skip expired offers
                if datetime.now() > offer.expiry:
                    self.logger.debug(f"Skipping expired offer {offer_id}")
                    self.offer_queue.task_done()
                    continue
                    
                # Publish offer to network if it's our offer
                if self.local_device.name == offer.seller_id:
                    await self.trading_integration.publish_offer(offer_id, offer)
                    
                self.offer_queue.task_done()
        except Exception as e:
            self.logger.error(f"Error processing offers: {e}")
    
    async def _process_requests(self):
        """Process new requests in the queue."""
        try:
            while not self.request_queue.empty():
                request_id, request = await self.request_queue.get()
                
                # Publish request to network if it's our request
                if self.local_device.name == request.buyer_id:
                    await self.trading_integration.publish_request(request_id, request)
                    
                self.request_queue.task_done()
        except Exception as e:
            self.logger.error(f"Error processing requests: {e}")
    
    async def _match_trades(self):
        """Match offers with requests."""
        try:
            # Skip if we are not a prosumer (only prosumers match trades)
            if not self.is_prosumer:
                return
                
            # Drop anything that has lapsed or been fully traded
            self._prune_expired()

            # Get valid offers and requests, net of matches that are still pending
            reserved_offer, reserved_request = self._pending_reservations()
            valid_offers = {}
            for oid, o in list(self.active_offers.items()):
                remaining = o.amount - reserved_offer.get(oid, 0.0)
                if remaining > AMOUNT_EPSILON:
                    valid_offers[oid] = TradeOffer(o.timestamp, o.seller_id, remaining, o.min_price, o.expiry)
            valid_requests = {}
            for rid, r in list(self.active_requests.items()):
                remaining = r.amount - reserved_request.get(rid, 0.0)
                if remaining > AMOUNT_EPSILON:
                    valid_requests[rid] = TradeRequest(r.timestamp, r.buyer_id, remaining, r.max_price,
                                                       r.priority, r.expiry)
            
            # Sort requests by priority (highest first)
            sorted_requests = sorted(
                valid_requests.items(),
                key=lambda x: (x[1].priority, x[1].timestamp),
                reverse=True  # Higher priority and older requests first
            )
            
            matched_trades = []
            
            # For each request, find matching offers
            for request_id, request in sorted_requests:
                remaining_amount = request.amount
                request_matches = []
                
                # Sort offers by price (lowest first)
                sorted_offers = sorted(
                    valid_offers.items(),
                    key=lambda x: (x[1].min_price, -x[1].amount)  # Lowest price, highest amount
                )
                
                for offer_id, offer in sorted_offers:
                    # Skip if price doesn't match
                    if offer.min_price > request.max_price:
                        continue
                        
                    # Skip self-trades
                    if offer.seller_id == request.buyer_id:
                        continue

                    # Skip pairs that recently failed to execute (e.g. peer unreachable)
                    if self._retry_after.get((offer_id, request_id), 0) > time.monotonic():
                        continue
                        
                    # Calculate trade amount
                    trade_amount = min(remaining_amount, offer.amount)
                    if trade_amount <= AMOUNT_EPSILON:
                        continue
                        
                    # Calculate price (for now, just use offer price)
                    trade_price = offer.min_price
                    
                    # Create trade match
                    trade_match = TradeMatch(
                        offer_id=offer_id,
                        request_id=request_id,
                        seller_id=offer.seller_id,
                        buyer_id=request.buyer_id,
                        amount=trade_amount,
                        price=trade_price
                    )
                    
                    # Generate unique ID
                    match_id = (f"match_{trade_match.seller_id}_{trade_match.buyer_id}_"
                                f"{int(trade_match.created_at.timestamp())}_{next(self._match_counter)}")
                    
                    # Add to matches
                    request_matches.append((match_id, trade_match))
                    
                    # Update remaining amount
                    remaining_amount -= trade_amount
                    
                    # Update available offer amount
                    valid_offers[offer_id] = TradeOffer(
                        timestamp=offer.timestamp,
                        seller_id=offer.seller_id,
                        amount=offer.amount - trade_amount,
                        min_price=offer.min_price,
                        expiry=offer.expiry
                    )
                    
                    # If request is fully matched, break
                    if remaining_amount <= AMOUNT_EPSILON:
                        break
                
                # Add all matches for this request
                matched_trades.extend(request_matches)
            
            # Queue matched trades for processing
            for match_id, trade_match in matched_trades:
                self.trade_matches[match_id] = trade_match
                await self.match_queue.put((match_id, trade_match))
                
            if matched_trades:
                self.logger.info(f"Matched {len(matched_trades)} trades")
                
        except Exception as e:
            self.logger.error(f"Error matching trades: {e}")
    
    async def _process_matched_trades(self):
        """Process matched trades in the queue."""
        try:
            while not self.match_queue.empty():
                match_id, trade_match = await self.match_queue.get()
                
                # Check if we're involved in this trade
                is_seller = trade_match.seller_id == self.local_device.name
                is_buyer = trade_match.buyer_id == self.local_device.name
                
                if not (is_seller or is_buyer):
                    # We're not involved, skip
                    self.match_queue.task_done()
                    continue
                
                # Verify the trade
                is_valid = await self.verification_system.verify_trade(match_id, trade_match)
                
                if not is_valid:
                    self.logger.warning(f"Trade {match_id} failed verification")
                    trade_match.status = "failed"
                    self.trade_matches[match_id] = trade_match
                    self.match_queue.task_done()
                    continue
                
                # Execute the trade
                success = await self._execute_trade(match_id, trade_match)
                
                if success:
                    # Update trade status
                    trade_match.status = "completed"
                    trade_match.updated_at = datetime.now()
                    self.trade_matches[match_id] = trade_match
                    
                    # Move to completed trades
                    self.completed_trades.append(trade_match)
                    
                    # Log success
                    self.logger.info(
                        f"Completed trade {match_id}: {trade_match.amount} kWh "
                        f"from {trade_match.seller_id} to {trade_match.buyer_id} "
                        f"at £{trade_match.price}/kWh"
                    )
                else:
                    # Update trade status
                    trade_match.status = "failed"
                    trade_match.updated_at = datetime.now()
                    self.trade_matches[match_id] = trade_match
                    
                    # Log failure
                    self.logger.warning(f"Failed to execute trade {match_id}")
                
                self.match_queue.task_done()
                
        except Exception as e:
            self.logger.error(f"Error processing matched trades: {e}")
    
    async def _execute_trade(self, match_id: str, trade_match: TradeMatch) -> bool:
        """
        Execute a matched trade: tell the counterparty, then settle our side.

        The counterparty applies its own side when it receives the notification
        (see handle_trade_completion). If it can't be reached the trade fails and
        nothing changes locally, so the two ledgers never disagree.

        Args:
            match_id: Match ID
            trade_match: Trade match object

        Returns:
            True if trade was executed successfully
        """
        try:
            is_seller = trade_match.seller_id == self.local_device.name
            is_buyer = trade_match.buyer_id == self.local_device.name

            if is_seller:
                counterparty = trade_match.buyer_id
            elif is_buyer:
                counterparty = trade_match.seller_id
            else:
                return False

            notified = await self.trading_integration.notify_trade_completion(
                counterparty, match_id, trade_match
            )
            if not notified:
                # Back off this offer/request pair so the matcher doesn't hammer a dead peer
                self._retry_after[(trade_match.offer_id, trade_match.request_id)] = (
                    time.monotonic() + RETRY_BACKOFF_SECONDS
                )
                self.logger.warning(f"Could not notify {counterparty} of trade {match_id}; trade not executed")
                return False

            self.apply_trade(match_id, trade_match)
            return True

        except Exception as e:
            self.logger.error(f"Error executing trade {match_id}: {e}")
            return False

    def apply_trade(self, match_id: str, trade_match: TradeMatch) -> bool:
        """
        Apply the local side of a trade: move energy and money, and use up the
        matched amount on any offer/request we hold. Idempotent per match id.

        Returns:
            True if applied, False if already applied or we're not a party to it.
        """
        name = self.local_device.name if self.local_device else None
        is_seller = trade_match.seller_id == name
        is_buyer = trade_match.buyer_id == name
        if not (is_seller or is_buyer):
            return False

        value = trade_match.amount * trade_match.price
        with self._lock:
            if match_id in self._applied_matches:
                return False
            self._applied_matches.add(match_id)

            if is_seller:
                self.energy_sold += trade_match.amount
                self._interval_sold += trade_match.amount
                self.currency += value
            if is_buyer:
                self.energy_bought += trade_match.amount
                self._interval_bought += trade_match.amount
                self.currency -= value

            offer = self.active_offers.get(trade_match.offer_id)
            if offer:
                offer.amount -= trade_match.amount
            request = self.active_requests.get(trade_match.request_id)
            if request:
                request.amount -= trade_match.amount
            self._prune_expired()

        self.logger.info(
            f"Trade {match_id}: {'sold' if is_seller else 'bought'} {trade_match.amount:.3f} kWh "
            f"at £{trade_match.price:.3f}/kWh (balance £{self.currency:.2f})"
        )
        return True

    def handle_trade_completion(self, match_id: str, trade_match: TradeMatch) -> bool:
        """Handle a completion notice from the counterparty (called by the server)."""
        trade_match.status = "completed"
        trade_match.updated_at = datetime.now()
        applied = self.apply_trade(match_id, trade_match)
        if applied:
            self.trade_matches[match_id] = trade_match
            self.completed_trades.append(trade_match)
        return applied

    def settle_interval(self, surplus: float = 0.0, deficit: float = 0.0) -> Dict[str, float]:
        """
        Close out one simulated interval. Whatever surplus wasn't sold to peers is
        sold to the grid, and whatever deficit wasn't bought from peers is bought
        from the grid.

        Args:
            surplus: kWh this device had spare this interval (>= 0)
            deficit: kWh this device was short this interval (>= 0)

        Returns:
            Summary: p2p_sold, p2p_bought, grid_sold, grid_bought (kWh),
            cash_delta (£, including peer trades), currency (£ balance)
        """
        with self._lock:
            p2p_sold, p2p_bought = self._interval_sold, self._interval_bought
            self._interval_sold = self._interval_bought = 0.0

            grid_sold = max(surplus - p2p_sold, 0.0)
            grid_bought = max(deficit - p2p_bought, 0.0)
            grid_cash = grid_sold * self.grid_sell_price - grid_bought * self.grid_buy_price
            self.currency += grid_cash

            return {
                "p2p_sold": p2p_sold,
                "p2p_bought": p2p_bought,
                "grid_sold": grid_sold,
                "grid_bought": grid_bought,
                "grid_cash": grid_cash,
                "currency": self.currency,
            }

    def _pending_reservations(self):
        """Amounts already tied up in matches that haven't completed or failed yet."""
        reserved_offer, reserved_request = {}, {}
        for m in list(self.trade_matches.values()):
            if m.status == "pending":
                reserved_offer[m.offer_id] = reserved_offer.get(m.offer_id, 0.0) + m.amount
                reserved_request[m.request_id] = reserved_request.get(m.request_id, 0.0) + m.amount
        return reserved_offer, reserved_request

    def _prune_expired(self):
        """Remove offers/requests that expired or have been fully traded."""
        now = datetime.now()
        for oid, o in list(self.active_offers.items()):
            if now > o.expiry or o.amount <= AMOUNT_EPSILON:
                self.active_offers.pop(oid, None)
        for rid, r in list(self.active_requests.items()):
            if (r.expiry is not None and now > r.expiry) or r.amount <= AMOUNT_EPSILON:
                self.active_requests.pop(rid, None)

    def get_trade_status(self) -> Dict[str, Any]:
        """
        Get current trading status.
        
        Returns:
            Dictionary with trading status information
        """
        return {
            "active_offers": len(self.active_offers),
            "active_requests": len(self.active_requests),
            "pending_matches": len([m for m in self.trade_matches.values() if m.status == "pending"]),
            "completed_trades": len(self.completed_trades),
            "is_processing": self.processing_active
        }