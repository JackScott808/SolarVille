# Branch: unified-main
# File: sync.py

"""Start barrier: make every node begin the simulation at the same moment.

Each node simulates from the same data on its own timer, so if one starts a few seconds after
another their intervals are offset for the whole run. The barrier removes that offset:

1. Every node's server is up and it marks itself *ready*.
2. The **leader** (the first device in the topology config) polls its peers until they are all ready,
   then tells each one to start in ``start_delay`` seconds and waits the same delay itself.
3. Followers wait for that signal, then wait out what is left of the delay.

The delay is *relative*, so the nodes' wall clocks never need to agree; they end up starting
within roughly one network round trip of each other. If a peer never shows up, the barrier gives
up after ``timeout`` and the node simulates on its own (trades with that peer simply never match).
"""

import logging
import time
from typing import Callable, List

from core.config import ConfigManager
from core.device_types import PiDevice


class StartBarrier:
    """Rendezvous with the other devices before the simulation starts."""

    def __init__(self, config: ConfigManager, network, server, timeout: float = 20.0,
                 start_delay: float = 1.0, poll_interval: float = 0.25,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        """
        Args:
            config: Configuration (devices and which one is local)
            network: NetworkManager used to talk to peers
            server: the local Server (holds the ready/start state peers read and write)
            timeout: Seconds to wait for peers before simulating alone
            start_delay: Seconds between the leader's go signal and the start
            poll_interval: Seconds between readiness checks
            clock, sleep: Injectable for tests
        """
        self.config = config
        self.network = network
        self.server = server
        self.timeout = timeout
        self.start_delay = start_delay
        self.poll_interval = poll_interval
        self.clock = clock
        self.sleep = sleep
        self.logger = logging.getLogger(__name__)

    def peers(self) -> List[PiDevice]:
        local = self.config.get_local_device()
        return [d for d in self.config.devices.values() if not local or d.name != local.name]

    def is_leader(self) -> bool:
        local = self.config.get_local_device()
        first = next(iter(self.config.devices.values()), None)
        return bool(local and first and local.name == first.name)

    def wait(self) -> bool:
        """Block until the nodes are aligned.

        Returns:
            True if every peer started with us (or there are no peers); False if we are going alone
            or only with some of them.
        """
        peers = self.peers()
        if not peers:
            return True
        self.server.set_ready(True)
        return self._lead(peers) if self.is_leader() else self._follow()

    # -- leader ------------------------------------------------------------------------------

    def _lead(self, peers: List[PiDevice]) -> bool:
        self.logger.info(f"Waiting for {', '.join(p.name for p in peers)} to be ready (up to {self.timeout:.0f}s)")
        deadline = self.clock() + self.timeout
        ready: List[PiDevice] = []
        while self.clock() < deadline:
            ready = [p for p in peers if self._is_ready(p)]
            if len(ready) == len(peers):
                break
            self.sleep(self.poll_interval)

        if not ready:
            self.logger.warning("No peers became ready; running on our own")
            return False

        go_at = self.clock() + self.start_delay
        started = [p for p in ready if self._send_start(p)]
        remaining = go_at - self.clock()
        if remaining > 0:
            self.sleep(remaining)

        missing = [p.name for p in peers if p not in started]
        if missing:
            self.logger.warning(f"Starting without {', '.join(missing)}: not ready or not reachable")
            return False
        self.logger.info(f"All peers ready; started together ({len(started)} peer(s))")
        return True

    def _is_ready(self, peer: PiDevice) -> bool:
        response = self.network.send_request(peer, "/sim/ready", method="GET", timeout=1.0)
        return "error" not in response and bool(response.get("ready"))

    def _send_start(self, peer: PiDevice) -> bool:
        response = self.network.send_request(peer, "/sim/start", method="POST",
                                             data={"delay": self.start_delay}, timeout=2.0)
        return "error" not in response and response.get("status") == "success"

    # -- follower ----------------------------------------------------------------------------

    def _follow(self) -> bool:
        self.logger.info(f"Ready; waiting for the start signal (up to {self.timeout:.0f}s)")
        deadline = self.clock() + self.timeout
        while not self.server.start_event.is_set():
            if self.clock() >= deadline:
                self.logger.warning("No start signal received; running on our own")
                return False
            self.sleep(self.poll_interval / 5)
        remaining = self.server.start_delay - (self.clock() - self.server.start_received_at)
        if remaining > 0:
            self.sleep(remaining)
        self.logger.info("Start signal received; starting together")
        return True
