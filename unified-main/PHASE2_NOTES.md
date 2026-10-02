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
- Offers and requests are stamped with the simulated interval they belong to and only trade with each other
  within it. `settle_interval()` then sells whatever surplus wasn't traded to the grid at that time's export price
  and buys whatever deficit wasn't covered at that time's import price (see Pricing).
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

## Pricing (`core/tariff.py`, `tariff:` in `config/simulation.yml`)

The old flat constants (buy 25p, sell 5p, peer trades at the seller's ask) are replaced by:

- **Time-of-use grid import price**, in local London time (so it shifts an hour in British Summer Time):
  off-peak 00:30-05:30 £0.12, evening peak 16:00-19:00 £0.36, otherwise £0.27.
- **Export price** £0.08 (Smart Export Guarantee style), far below the import price: that gap is the reason
  to trade locally.
- **Peer-to-peer price formation**: sellers ask no less than the export price (they could sell to the grid),
  buyers bid no more than the import price. Each interval the market price sits between the two according to
  scarcity, `export + (import - export) * r / (1 + r)` with `r = demand / supply`: plentiful local supply pushes it
  towards the export price, scarce supply towards the import price, balance gives the mid-market rate
  (`p2p_pricing: mid_market` always uses the midpoint). Each trade is then clamped to the two parties' own limits.
- These numbers are **representative** of UK domestic time-of-use tariffs, not a specific supplier's; export rates
  in particular vary widely. Edit `tariff:` to model another. The config is validated (no gaps in the day, import
  never below export).

## Interval synchronisation

Two things used to put the nodes out of step: they started whenever the user launched them, and trades were booked
to the interval in which they *arrived*. Both are fixed:

- **Start barrier** (`network/sync.py`, `/sim/ready` and `/sim/start`): every node reports ready; the leader (first
  device in `network_topology.yml`) waits for the others, then tells each to start in 1s, using a *relative* delay
  so wall clocks need not agree. Result: nodes stay within a few milliseconds of each other (measured 4 ms with
  pi2 launched 4s late). `--standalone` skips the wait; `--sync-timeout` (default 20s) bounds it; a node whose
  peers never appear simulates alone.
- **Fixed deadlines**: interval k ends at `start + (k+1) * sleep_time` rather than "sleep after the last one", so
  processing time can't make nodes drift.
- **Interval-stamped trades**: offers, requests and matches carry the simulated interval; energy only trades
  within its own interval, and each node books a trade to that interval whenever it arrives, so per-interval
  figures agree exactly on both nodes. A trade that does arrive after its interval was settled reverses the grid
  transaction it replaces (the ledger stays right; that interval's already-written plot row would be stale).
- Without the barrier a large skew means no trades at all (offers and requests for the same interval never overlap
  in real time): `NODE_ARGS=--standalone scripts/two_nodes.sh` shows it.

## Running two nodes and checking they trade

```bash
scripts/two_nodes.sh [start_date] [speed] [pi2_delay]   # default: 2013-07-08 (very sunny), 900x, pi2 started 4s late
```

Runs pi1 (prosumer) and pi2 (consumer) as separate processes, each listening on its own loopback address
(127.0.0.1 / 127.0.0.2, so both can use the same port, 5050 by default), then `scripts/verify_two_nodes.py` checks: the nodes stayed in
step, sold == bought in *every interval*, same trades on both sides, every possible trade happened, every trade
priced between the grid's export and import price, and final balances match a recomputation from the per-interval
prices. On macOS first run `sudo ifconfig lo0 alias 127.0.0.2`. On a real network use `--bind` (default
0.0.0.0) and the Pis' addresses in `config/network_topology.yml`.

Running it found a bug no unit test could: `TradingIntegration` called `NetworkManager.get_device_by_name()`,
which did not exist (the integration test added the method to its own mock), so no trade could ever complete.
Fixed, with tests against a real `NetworkManager`. Result on the default day: 18 trades, 2.326 kWh at
£0.055/kWh (pi2 saved £0.45 vs buying that energy from the grid; pi1 earned £0.01 more than exporting it).

## Still to do

- Real hardware: `SolarMonitor`, `CapacitorManager` and `LCDManager` only implement mock mode
  (`# TODO: Initialize real ...`). The old INA219 / LCD code is in `Old Code/` and the `realTime` branch.
- Two nodes have been run as separate processes on one machine (below) but not yet on two physical machines.
- The start barrier assumes every device starts within `--sync-timeout` of the leader. A device that joins later
  than that simulates alone (late joining a running simulation is not supported).
- Peer-to-peer matching is done by prosumers on their own view of offers and requests; with several prosumers
  two of them could match the same request (the original design; not exercised with only one prosumer).
- The live window has only been exercised headlessly (spawned process, Agg backend), not on a real display.
- Trades settle within the same interval only if peers answer within it; a late acknowledgement is counted in
  the next interval.
- The tariff is static; real dynamic prices (e.g. half-hourly Agile-style rates) would need a data source.
- `Server.stop()` cannot actually stop Flask's dev server (the thread is a daemon and dies with the process).

## Running

```bash
cd unified-main
python3 core/main.py --mock --device pi1   # prosumer
python3 core/main.py --mock --device pi2   # consumer
python3 -m unittest discover -s tests      # tests
```
