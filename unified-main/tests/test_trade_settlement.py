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
from core.trade_types import TradeMatch, TradeOffer, TradeRequest
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

        # market price for supply 1.0 vs demand 0.4 on a flat 25p/5p tariff: plentiful supply, so cheap
        price = GRID_SELL + (GRID_BUY - GRID_SELL) * 0.4 / 1.4
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


INTERVAL = "2013-01-15T12:00:00"  # standard band: import 27p, export 8p on the default tariff


def make_tariff_manager(device: PiDevice) -> TradingManager:
    from core.tariff import Tariff
    config = Mock(spec=ConfigManager)
    config.get_local_device.return_value = device
    config.grid_buy_price, config.grid_sell_price = GRID_BUY, GRID_SELL
    config.tariff = Tariff()
    manager = TradingManager(config, Mock(spec=NetworkManager))
    manager.trading_integration = Mock()
    return manager


class TestIntervalStampedTrading(unittest.TestCase):
    """Trades belong to the simulated interval they were posted for, however late they arrive."""

    def setUp(self):
        self.seller_dev = PiDevice("pi1", "10.0.0.1", True, "prosumer-pi-1")
        self.buyer_dev = PiDevice("pi2", "10.0.0.2", False, "consumer-pi-1")
        self.seller = make_tariff_manager(self.seller_dev)
        self.buyer = make_tariff_manager(self.buyer_dev)

        async def notify(peer_id, match_id, match):
            return self.buyer.handle_trade_completion(match_id, TradeMatch.from_dict(match.to_dict())) or True
        self.seller.trading_integration.notify_trade_completion = notify

    def _run(self, offers, requests):
        """offers/requests: lists of (amount, interval[, price]). Returns after one match+execute pass."""
        async def go():
            for amount, interval, *price in offers:
                await self.seller.create_offer(amount, price[0] if price else None, ttl=30, interval=interval)
            for amount, interval, *price in requests:
                rid = await self.buyer.create_request(amount, price[0] if price else None, ttl=30, interval=interval)
                self.seller.active_requests[rid] = TradeRequest.from_dict(self.buyer.active_requests[rid].to_dict())
            await self.seller._match_trades()
            await self.seller._process_matched_trades()
        asyncio.run(go())

    def test_default_ask_and_bid_are_the_grid_export_and_import_prices(self):
        async def go():
            oid = await self.seller.create_offer(1.0, interval=INTERVAL)
            rid = await self.buyer.create_request(1.0, interval=INTERVAL)
            return oid, rid
        oid, rid = asyncio.run(go())
        self.assertEqual(self.seller.active_offers[oid].min_price, 0.08)   # what the grid would pay
        self.assertEqual(self.buyer.active_requests[rid].max_price, 0.27)  # what the grid would charge

    def test_peak_period_widens_the_asks_and_bids(self):
        peak = "2013-01-15T17:30:00"
        self.assertEqual(self.seller.grid_prices(peak), (0.08, 0.36))
        self.assertEqual(self.seller.grid_prices("2013-01-15T03:00:00"), (0.08, 0.12))

    def test_offers_only_trade_with_requests_for_the_same_interval(self):
        self._run([(1.0, "2013-01-15T12:00:00")], [(0.4, "2013-01-15T12:30:00")])
        self.assertEqual(self.seller.trade_matches, {})
        self.assertEqual(self.seller.energy_sold, 0.0)

    def test_same_interval_trades_and_is_booked_to_that_interval(self):
        self._run([(1.0, INTERVAL)], [(0.4, INTERVAL)])
        sell = self.seller.settle_interval(surplus=1.0, interval=INTERVAL)
        buy = self.buyer.settle_interval(deficit=0.4, interval=INTERVAL)
        self.assertAlmostEqual(sell["p2p_sold"], 0.4)
        self.assertAlmostEqual(buy["p2p_bought"], 0.4)       # both nodes agree on the interval
        self.assertAlmostEqual(sell["p2p_price"], buy["p2p_price"])
        self.assertAlmostEqual(sell["grid_sold"], 0.6)       # the rest of the surplus goes to the grid

    def test_trade_price_is_between_export_and_import_and_respects_scarcity(self):
        self._run([(1.0, INTERVAL)], [(0.1, INTERVAL)])      # plentiful supply
        cheap = self.seller.settle_interval(surplus=1.0, interval=INTERVAL)["p2p_price"]
        self.setUp()
        self._run([(0.1, INTERVAL)], [(1.0, INTERVAL)])      # scarce supply
        dear = self.seller.settle_interval(surplus=0.1, interval=INTERVAL)["p2p_price"]
        self.assertTrue(0.08 <= cheap < dear <= 0.27, (cheap, dear))
        self.assertLess(cheap, 0.12)
        self.assertGreater(dear, 0.22)

    def test_trade_price_never_breaks_a_buyers_limit_or_sellers_ask(self):
        self._run([(1.0, INTERVAL, 0.10)], [(0.4, INTERVAL, 0.12)])  # market price would be ~0.13
        match = next(iter(self.seller.trade_matches.values()))
        self.assertAlmostEqual(match.price, 0.12)  # capped at the buyer's bid
        self.assertGreaterEqual(match.price, 0.10)

    def test_no_trade_when_ask_exceeds_bid(self):
        self._run([(1.0, INTERVAL, 0.20)], [(0.4, INTERVAL, 0.15)])
        self.assertEqual(self.seller.trade_matches, {})

    def test_grid_settlement_uses_the_prices_of_that_time_of_day(self):
        peak = "2013-01-15T17:30:00"
        night = "2013-01-15T03:00:00"
        buy_peak = self.buyer.settle_interval(deficit=1.0, interval=peak)
        buy_night = self.buyer.settle_interval(deficit=1.0, interval=night)
        self.assertAlmostEqual(buy_peak["grid_cash"], -0.36)
        self.assertAlmostEqual(buy_night["grid_cash"], -0.12)
        self.assertEqual((buy_peak["import_price"], buy_peak["export_price"]), (0.36, 0.08))
        sell = self.seller.settle_interval(surplus=1.0, interval=peak)
        self.assertAlmostEqual(sell["grid_cash"], 0.08)

    def test_a_trade_that_arrives_after_settlement_is_booked_to_its_interval_and_the_grid_leg_reversed(self):
        # The seller settles interval k before the trade for k is notified (clock skew)
        sell_first = self.seller.settle_interval(surplus=1.0, interval=INTERVAL)
        self.assertAlmostEqual(sell_first["p2p_sold"], 0.0)
        self.assertAlmostEqual(self.seller.currency, 100 + 1.0 * 0.08)   # all 1 kWh went to the grid at 8p
        match = TradeMatch("o", "r", "pi1", "pi2", 0.4, 0.15, interval=INTERVAL)
        self.assertTrue(self.seller.apply_trade("late", match))
        # 0.4 kWh was sold to the peer at 15p instead of to the grid at 8p
        self.assertAlmostEqual(self.seller.currency, 100 + 0.6 * 0.08 + 0.4 * 0.15)
        # and the next interval is untouched
        nxt = self.seller.settle_interval(surplus=0.0, interval="2013-01-15T12:30:00")
        self.assertAlmostEqual(nxt["p2p_sold"], 0.0)

    def test_late_trade_for_a_buyer_refunds_the_grid_purchase(self):
        self.buyer.settle_interval(deficit=1.0, interval=INTERVAL)       # bought 1 kWh at 27p
        self.assertAlmostEqual(self.buyer.currency, 100 - 0.27)
        match = TradeMatch("o", "r", "pi1", "pi2", 0.4, 0.15, interval=INTERVAL)
        self.buyer.apply_trade("late", match)
        self.assertAlmostEqual(self.buyer.currency, 100 - 0.6 * 0.27 - 0.4 * 0.15)

    def test_a_trade_for_a_future_interval_waits_for_its_own_settlement(self):
        match = TradeMatch("o", "r", "pi1", "pi2", 0.4, 0.15, interval="2013-01-15T12:30:00")
        self.seller.apply_trade("early", match)                         # peer is a little ahead of us
        k = self.seller.settle_interval(surplus=1.0, interval=INTERVAL)
        k1 = self.seller.settle_interval(surplus=1.0, interval="2013-01-15T12:30:00")
        self.assertAlmostEqual(k["p2p_sold"], 0.0)
        self.assertAlmostEqual(k1["p2p_sold"], 0.4)

    def test_interval_survives_the_wire_format(self):
        offer = TradeOffer.from_dict(TradeOffer(datetime.now(), "pi1", 1.0, 0.08, datetime.now(), INTERVAL).to_dict())
        request = TradeRequest.from_dict(TradeRequest(datetime.now(), "pi2", 1.0, 0.27, interval=INTERVAL).to_dict())
        match = TradeMatch.from_dict(TradeMatch("o", "r", "pi1", "pi2", 1.0, 0.1, interval=INTERVAL).to_dict())
        self.assertEqual((offer.interval, request.interval, match.interval), (INTERVAL, INTERVAL, INTERVAL))
        self.assertNotIn("interval", TradeMatch("o", "r", "a", "b", 1.0, 0.1).to_dict())  # old messages unchanged


class TestRealNetworkIntegration(unittest.TestCase):
    """TradingIntegration against a *real* NetworkManager (only the HTTP call is faked).

    The mock-based integration tests hand-add whatever method the code calls, so they cannot notice
    when the real NetworkManager lacks it - which is how notify_trade_completion shipped broken.
    """

    def setUp(self):
        from network.trading_integration import TradingIntegration
        config = Mock(spec=ConfigManager)
        self.pi1 = PiDevice("pi1", "127.0.0.1", True, "prosumer-pi-1")
        self.pi2 = PiDevice("pi2", "127.0.0.2", False, "consumer-pi-1")
        config.devices = {"pi1": self.pi1, "pi2": self.pi2}
        config.get_local_device.return_value = self.pi1
        config.server_port = 5000
        config.retry_attempts = 1
        config.timeout_seconds = 1
        self.network = NetworkManager(config)
        self.integration = TradingIntegration(self.network)
        self.match = TradeMatch("o1", "r1", "pi1", "pi2", 0.4, 0.055)

    def test_get_device_by_name(self):
        self.assertIs(self.network.get_device_by_name("pi2"), self.pi2)
        self.assertIsNone(self.network.get_device_by_name("nope"))

    def test_notify_trade_completion_posts_to_the_peer(self):
        self.network.send_request = Mock(return_value={"status": "success"})
        ok = asyncio.run(self.integration.notify_trade_completion("pi2", "m1", self.match))
        self.assertTrue(ok)
        kwargs = self.network.send_request.call_args.kwargs
        self.assertEqual(kwargs["peer"], self.pi2)
        self.assertEqual(kwargs["endpoint"], "/trade/completion")
        self.assertEqual(kwargs["data"]["trade"]["amount"], 0.4)

    def test_notify_unknown_peer_is_a_clean_failure(self):
        self.network.send_request = Mock()
        self.assertFalse(asyncio.run(self.integration.notify_trade_completion("ghost", "m1", self.match)))
        self.network.send_request.assert_not_called()


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
