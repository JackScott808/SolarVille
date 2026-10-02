# Branch: unified-main
# File: test_config_device.py
"""Tests for explicit local-device selection (--device) in ConfigManager."""
import unittest

from core.config import ConfigManager
from core.device_types import PiDevice


def make_config() -> ConfigManager:
    config = ConfigManager("config")
    config.hardware_config.mock_mode = True
    config.devices = {
        "pi1": PiDevice(name="pi1", ip_address="10.0.0.1", is_prosumer=True, hostname="prosumer-pi-1"),
        "pi2": PiDevice(name="pi2", ip_address="10.0.0.2", is_prosumer=False, hostname="consumer-pi-1"),
    }
    return config


class TestLocalDeviceOverride(unittest.TestCase):
    def test_mock_mode_falls_back_to_first_device(self):
        self.assertEqual(make_config().get_local_device().name, "pi1")

    def test_override_is_seen_by_get_local_device(self):
        config = make_config()
        config.set_local_device("pi2")
        # Every component that calls get_local_device() must agree with --device
        self.assertEqual(config.get_local_device().name, "pi2")
        self.assertFalse(config.get_local_device().is_prosumer)

    def test_unknown_device_raises(self):
        with self.assertRaises(ValueError):
            make_config().set_local_device("nope")


class TestSolarScaleFactor(unittest.TestCase):
    def test_default_scale_factor(self):
        self.assertEqual(make_config().sim_config.solar_scale_factor, 1000.0)


if __name__ == "__main__":
    unittest.main()
