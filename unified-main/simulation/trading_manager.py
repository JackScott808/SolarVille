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
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional, Any

from core.config import ConfigManager
from core.device_types import PiDevice
from core.energy_types import EnergyReading, ProsumerReading
from core.tariff import Tariff
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
        # Peer-to-peer trades by the simulated interval they belong to (not by when they arrived), so two
        # nodes whose clocks differ slightly still agree on which interval a trade was in.
        self._interval_trades = {}                # interval -> {sold, bought, sold_cash, bought_cash}
        self._settled = {}                        # interval -> grid amounts/prices used when it was settled
        self._applied_matches = set()             # match ids already applied locally (idempotency)
        self._match_counter = itertools.count(1)
        self._retry_after = {}                    # (offer_id, request_id) -> monotonic time
        
        # Trade processing flags
        self.processing_active = False
        self.processing_task = None
        
        # Trading settings
        self.grid_buy_price = self.config.grid_buy_price
        self.grid_sell_price = self.config.grid_sell_price
        configured = getattr(self.config, "tariff", None)
        self.tariff = configured if isinstance(configured, Tariff) else Tariff.flat(
            self.grid_buy_price, self.grid_sell_price)
        
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
    
    def _interval_time(self, interval: Optional[str]) -> datetime:
        """The simulated time an interval stamp refers to (now, in UTC, if there is none)."""
        if interval:
            try:
                return datetime.fromisoformat(interval)
            except ValueError:
                self.logger.warning(f"Unreadable interval stamp '{interval}'; using the current time")
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def grid_prices(self, interval: Optional[str] = None):
        """(export price, import price) in £/kWh that the grid offers for an interval."""
        ts = self._interval_time(interval)
        return self.tariff.export_price_at(ts), self.tariff.import_price(ts)

    async def create_offer(self, amount: float, min_price: float = None, ttl: float = 30.0,
                           interval: str = None):
        """
        Create a new offer to sell energy.
        
        Args:
            amount: Amount of energy to sell in kWh
            min_price: Minimum acceptable price per kWh. Defaults to the grid export price: selling
                for less than that would be pointless, since the grid pays it.
            ttl: Seconds before the offer expires
            interval: Simulated interval the energy belongs to; offers only trade with requests for
                the same interval
            
        Returns:
            Offer ID
        """
        if amount <= 0:
            raise ValueError("Offer amount must be positive")
            
        if min_price is None:
            min_price = self.grid_prices(interval)[0]
            
        # Create offer with expiration
        expiry = datetime.now() + timedelta(seconds=ttl)
        
        offer = TradeOffer(
            timestamp=datetime.now(),
            seller_id=self.local_device.name,
            amount=amount,
            min_price=min_price,
            expiry=expiry,
            interval=interval
        )
        
        # Generate unique ID
        offer_id = f"offer_{offer.seller_id}_{int(offer.timestamp.timestamp() * 1000)}_{next(self._match_counter)}"
        self.active_offers[offer_id] = offer
        
        # Queue for processing
        await self.offer_queue.put((offer_id, offer))
        
        self.logger.info(f"Created offer {offer_id}: {amount:.3f} kWh at no less than £{min_price:.3f}/kWh")
        return offer_id
        
    async def create_request(self, amount: float, max_price: float = None, priority: int = 0,
                             ttl: float = None, interval: str = None):
        """
        Create a new request to buy energy.
        
        Args:
            amount: Amount of energy to buy in kWh
            max_price: Maximum acceptable price per kWh. Defaults to the grid import price at that
                time: paying more than that would be pointless, since the grid sells at it.
            priority: Priority level (higher = more urgent)
            ttl: Seconds before the request lapses (None = never)
            interval: Simulated interval the energy is needed in
            
        Returns:
            Request ID
        """
        if amount <= 0:
            raise ValueError("Request amount must be positive")
            
        if max_price is None:
            max_price = self.grid_prices(interval)[1]
            
        request = TradeRequest(
            timestamp=datetime.now(),
            buyer_id=self.local_device.name,
            amount=amount,
            max_price=max_price,
            priority=priority,
            expiry=datetime.now() + timedelta(seconds=ttl) if ttl is not None else None,
            interval=interval
        )
        
        # Generate unique ID
        request_id = (f"request_{request.buyer_id}_{int(request.timestamp.timestamp() * 1000)}_"
                      f"{next(self._match_counter)}")
        self.active_requests[request_id] = request
        
        # Queue for processing
        await self.request_queue.put((request_id, request))
        
        self.logger.info(f"Created request {request_id}: {amount:.3f} kWh at no more than £{max_price:.3f}/kWh")
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
        """Match offers with requests, interval by interval."""
        try:
            # Skip if we are not a prosumer (only prosumers match trades)
            if not self.is_prosumer:
                return
                
            # Drop anything that has lapsed or been fully traded
            self._prune_expired()

            # Group what is still available (net of matches that are still pending) by interval:
            # energy can only be traded within the interval it belongs to.
            reserved_offer, reserved_request = self._pending_reservations()
            offers, requests = {}, {}
            for oid, o in list(self.active_offers.items()):
                remaining = o.amount - reserved_offer.get(oid, 0.0)
                if remaining > AMOUNT_EPSILON:
                    offers.setdefault(o.interval, {})[oid] = TradeOffer(
                        o.timestamp, o.seller_id, remaining, o.min_price, o.expiry, o.interval)
            for rid, r in list(self.active_requests.items()):
                remaining = r.amount - reserved_request.get(rid, 0.0)
                if remaining > AMOUNT_EPSILON:
                    requests.setdefault(r.interval, {})[rid] = TradeRequest(
                        r.timestamp, r.buyer_id, remaining, r.max_price, r.priority, r.expiry, r.interval)

            matched_trades = []
            for interval, interval_offers in offers.items():
                if interval in requests:
                    matched_trades.extend(self._match_interval(interval, interval_offers, requests[interval]))
            
            # Queue matched trades for processing
            for match_id, trade_match in matched_trades:
                self.trade_matches[match_id] = trade_match
                await self.match_queue.put((match_id, trade_match))
                
            if matched_trades:
                self.logger.info(f"Matched {len(matched_trades)} trades")
                
        except Exception as e:
            self.logger.error(f"Error matching trades: {e}")

    def _match_interval(self, interval, valid_offers: Dict[str, TradeOffer],
                        valid_requests: Dict[str, TradeRequest]):
        """Match one interval's offers and requests at that interval's market price.

        The market price comes from how scarce local supply is (see Tariff.p2p_price). Each trade
        then respects both parties' limits: it is never below the seller's ask or above the
        buyer's bid.
        """
        supply = sum(o.amount for o in valid_offers.values())
        demand = sum(r.amount for r in valid_requests.values())
        market_price = self.tariff.p2p_price(supply, demand, self._interval_time(interval))

        # Sort requests by priority (highest first, older first)
        sorted_requests = sorted(valid_requests.items(), key=lambda x: (x[1].priority, x[1].timestamp),
                                 reverse=True)
        matches = []
        for request_id, request in sorted_requests:
            remaining_amount = request.amount

            # Cheapest, then largest, offers first
            sorted_offers = sorted(valid_offers.items(), key=lambda x: (x[1].min_price, -x[1].amount))
            for offer_id, offer in sorted_offers:
                if offer.min_price > request.max_price:
                    continue  # no price both would accept
                if offer.seller_id == request.buyer_id:
                    continue  # no self-trades
                # Skip pairs that recently failed to execute (e.g. peer unreachable)
                if self._retry_after.get((offer_id, request_id), 0) > time.monotonic():
                    continue

                trade_amount = min(remaining_amount, offer.amount)
                if trade_amount <= AMOUNT_EPSILON:
                    continue

                trade_price = min(max(market_price, offer.min_price), request.max_price)
                trade_match = TradeMatch(
                    offer_id=offer_id,
                    request_id=request_id,
                    seller_id=offer.seller_id,
                    buyer_id=request.buyer_id,
                    amount=trade_amount,
                    price=trade_price,
                    interval=interval
                )
                match_id = (f"match_{trade_match.seller_id}_{trade_match.buyer_id}_"
                            f"{int(trade_match.created_at.timestamp())}_{next(self._match_counter)}")
                matches.append((match_id, trade_match))

                remaining_amount -= trade_amount
                valid_offers[offer_id] = TradeOffer(offer.timestamp, offer.seller_id, offer.amount - trade_amount,
                                                    offer.min_price, offer.expiry, offer.interval)
                if remaining_amount <= AMOUNT_EPSILON:
                    break
        return matches
    
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

        The trade is booked against the interval it belongs to (trade_match.interval), not the
        moment it arrived. If that interval has already been settled with the grid, the grid
        transaction it replaces is reversed so the books stay right.

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

            record = self._interval_trades.setdefault(
                trade_match.interval, {"sold": 0.0, "bought": 0.0, "sold_cash": 0.0, "bought_cash": 0.0})
            if is_seller:
                self.energy_sold += trade_match.amount
                self.currency += value
                record["sold"] += trade_match.amount
                record["sold_cash"] += value
            if is_buyer:
                self.energy_bought += trade_match.amount
                self.currency -= value
                record["bought"] += trade_match.amount
                record["bought_cash"] += value

            settled = self._settled.get(trade_match.interval) if trade_match.interval is not None else None
            if settled is not None:
                # The interval was already closed out: this energy was wrongly sold to / bought from
                # the grid, so undo that part.
                if is_seller:
                    replaced = min(trade_match.amount, settled["grid_sold"])
                    self.currency -= replaced * settled["export"]
                    settled["grid_sold"] -= replaced
                if is_buyer:
                    replaced = min(trade_match.amount, settled["grid_bought"])
                    self.currency += replaced * settled["import"]
                    settled["grid_bought"] -= replaced
                self.logger.info(f"Trade {match_id} arrived after interval {trade_match.interval} was settled; "
                                 "grid transaction reversed")

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

    def settle_interval(self, surplus: float = 0.0, deficit: float = 0.0,
                        interval: str = None) -> Dict[str, Any]:
        """
        Close out one simulated interval. Whatever surplus wasn't sold to peers is sold to the
        grid at that time's export price, and whatever deficit wasn't bought from peers is bought
        from the grid at that time's import price.

        Args:
            surplus: kWh this device had spare this interval (>= 0)
            deficit: kWh this device was short this interval (>= 0)
            interval: The interval being settled (same stamp used on its offers/requests)

        Returns:
            Summary: p2p_sold, p2p_bought, grid_sold, grid_bought (kWh); p2p_price (average £/kWh of
            this interval's peer trades, None if there were none); import_price and export_price
            (£/kWh the grid charged/paid); grid_cash; and the new currency balance.
        """
        export_price, import_price = self.grid_prices(interval)
        with self._lock:
            record = self._interval_trades.pop(interval, None) or {
                "sold": 0.0, "bought": 0.0, "sold_cash": 0.0, "bought_cash": 0.0}
            p2p_sold, p2p_bought = record["sold"], record["bought"]

            grid_sold = max(surplus - p2p_sold, 0.0) + 0.0  # + 0.0 turns -0.0 into 0.0
            grid_bought = max(deficit - p2p_bought, 0.0) + 0.0
            grid_cash = grid_sold * export_price - grid_bought * import_price
            self.currency += grid_cash

            if interval is not None:
                # Trades for this interval that arrive later are corrected in apply_trade()
                self._settled[interval] = {"grid_sold": grid_sold, "grid_bought": grid_bought,
                                           "export": export_price, "import": import_price}

            if p2p_sold > 0:
                p2p_price = record["sold_cash"] / p2p_sold
            elif p2p_bought > 0:
                p2p_price = record["bought_cash"] / p2p_bought
            else:
                p2p_price = None

            return {
                "p2p_sold": p2p_sold,
                "p2p_bought": p2p_bought,
                "grid_sold": grid_sold,
                "grid_bought": grid_bought,
                "p2p_price": p2p_price,
                "import_price": import_price,
                "export_price": export_price,
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