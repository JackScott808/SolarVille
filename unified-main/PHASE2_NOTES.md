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

## Trades now settle (no longer just "notify the peer")

- Matching marks what it uses: a matched amount is taken off both the offer and the request, so nothing is
  matched twice (previously the 0.1s matcher would re-match the same request indefinitely). Match ids are unique.
- `TradingManager` keeps a ledger (`currency`, `energy_sold`, `energy_bought`). Each side applies its own half
  of a trade (`apply_trade`, idempotent per match id): the executor after the counterparty acknowledges it, the
  counterparty when `/trade/completion` arrives (`handle_trade_completion`). If the counterparty can't be reached
  the trade fails and neither ledger changes; that pair is backed off for 2s instead of retried every 0.1s.
- Offers and requests now live for one simulated interval (`TradeRequest` gained an optional `expiry`).
  `settle_interval()` then sells whatever surplus wasn't traded to the grid at `grid_sell_price` and buys
  whatever deficit wasn't covered at `grid_buy_price`.
- Prosumers with a deficit request energy too.

## Plotting

`simulation/plotting.py` draws stacked panels on one time axis: demand vs generation, storage (prosumer),
surplus/deficit balance, money. `VisualisationManager` shows them live in a separate process (GUI toolkits
need their own main thread, esp. macOS) and always saves `output/<device>_<start>_<scale>.png` and `.csv`, so a
headless run still gets the plot. Colours follow the entity (demand blue, generation orange, storage aqua,
money violet) and were validated for colour-blind separation and contrast.

```bash
python3 core/main.py --mock --device pi1                 # live window; stays open at the end
python3 core/main.py --mock --device pi1 --no-plot       # file only
python3 core/main.py --mock --device pi1 --plot-theme dark --plot-dir results
```

## Still to do

- Real hardware: `SolarMonitor`, `CapacitorManager` and `LCDManager` only implement mock mode
  (`# TODO: Initialize real ...`). The old INA219 / LCD code is in `Old Code/` and the `realTime` branch.
- Not yet run across two real machines: the peer-to-peer path is covered by in-process tests with a fake
  network, and each node has been run alone in mock mode. Both nodes want port 5000, so two on one machine clash.
- The live window has only been exercised headlessly (spawned process, Agg backend), not on a real display.
- Trades settle within the same interval only if peers answer within it; a late acknowledgement is counted in
  the next interval.
- Prices are still the fixed `GRID_*_PRICE` constants (see the TODO in `core/constants.py`).
- `Server.stop()` cannot actually stop Flask's dev server (the thread is a daemon and dies with the process).

## Running

```bash
cd unified-main
python3 core/main.py --mock --device pi1   # prosumer
python3 core/main.py --mock --device pi2   # consumer
python3 -m unittest discover -s tests      # tests
```
