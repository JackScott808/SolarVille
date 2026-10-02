# Branch: unified-main
# File: tests/test_sync.py
"""Tests for the start barrier (network/sync.py) and its server endpoints, on a fake clock."""

import threading
import unittest
from unittest.mock import Mock

from core.config import ConfigManager
from core.device_types import PiDevice
from network.server import Server
from network.sync import StartBarrier

PI1 = PiDevice("pi1", "127.0.0.1", True, "prosumer-pi-1")
PI2 = PiDevice("pi2", "127.0.0.2", False, "consumer-pi-1")
PI3 = PiDevice("pi3", "127.0.0.3", False, "consumer-pi-2")


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.now)


class FakeServer:
    def __init__(self, clock):
        self._clock = clock
        self.ready = False
        self.start_event = threading.Event()
        self.start_delay = 0.0
        self.start_received_at = None

    def set_ready(self, ready=True):
        self.ready = ready

    def receive_start(self, delay):
        self.start_delay, self.start_received_at = delay, self._clock.now
        self.start_event.set()


class FakeNetwork:
    """Peers become ready at given fake times; records the start signals they receive."""

    def __init__(self, clock, ready_at, start_ok=True):
        self.clock, self.ready_at, self.start_ok = clock, ready_at, start_ok
        self.starts = {}

    def send_request(self, peer, endpoint, method="GET", data=None, timeout=None, **_):
        if endpoint == "/sim/ready":
            at = self.ready_at.get(peer.name)
            if at is None or self.clock.now < at:
                return {"error": "connection_failed"} if at is None else {"ready": False}
            return {"ready": True}
        if endpoint == "/sim/start":
            if not self.start_ok:
                return {"error": "timeout"}
            self.starts[peer.name] = (self.clock.now, data["delay"])
            return {"status": "success"}
        raise AssertionError(endpoint)


def make(local, devices, ready_at=None, start_ok=True, **kwargs):
    fake = FakeClock()
    config = Mock(spec=ConfigManager)
    config.devices = {d.name: d for d in devices}
    config.get_local_device.return_value = local
    server = FakeServer(fake)
    network = FakeNetwork(fake, ready_at or {}, start_ok)
    barrier = StartBarrier(config, network, server, clock=fake.clock, sleep=fake.sleep, **kwargs)
    return barrier, fake, server, network


class TestLeader(unittest.TestCase):
    def test_no_peers_means_nothing_to_wait_for(self):
        barrier, fake, server, _ = make(PI1, [PI1])
        self.assertTrue(barrier.wait())
        self.assertEqual(fake.sleeps, [])
        self.assertFalse(server.ready)

    def test_first_device_is_the_leader(self):
        self.assertTrue(make(PI1, [PI1, PI2])[0].is_leader())
        self.assertFalse(make(PI2, [PI1, PI2])[0].is_leader())

    def test_leader_waits_for_the_peer_then_starts_together(self):
        barrier, fake, server, network = make(PI1, [PI1, PI2], ready_at={"pi2": 3.0}, start_delay=1.0)
        self.assertTrue(barrier.wait())
        self.assertTrue(server.ready)
        sent_at, delay = network.starts["pi2"]
        self.assertGreaterEqual(sent_at, 3.0)          # not before the peer was ready
        self.assertEqual(delay, 1.0)
        # the leader itself starts exactly start_delay after sending the signal
        self.assertAlmostEqual(fake.now, sent_at + 1.0)

    def test_leader_gives_up_when_no_peer_ever_shows_up(self):
        barrier, fake, _, network = make(PI1, [PI1, PI2], ready_at={}, timeout=5.0)
        self.assertFalse(barrier.wait())
        self.assertGreaterEqual(fake.now, 5.0)
        self.assertEqual(network.starts, {})

    def test_leader_starts_with_those_that_are_ready_and_reports_the_rest_missing(self):
        barrier, _, _, network = make(PI1, [PI1, PI2, PI3], ready_at={"pi2": 0.0}, timeout=3.0)
        self.assertFalse(barrier.wait())               # not everyone made it...
        self.assertIn("pi2", network.starts)           # ...but the ready one was still started
        self.assertNotIn("pi3", network.starts)

    def test_a_failed_start_signal_is_reported(self):
        barrier, _, _, _ = make(PI1, [PI1, PI2], ready_at={"pi2": 0.0}, start_ok=False)
        self.assertFalse(barrier.wait())


class TestFollower(unittest.TestCase):
    def test_follower_starts_after_the_leaders_delay(self):
        barrier, fake, server, _ = make(PI2, [PI1, PI2], timeout=10.0)
        fake.on_sleep = lambda now: server.receive_start(1.0) if now >= 2.0 and not server.start_event.is_set() else None
        self.assertTrue(barrier.wait())
        self.assertTrue(server.ready)
        # started exactly start_delay after the signal arrived
        self.assertAlmostEqual(fake.now, server.start_received_at + 1.0)

    def test_follower_that_hears_nothing_runs_alone(self):
        barrier, fake, _, _ = make(PI2, [PI1, PI2], timeout=4.0)
        self.assertFalse(barrier.wait())
        self.assertGreaterEqual(fake.now, 4.0)

    def test_leader_and_follower_start_at_the_same_instant(self):
        """The whole point: both resume at (signal time + delay), whatever order they arrived in."""
        leader, lclock, lserver, lnet = make(PI1, [PI1, PI2], ready_at={"pi2": 7.0}, start_delay=1.5)
        leader.wait()
        signal_time, delay = lnet.starts["pi2"]
        leader_start = lclock.now

        follower, fclock, fserver, _ = make(PI2, [PI1, PI2])
        fclock.now = 7.0
        fclock.on_sleep = lambda now: (fserver.receive_start(delay)
                                       if now >= signal_time and not fserver.start_event.is_set() else None)
        follower.wait()
        self.assertAlmostEqual(fclock.now - signal_time, leader_start - signal_time, delta=0.3)


class TestServerEndpoints(unittest.TestCase):
    def setUp(self):
        config = Mock(spec=ConfigManager)
        config.server_port = 5000
        config.devices = {"pi1": PI1, "pi2": PI2}
        config.get_local_device.return_value = PI2
        self.server = Server(config)
        self.client = self.server.app.test_client()

    def test_not_ready_until_the_node_says_so(self):
        body = self.client.get("/sim/ready").get_json()
        self.assertEqual((body["ready"], body["started"], body["device"]), (False, False, "pi2"))
        self.server.set_ready()
        self.assertTrue(self.client.get("/sim/ready").get_json()["ready"])

    def test_start_records_the_delay_once(self):
        r = self.client.post("/sim/start", json={"delay": 1.5})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.server.start_event.is_set())
        self.assertEqual(self.server.start_delay, 1.5)
        self.assertIsNotNone(self.server.start_received_at)
        # a repeated signal must not move the start time
        first = self.server.start_received_at
        self.client.post("/sim/start", json={"delay": 9.0})
        self.assertEqual(self.server.start_delay, 1.5)
        self.assertEqual(self.server.start_received_at, first)
        self.assertTrue(self.client.get("/sim/ready").get_json()["started"])

    def test_start_rejects_bad_delays(self):
        for bad in ({"delay": "soon"}, {"delay": -1}):
            self.assertEqual(self.client.post("/sim/start", json=bad).status_code, 400)
        self.assertFalse(self.server.start_event.is_set())


if __name__ == "__main__":
    unittest.main()
