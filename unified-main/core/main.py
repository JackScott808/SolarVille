# Branch: unified-main
# File: main.py

#!/usr/bin/env python3
import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path

# Make `core`, `network`, ... importable however this file is launched
# (python core/main.py, from any directory, without setting PYTHONPATH)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Core imports
from core.config import ConfigManager
from core.device_types import PiDevice

# Hardware imports
from hardware.lcd_manager import LCDManager
from hardware.solar_manager import SolarMonitor
from hardware.capacitor_manager import CapacitorManager

# Network imports
from network.trading_integration import TradingIntegration
from network.discovery import PeerDiscovery
from network.health_check import HealthChecker
from network.server import Server

# Simulation imports
from simulation.trading_manager import TradingManager
from simulation.visualisation_manager import VisualisationManager
from simulation.data_analysis import load_data
from simulation.solar_model import create_solar_source

# Utils imports
from utils.logging import setup_logging
from utils.error_handling import ErrorHandler

MIN_TRADE_KWH = 0.01  # ignore surpluses/deficits smaller than this; the grid takes them

class SolarVille:
    def __init__(self):
        """Initialize SolarVille system"""
        self.config = None
        self.device = None
        self.components = {}
        self.server = None
        self._loop = None          # asyncio loop that runs trading in the background
        self._loop_thread = None
        self._interrupted = False
        self.plot_options = {}
        
    def initialize(self, config_path: str = None, device_name: str = None, mock: bool = False,
                   plot_live: bool = True, plot_dir: str = "output", plot_theme: str = "light"):
        """Initialize all system components"""
        try:
            # Load configuration
            self.config = ConfigManager(config_path)
            self.config.load_config()
            if mock:
                self.config.sim_config.mock_mode = True
                self.config.hardware_config.mock_mode = True

            # Set up logging
            setup_logging(self.config.sim_config.log_level)

            # Get local device
            if device_name:
                # Use specified device
                # set_local_device also makes every other component (trading,
                # network, server) agree on which device this process is
                self.device = self.config.set_local_device(device_name)
                logging.info(f"Using specified device: {device_name}")
            else:
                # Auto-detect device
                self.device = self.config.get_local_device()

            if not self.device:
                raise ValueError("Could not determine local device")

            logging.info(f"Running as: {self.device.name} ({'prosumer' if self.device.is_prosumer else 'consumer'})")

            self.plot_options = {"live": plot_live, "output_dir": plot_dir, "theme": plot_theme}

            # Initialize components based on device role
            self._initialize_components()

            logging.info("SolarVille initialization complete")
            return True

        except Exception as e:
            logging.error(f"Initialization failed: {e}", exc_info=True)
            return False
            
    def _initialize_components(self):
        """Initialize components based on device role"""
        try:
            from network.network_manager import NetworkManager

            # Initialize network manager
            network_manager = NetworkManager(self.config)

            # Common components
            self.components.update({
                'network_manager': network_manager,
                'lcd': LCDManager(mock_mode=self.config.sim_config.mock_mode),
                'health_checker': HealthChecker(self.config, network_manager),
                'error_handler': ErrorHandler(),
                'trading_manager': TradingManager(self.config, network_manager),
                'vis_manager': VisualisationManager(
                    self.config.sim_config.start_date,
                    self.config.sim_config.timescale,
                    device_name=self.device.name,
                    is_prosumer=self.device.is_prosumer,
                    **self.plot_options
                )
            })

            # Prosumer-specific components
            if self.device.is_prosumer:
                self.components.update({
                    'solar_monitor': SolarMonitor(
                        mock_mode=self.config.sim_config.mock_mode,
                        solar_source=self._create_solar_source(),
                        interval_seconds=self.config.sim_config.interval_seconds,
                    ),
                    'capacitor_manager': CapacitorManager(
                        mock_mode=self.config.sim_config.mock_mode,
                        capacity_kwh=self.config.prosumer_config.storage_capacity_kwh,
                    )
                })

            # HTTP server so peers can send us offers, requests and notifications
            self.server = Server(self.config, self.components['trading_manager'])

            logging.info(f"Initialized components for {'prosumer' if self.device.is_prosumer else 'consumer'}")

        except Exception as e:
            logging.error(f"Component initialization failed: {e}", exc_info=True)
            raise
            
    def start(self):
        """Start the simulation"""
        try:
            logging.info("Starting simulation...")

            # Get simulation config
            sim_config = self.config.sim_config

            # Load energy data with filtering
            logging.info(f"Loading data for household: {getattr(sim_config, 'household', 'MAC000002')}")

            # Calculate end date
            from simulation.data_analysis import calculate_end_date
            end_date = calculate_end_date(sim_config.start_date, sim_config.timescale)

            # Relative dataset paths in simulation.yml are relative to unified-main/
            file_path = Path(sim_config.file_path)
            if not file_path.is_absolute() and not file_path.exists():
                file_path = ROOT / file_path

            data = load_data(
                file_path=str(file_path),
                household=getattr(sim_config, 'household', 'MAC000002'),
                start_date=sim_config.start_date,
                end_date=end_date.strftime("%Y-%m-%d")
            )

            if data is None or data.empty:
                raise ValueError("Failed to load energy data or no data in range")

            logging.info(f"Loaded {len(data)} data points")

            # Open the live plot (a no-op without a display; the plot is still saved at the end)
            self.components['vis_manager'].start(data)

            self._start_networking()

            # Start main simulation loop
            self._run_simulation(data)

            # Let the user look at the finished plot; skipped on Ctrl-C so exit is immediate
            if not self._interrupted:
                self.components['vis_manager'].wait_until_closed()

        except KeyboardInterrupt:
            logging.info("Simulation interrupted by user")
        except Exception as e:
            logging.error(f"Simulation error: {e}", exc_info=True)
        finally:
            self.cleanup()

    def _create_solar_source(self):
        """Solar output for mock mode: real PVGIS data if present, else the London model."""
        p = self.config.prosumer_config
        data_dir = Path(self.config.sim_config.file_path)
        if not data_dir.is_absolute() and not data_dir.exists():
            data_dir = ROOT / data_dir
        source = create_solar_source(p.system_kwp, p.tilt_deg, p.azimuth_deg, p.latitude, p.longitude,
                                     p.solar_source, str(data_dir.parent))
        logging.info(f"Solar source: {source.description}")
        return source

    def _start_networking(self):
        """Start the peer-facing server and the background trade processing loop."""
        try:
            self.server.start()
        except Exception as e:
            # Not fatal: the node can still simulate, it just can't receive peer trades
            logging.warning(f"Server failed to start, continuing without peer connectivity: {e}")

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()
        self._run_async(self.components['trading_manager'].start_processing())

    def _run_async(self, coro, timeout: float = 10.0):
        """Run a coroutine on the background trading loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def _run_simulation(self, data):
        """
        Main simulation loop - iterates through energy data and simulates behavior.

        Args:
            data: DataFrame with datetime index and 'energy' column
        """
        import time
        import asyncio
        from core.energy_types import EnergyReading, ProsumerReading
        from simulation.data_analysis import calculate_sleep_time

        logging.info("Starting simulation loop")
        logging.info(f"Device: {self.device.name} ({'prosumer' if self.device.is_prosumer else 'consumer'})")

        sim_config = self.config.sim_config
        sleep_time = calculate_sleep_time(
            sim_config.simulation_speed,
            sim_config.interval_seconds
        )

        logging.info(f"Simulation speed: {sim_config.simulation_speed}x")
        logging.info(f"Sleep time between readings: {sleep_time:.2f}s")

        trading_manager = self.components['trading_manager']
        # Offers/requests live for one interval of real time
        offer_ttl = max(sleep_time, 1.0)

        # Run simulation
        try:
            # Iterate through data
            for idx, (timestamp, row) in enumerate(data.iterrows()):
                # Get energy consumption (ensure it's a float)
                energy_demand = float(row['energy'])  # kWh

                # Create reading based on device type
                if self.device.is_prosumer:
                    # Get solar and storage data from hardware (or mock)
                    solar_manager = self.components.get('solar_monitor')
                    capacitor_manager = self.components.get('capacitor_manager')

                    # Get readings from hardware managers
                    solar_data = solar_manager.get_readings(timestamp)
                    # Mock mode already produces household-scale output (see simulation.solar_model);
                    # only a real table-top panel needs scaling up to act as a house.
                    scale = 1.0 if sim_config.mock_mode else sim_config.solar_scale_factor
                    solar_energy = solar_data['solar_energy'] * scale  # kWh
                    solar_power = solar_data['solar_power'] * scale    # W

                    storage_level = capacitor_manager.get_soc() * 100  # Convert to percentage

                    # Calculate storage power (positive = charging, negative = discharging)
                    balance = solar_energy - energy_demand
                    if balance > 0:
                        # Surplus - try to charge
                        stored = capacitor_manager.charge(balance)
                        storage_power = stored * 2000  # Rough W conversion
                        balance -= stored
                    elif balance < 0:
                        # Deficit - try to discharge
                        discharged = capacitor_manager.discharge(abs(balance))
                        storage_power = -discharged * 2000  # Negative for discharge
                        balance += discharged
                    else:
                        storage_power = 0

                    reading = ProsumerReading(
                        timestamp=timestamp,
                        demand=energy_demand,
                        generation=solar_energy,
                        balance=balance,  # Final balance after storage
                        storage_level=storage_level,
                        storage_power=storage_power,
                        solar_power=solar_power
                    )

                else:
                    # Consumer reading
                    reading = EnergyReading(
                        timestamp=timestamp,
                        demand=energy_demand,
                        balance=-energy_demand
                    )

                # Post this interval's surplus as an offer, or deficit as a request.
                # They live for one interval; whatever isn't traded peer-to-peer is
                # settled with the grid below.
                surplus = max(reading.balance, 0.0)
                deficit = max(-reading.balance, 0.0)
                if surplus > MIN_TRADE_KWH:
                    logging.info(f"Surplus: {surplus:.3f} kWh - creating trade offer")
                    self._run_async(trading_manager.create_offer(surplus, ttl=offer_ttl))
                if deficit > MIN_TRADE_KWH:
                    logging.info(f"Deficit: {deficit:.3f} kWh - creating trade request")
                    self._run_async(trading_manager.create_request(deficit, ttl=offer_ttl))

                # Sleep to maintain simulation speed (peers match and settle meanwhile)
                time.sleep(sleep_time)

                trade = trading_manager.settle_interval(surplus=surplus, deficit=deficit)

                # Log current state every few readings
                if idx % 1 == 0:
                    log_msg = f"[{timestamp}] Demand: {reading.demand:.3f} kWh, Balance: {reading.balance:+.3f} kWh"
                    if self.device.is_prosumer:
                        log_msg += f", Gen: {reading.generation:.3f} kWh, SOC: {reading.storage_level:.1f}%"
                    log_msg += (f" | P2P sold/bought: {trade['p2p_sold']:.3f}/{trade['p2p_bought']:.3f} kWh,"
                                f" grid sold/bought: {trade['grid_sold']:.3f}/{trade['grid_bought']:.3f} kWh,"
                                f" balance: £{trade['currency']:.2f}")
                    print(log_msg, flush=True)  # Print to stdout
                    logging.info(log_msg)

                # Update LCD (LCDManager just logs in mock mode)
                lcd = self.components.get('lcd')
                if lcd:
                    if self.device.is_prosumer:
                        lcd.display(f"Bat:{reading.storage_level:.0f}% Gen:{reading.solar_power:.1f}W",
                                    f"GBP {trade['currency']:.2f}")
                    else:
                        lcd.display(f"Demand:{reading.demand:.3f}kWh",
                                    f"GBP {trade['currency']:.2f}")

                # Update visualization
                vis_manager = self.components.get('vis_manager')
                if vis_manager:
                    vis_manager.update(reading, **trade)

        except KeyboardInterrupt:
            self._interrupted = True
            logging.info("Simulation interrupted by user")
        finally:
            logging.info("Simulation loop completed")
        
    def cleanup(self):
        """Cleanup resources"""
        logging.info("Cleaning up...")
        if self._loop is not None:
            try:
                self._run_async(self.components['trading_manager'].stop_processing(), timeout=2.0)
            except Exception as e:
                logging.warning(f"Trade processing did not stop cleanly ({type(e).__name__}); an unreachable peer may still be being contacted")
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5.0)
            self._loop = None
        if self.server is not None:
            try:
                self.server.stop()
            except Exception as e:
                logging.error(f"Error stopping server: {e}")
        for component in self.components.values():
            if hasattr(component, 'cleanup'):
                try:
                    component.cleanup()
                except Exception as e:
                    logging.error(f"Cleanup error: {e}")

def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='SolarVille Smart Grid Simulation')
    parser.add_argument('--config', type=str, default=str(ROOT / 'config'), help='Path to configuration directory')
    parser.add_argument('--mock', action='store_true', help='Run in mock mode (required on non-Pi hardware)')
    parser.add_argument('--device', type=str, help='Device name to simulate (e.g., pi1, pi2). Overrides hostname matching.')
    parser.add_argument('--no-plot', action='store_true', help='Do not open the live plot window (the plot is still saved)')
    parser.add_argument('--plot-dir', type=str, default='output', help='Directory for the saved plot and CSV (default: output)')
    parser.add_argument('--plot-theme', choices=['light', 'dark'], default='light', help='Plot colour theme')
    return parser.parse_args()

def main():
    """Main entry point"""
    args = parse_args()

    # Create and initialize SolarVille
    solarville = SolarVille()
    if not solarville.initialize(args.config, args.device, args.mock,
                              plot_live=not args.no_plot, plot_dir=args.plot_dir, plot_theme=args.plot_theme):
        sys.exit(1)

    # Start simulation
    solarville.start()

if __name__ == "__main__":
    main()