# Branch: unified-main
# File: tests/test_port_config.py
"""The server port is configurable, and the default avoids macOS's AirPlay Receiver (port 5000)."""

import os
import tempfile
import unittest

from core.config import ConfigManager, ConfigurationError
from core.constants import DEFAULT_PORT

TOPOLOGY = """{port}
devices:
  pi1: {{name: pi1, ip_address: 10.0.0.1, is_prosumer: true,  hostname: p1}}
  pi2: {{name: pi2, ip_address: 10.0.0.2, is_prosumer: false, hostname: c1}}
"""


class TestServerPort(unittest.TestCase):
    def load(self, port_line=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "network_topology.yml"), "w") as f:
            f.write(TOPOLOGY.format(port=port_line))
        config = ConfigManager(tmp.name)
        config._load_network_topology()
        return config

    def test_default_port_is_not_5000(self):
        self.assertNotEqual(DEFAULT_PORT, 5000)  # macOS AirPlay Receiver listens on 5000
        self.assertEqual(ConfigManager("config").server_port, DEFAULT_PORT)

    def test_topology_file_can_set_the_port(self):
        self.assertEqual(self.load("port: 6123").server_port, 6123)

    def test_no_port_in_the_topology_keeps_the_default(self):
        self.assertEqual(self.load().server_port, DEFAULT_PORT)

    def test_shipped_topology_loads_with_the_default_port(self):
        config = ConfigManager("config")
        config._load_network_topology()
        self.assertEqual(config.server_port, DEFAULT_PORT)

    def test_invalid_ports_are_rejected(self):
        config = ConfigManager("config")
        for bad in (80, 70000, "http", None, -1):
            with self.assertRaises(ConfigurationError, msg=repr(bad)):
                config.set_server_port(bad)
        for bad in ("port: 80", "port: abc"):
            with self.assertRaises(ConfigurationError, msg=bad):
                self.load(bad)

    def test_set_server_port_overrides(self):
        config = self.load("port: 6123")
        config.set_server_port("6200")
        self.assertEqual(config.server_port, 6200)


if __name__ == "__main__":
    unittest.main()
