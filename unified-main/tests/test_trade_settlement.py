# Branch: unified-main
# File: tests/test_trade_settlement.py
"""End-to-end trade settlement between two in-process TradingManagers.

The "network" is a fake: the seller's notify_trade_completion calls straight into
the buyer's handle_trade_completion (as the Flask /trade/completion route would).
"""

import asyncio
import unittest
from datetime import datetime
from unittest.mock import Mock

from core.config import ConfigManager
from core.device_types import PiDevice
from core.trade_types import TradeMatch, TradeRequest
from network.network_manager import NetworkManager
from simulation.trading_manager import TradingManager

GRID_BUY, GRID_SELL = 0.25, 0.05


def make_manager(device: PiDevice) -> TradingManager:
    config = Mock(spec=ConfigManager)
    config.get_local_device.return_value = device
    config.grid_buy_price = GRID_BUY
    config.grid_sell_price = GRID_SELL
    manager = TradingManager(config, Mock(spec=NetworkManager))
    manager.trading_integration = Mock()
    return manager


class TestTradeSettlement(unittest.TestCase):
    def setUp(self):
        self.seller_dev = PiDevice("pi1", "10.0.0.1", True, "prosumer-pi-1")
        self.buyer_dev = PiDevice("pi2", "10.0.0.2", False, "consumer-pi-1")
        self.seller = make_manager(self.seller_dev)
        self.buyer = make_manager(self.buyer_dev)
        self.peer_reachable = True

        async def notify(peer_id, match_id, match):
            if not self.peer_reachable:
                return False
            # round-trip through the wire format like the real server does
            return self.buyer.handle_trade_completion(match_id, TradeMatch.from_dict(match.to_dict())) or True

        self.seller.trading_integration.notify_trade_completion = notify

    def _post(self, offer_amount=1.0, request_amount=0.4):
        """Seller offers, buyer requests; the seller's node holds a copy of the request."""
        async def go():
            await self.seller.create_offer(offer_amount, ttl=30)
            request_id = await self.buyer.create_request(request_amount, ttl=30)
            request = self.buyer.active_requests[request_id]
            # as if received over the network (the real route deserialises a copy)
            self.seller.active_requests[request_id] = TradeRequest.from_dict(request.to_dict())
            return request_id
        return asyncio.run(go())

    def _match_and_process(self):
        async def go():
            await self.seller._match_trades()
            await self.seller._process_matched_trades()
        asyncio.run(go())

    def test_trade_moves_energy_and_money_on_both_sides(self):
        self._post(offer_amount=1.0, request_amount=0.4)
        self._match_and_process()

        price = GRID_SELL * 1.1  # default offer price
        self.assertAlmostEqual(self.seller.energy_sold, 0.4)
        self.assertAlmostEqual(self.buyer.energy_bought, 0.4)
        self.assertAlmostEqual(self.seller.currency, 100 + 0.4 * price)
        self.assertAlmostEqual(self.buyer.currency, 100 - 0.4 * price)
        # money is conserved across the two devices
        self.assertAlmostEqual(self.seller.currency + self.buyer.currency, 200.0)

    def test_fully_matched_request_is_not_matched_again(self):
        self._post(offer_amount=1.0, request_amount=0.4)
        for _ in range(5):  # the real loop re-runs matching every 0.1s
            self._match_and_process()

        self.assertEqual(len(self.seller.completed_trades), 1)
        self.assertAlmostEqual(self.seller.energy_sold, 0.4)
        # the offer keeps only what is left
        self.assertAlmostEqual(next(iter(self.seller.active_offers.values())).amount, 0.6)
        self.assertEqual(self.seller.active_requests, {})

    def test_unreachable_buyer_leaves_ledgers_untouched(self):
        self.peer_reachable = False
        self._post()
        self._match_and_process()

        self.assertEqual(self.seller.energy_sold, 0.0)
        self.assertEqual(self.seller.currency, 100.0)
        self.assertEqual(self.buyer.currency, 100.0)
        self.assertEqual(self.seller.completed_trades, [])

        # and the pair is backed off rather than retried every 0.1s
        attempts = []

        async def counting_notify(peer_id, match_id, match):
            attempts.append(match_id)
            return False

        self.seller.trading_integration.notify_trade_completion = counting_notify
        self._match_and_process()
        self.assertEqual(attempts, [])

    def test_completion_notice_is_idempotent(self):
        self._post()
        self._match_and_process()
        match_id, match = next(iter(self.seller.trade_matches.items()))
        before = self.buyer.currency
        self.assertFalse(self.buyer.handle_trade_completion(match_id, match))
        self.assertEqual(self.buyer.currency, before)

    def test_third_party_ignores_trade(self):
        other = make_manager(PiDevice("pi3", "10.0.0.3", False, "consumer-pi-2"))
        match = TradeMatch("o", "r", "pi1", "pi2", 0.5, 0.05)
        self.assertFalse(other.apply_trade("m1", match))
        self.assertEqual(other.currency, 100.0)

    def test_expired_request_is_not_matched(self):
        async def go():
            await self.seller.create_offer(1.0, ttl=30)
            rid = await self.buyer.create_request(0.4, ttl=-1)  # already lapsed
            self.seller.active_requests[rid] = self.buyer.active_requests[rid]
            await self.seller._match_trades()
        asyncio.run(go())
        self.assertEqual(self.seller.trade_matches, {})

    def test_request_expiry_survives_serialisation(self):
        async def go():
            return await self.buyer.create_request(0.4, ttl=10)
        rid = asyncio.run(go())
        req = self.buyer.active_requests[rid]
        self.assertEqual(TradeRequest.from_dict(req.to_dict()).expiry, req.expiry)
        old = TradeRequest(datetime.now(), "pi2", 1.0, 0.2).to_dict()
        self.assertNotIn("expiry", old)
        self.assertIsNone(TradeRequest.from_dict(old).expiry)


class TestGridSettlement(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager(PiDevice("pi1", "10.0.0.1", True, "prosumer-pi-1"))

    def test_unsold_surplus_goes_to_grid(self):
        s = self.manager.settle_interval(surplus=0.5)
        self.assertAlmostEqual(s["grid_sold"], 0.5)
        self.assertAlmostEqual(s["currency"], 100 + 0.5 * GRID_SELL)

    def test_unmet_deficit_is_bought_from_grid(self):
        s = self.manager.settle_interval(deficit=0.2)
        self.assertAlmostEqual(s["grid_bought"], 0.2)
        self.assertAlmostEqual(s["currency"], 100 - 0.2 * GRID_BUY)

    def test_peer_trades_reduce_what_the_grid_covers(self):
        self.manager.apply_trade("m1", TradeMatch("o", "r", "pi1", "pi2", 0.3, 0.055))
        s = self.manager.settle_interval(surplus=0.5)
        self.assertAlmostEqual(s["p2p_sold"], 0.3)
        self.assertAlmostEqual(s["grid_sold"], 0.2)
        # counters reset for the next interval
        self.assertAlmostEqual(self.manager.settle_interval(surplus=0.1)["p2p_sold"], 0.0)


if __name__ == "__main__":
    unittest.main()
