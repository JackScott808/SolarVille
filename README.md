# SolarVille: Table-top Smart Grid Demo

## Summary

SolarVille simulates a small neighbourhood electricity market. Households are modelled as devices
(Raspberry Pis in the physical demo): consumers only use energy, while prosumers also generate it from solar
panels and store it in a battery. Devices trade surplus energy with each other over the network, and whatever
they cannot trade with a neighbour is bought from or sold to the grid at time-of-use prices.

The code lives in [`unified-main/`](unified-main). The older implementation is kept in [`Old Code/`](Old%20Code)
for reference.

## Current status

What works, running on any computer in mock mode:

- Household demand replayed from the London smart meter dataset (included in the repository).
- Solar generation based on real London weather: hourly PVGIS data for 2012 to 2014 is included, and a physical
  solar model covers other dates.
- A battery for prosumers.
- A time-of-use grid tariff and peer-to-peer price formation that responds to local supply and demand.
- Peer-to-peer trading between devices over HTTP, with each device keeping an account of its energy and money.
- Several devices starting together and staying in step (a start barrier, and trades booked to the simulated
  interval they belong to).
- Live plots plus a saved PNG and CSV of every run.
- Around 200 unit tests.

What does not work yet:

- **Real hardware.** The solar sensor, battery and LCD drivers in `unified-main/hardware/` only implement mock mode.
  The earlier hardware code is in `Old Code/`.
- Joining a simulation that is already running (a device that misses the start simulates alone).
- Live half-hourly prices; the tariff is a fixed daily pattern.

## Requirements

- Python 3.11 (the version it was developed and tested on)
- The packages in `unified-main/requirements.txt`
- On Windows, also `tzdata`, which the standard library needs for time zone data (the requirements file installs it)
- To see the live plot window, a desktop session. Without a display the plot is only saved to a file.

## Setup

```sh
git clone https://github.com/JackScott808/SolarVille.git
cd SolarVille/unified-main
python3 -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Running a simulation

All commands are run from the `unified-main` directory.

```sh
python core/main.py --mock --standalone --device pi1
```

This simulates one prosumer household for one day. At the default speed one simulated day takes about five
minutes. A plot window shows the day as it happens and stays open at the end until you close it; Ctrl-C exits
immediately. The plot and the data behind it are always saved to `output/`.

The simulated days available depend on the dataset: household MAC000002 has data from 2012-10-12 to 2014-02-28.

### Command-line options

| Option | Meaning |
|---|---|
| `--config DIR` | Directory holding `simulation.yml` and `network_topology.yml` (default: `config/`) |
| `--device NAME` | Which device from the topology to simulate, for example `pi1` or `pi2`. Overrides hostname matching. |
| `--mock` | Force mock mode (generated hardware readings). `mock_mode: true` in `simulation.yml` has the same effect and is the default. |
| `--standalone` | Do not wait for the other devices before starting. Use this for single-node runs. |
| `--sync-timeout SECONDS` | How long to wait for the other devices before simulating alone (default 20) |
| `--bind ADDRESS` | Address the device's server listens on (default `0.0.0.0`) |
| `--no-plot` | Do not open the live plot window; the plot is still saved |
| `--plot-dir DIR` | Where to save the plot and CSV (default `output/`) |
| `--plot-theme light\|dark` | Plot colours |

Without `--standalone`, a device waits for the other devices in the topology before it starts. If none appear
within the timeout, it runs alone.

## Configuration

### `config/simulation.yml`

| Setting | Meaning |
|---|---|
| `simulation.file_path` | Demand data CSV (default `dataset/block_0.csv`, included) |
| `simulation.household` | Household ID to replay (default `MAC000002`) |
| `simulation.start_date`, `timescale` | Start date, and `d`, `w`, `m` or `y` for a day, week, month or year |
| `simulation.simulation_speed` | Speed-up factor. 300 means one half-hour interval every 6 seconds. |
| `simulation.interval_seconds` | Length of one data interval (1800 for the dataset) |
| `simulation.solar_scale_factor` | Real hardware only: scales a small table-top panel up to household size |
| `hardware.mock_mode` | Use generated hardware readings (default `true`) |
| `prosumer.*` | The simulated household: solar size in kWp, panel tilt and direction, location, `solar_source`, battery size in kWh |
| `tariff.*` | Grid prices and the peer-to-peer pricing rule (see Pricing) |

Timestamps in the data are treated as UTC.

### `config/network_topology.yml`

Lists the devices: name, IP address, whether it is a prosumer, and hostname. The first device is the leader that
starts the others together. Without `--device`, a device identifies itself by matching its hostname against this
list (in mock mode it falls back to the first device).

## How it works

### Solar generation

For a prosumer in mock mode, generation for each interval comes from one of two sources:

1. **Real PVGIS data.** The `dataset/pvgis_*.csv` files hold hourly PV output for a 1 kWp system in London
   (PVGIS-SARAH2, 2012 to 2014), scaled to `prosumer.system_kwp`. These cover the whole demand dataset.
2. **A physical model**, used for dates the files do not cover: sun position, clear-sky irradiance, a beam and
   diffuse split, a tilted panel, and London's average cloudiness with day-to-day variation. It was calibrated
   against the real data and matches its annual total to within about 2 percent.

Other years or locations can be downloaded with `python scripts/fetch_pvgis.py --year 2015`
(see `--help` for location and panel options). The simulation picks the files up automatically. Set
`prosumer.solar_source` to `model` to ignore them.

### Pricing

- **Grid import price** depends on the time of day in London local time: off-peak 00:30 to 05:30 at 12p/kWh,
  the evening peak 16:00 to 19:00 at 36p/kWh, and 27p/kWh otherwise.
- **Grid export price** is a flat 8p/kWh.
- **Peer-to-peer price** always falls between the export and import price. Plentiful local supply pushes it
  towards the export price, scarce supply towards the import price, and a balanced market gives the midpoint.
  Each trade also respects the seller's minimum and the buyer's maximum.

These values are representative of UK domestic time-of-use tariffs, not a particular supplier's rates.
Change them under `tariff:` in `simulation.yml`.

### Trading

Each interval, a device with surplus energy (after charging its battery) offers it for sale, and a device with a
deficit requests energy. Prosumers match offers with requests for the same interval and notify the other
party. Anything left over is sold to or bought from the grid at that interval's price.

## Running two or more devices

On one machine:

```sh
cd unified-main
scripts/two_nodes.sh
```

This starts a prosumer (`pi1`) and a consumer (`pi2`) as separate processes on a sunny day, starting `pi2` four
seconds late on purpose, then checks the logs and saved data: that the devices stayed in step, that both recorded
the same trades in every interval, that every trade was priced between the grid prices, and that the final
balances can be recomputed from the energy flows. On macOS, run `sudo ifconfig lo0 alias 127.0.0.2` once
first, because only `127.0.0.1` exists by default.

On separate machines, put each device's real IP address in `config/network_topology.yml` on all of them, then
run on each:

```sh
python core/main.py --device pi1      # on the first machine
python core/main.py --device pi2      # on the second machine
```

Start them within `--sync-timeout` seconds of each other. Port 5000 must be reachable between them.

## Tests

```sh
cd unified-main
python -m unittest discover -s tests
```

## Repository layout

```
unified-main/
  core/         configuration, tariff, device and trade data types, and main.py (the entry point)
  hardware/     solar sensor, battery and LCD (mock mode only)
  network/      HTTP server, peer communication and the start barrier
  simulation/   data loading, solar model, trading, plotting
  config/       simulation.yml and network_topology.yml
  dataset/      household demand data and PVGIS solar data
  scripts/      fetch_pvgis.py, two_nodes.sh, verify_two_nodes.py
  tests/        unit tests
  PHASE2_NOTES.md   design notes and the reasoning behind the pricing, solar and synchronisation work
Old Code/       the earlier implementation, including real hardware code
```

Other branches in this repository are earlier iterations of the project and are kept for reference.

## Raspberry Pi hardware

The physical demo uses Raspberry Pi 4 boards with mini solar panels, rechargeable batteries and a character LCD.
The hardware drivers for the current code are not written yet (see Current status); the code in `Old Code/`
shows how the earlier version read the sensors and drove the display.

## Contributing

Contributions are welcome in three areas:

- Hardware: finishing the drivers in `unified-main/hardware/` and improving the physical setup.
- Software: new features, tests and fixes.
- Documentation: guides and learning resources about grid management.

## Licence

Licensing terms have not been decided yet.

## Contact

Questions or suggestions: desen.kirli@ed.ac.uk

## Acknowledgements

Thanks to Jack Scott, Arif Akanda and Chun Gou for making SolarVille possible.
