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
- Mock solar output (~0.0005 kWh/step, random) never beat demand (~0.18 kWh/step), so a prosumer never had a
  surplus. Replaced by the London solar model below; `simulation.solar_scale_factor` now only applies to real
  hardware (a table-top panel standing in for a house).
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

## Realistic solar generation (London)

Mock-mode prosumers no longer use random numbers (`prosumer:` section of `config/simulation.yml`: size, tilt,
direction, location, battery size). Two sources, chosen per timestamp by `solar_source: auto`:

1. **Real PVGIS data** (`dataset/pvgis_*.csv`, fetched with `scripts/fetch_pvgis.py`): actual hourly PV output
   for London from PVGIS-SARAH2, currently 2012-2014, which covers the whole demand dataset (Oct 2012 - Feb 2014).
   Values are interpolated between PVGIS's samples (stamped at HH:10 UTC) and scaled to `system_kwp`.
2. **A physical model** (`simulation/solar_model.py`) for dates the files don't cover: sun position from
   latitude and date, Haurwitz clear-sky irradiance, Erbs beam/diffuse split, tilted panel, monthly average
   cloudiness plus day-to-day and passing-cloud variation. Weather is deterministic per date, so all nodes agree.

Validation against the real files (tests in `tests/test_solar_model.py`):

- The files parse completely (26,304 hours, none missing over the dataset period).
- The model's sun position matches PVGIS's own sun-height column to ~0.1 degrees, which also confirms the
  timestamps are UTC.
- The model was **calibrated on** the real data, so these agreements are by construction, not independent proof:
  annual yield ~985 vs 998 kWh/kWp (within ~1.5%), every month within ~8%, and the spread of daily output
  (best/typical/gloomy days, winter and summer) matches. Only three real years were used, so months can still be
  ~10-15% off against a longer record.
- Real seasonal contrast is smaller than one might guess: July yields ~3.3x December, not 7x.
- Timestamps are treated as UTC/GMT.

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

## Running two nodes and checking they trade

```bash
scripts/two_nodes.sh [start_date] [speed]     # default: 2013-07-08 (very sunny), 900x
```

Runs pi1 (prosumer) and pi2 (consumer) as separate processes, each listening on its own loopback address
(127.0.0.1 / 127.0.0.2, so both can use port 5000), then `scripts/verify_two_nodes.py` checks: same trades on both
sides, energy sold == bought, final balances match an independent recomputation, and every possible trade
happened. On macOS first run `sudo ifconfig lo0 alias 127.0.0.2`. On a real network use `--bind` (default
0.0.0.0) and the Pis' addresses in `config/network_topology.yml`.

Running it found a bug no unit test could: `TradingIntegration` called `NetworkManager.get_device_by_name()`,
which did not exist (the integration test added the method to its own mock), so no trade could ever complete.
Fixed, with tests against a real `NetworkManager`. Result on the default day: 18 trades, 2.326 kWh at
£0.055/kWh (pi2 saved £0.45 vs buying that energy from the grid; pi1 earned £0.01 more than exporting it).

## Still to do

- Real hardware: `SolarMonitor`, `CapacitorManager` and `LCDManager` only implement mock mode
  (`# TODO: Initialize real ...`). The old INA219 / LCD code is in `Old Code/` and the `realTime` branch.
- Two nodes have been run as separate processes on one machine (below) but not yet on two physical machines.
- Trades are attributed to the interval in which they arrive, and the two processes are not clock-synchronised
  (they start ~1-2s apart), so a trade near an interval boundary can be booked in adjacent intervals on the two
  nodes. Totals are exact; per-interval figures can shift by one slot. Fix: stamp offers/requests/matches with the
  simulated interval and settle by that, rather than by arrival time.
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
