# Phase 2 - trading wired into the main loop

`core/main.py` now runs the full node, not just the energy loop:

- **Trading**: prosumers with a surplus (> 0.1 kWh) create a `TradeOffer`; consumers (or prosumers with a
  deficit they cannot cover) create a `TradeRequest`. Trade processing runs on a background asyncio loop.
- **Server**: the Flask `Server` is started so peers can POST offers/requests/completions. If the port is
  unavailable the node keeps simulating without peer connectivity.
- **LCD**: updated every reading (logged in mock mode).
- **Shutdown**: trade processing and the server are stopped in `cleanup()`.

Fixes found while doing this:

- `--device` only changed `SolarVille.device`; the trading manager, server and network code still used hostname /
  first-device matching, so `--device pi2` traded as `pi1`. `ConfigManager.set_local_device()` now makes the
  override global.
- `TradingIntegration` called blocking `requests` code inside the event loop, so one unreachable peer froze all
  trading. Those calls now run via `asyncio.to_thread`.
- Mock solar output (~0.0005 kWh/step) never beat demand (~0.18 kWh/step), so a prosumer never had a surplus.
  `simulation.solar_scale_factor` (default 1000, as in the old `SOLAR_SCALE_FACTOR`) scales it up.
- `setup_logging` used `basicConfig` without `force=True`, which is a no-op once any import has configured the
  root logger, so the configured log level never applied.
- `--mock` was parsed but ignored; it now forces mock mode.

## Still to do

- Real hardware: `SolarMonitor`, `CapacitorManager` and `LCDManager` only implement mock mode
  (`# TODO: Initialize real ...`). The old INA219 / LCD code is in `Old Code/` and the `realTime` branch.
- Executed trades do not yet change energy balances or currency (`TradingManager._execute_trade` only notifies the
  peer).
- Visualisation is still a stub.
- `Server.stop()` cannot actually stop Flask's dev server (the thread is a daemon and dies with the process).

## Running

```bash
cd unified-main
PYTHONPATH=$(pwd) python3 core/main.py --mock --device pi1   # prosumer
PYTHONPATH=$(pwd) python3 core/main.py --mock --device pi2   # consumer
PYTHONPATH=$(pwd) python3 -m unittest discover -s tests      # tests
```
