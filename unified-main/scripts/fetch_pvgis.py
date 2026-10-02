#!/usr/bin/env python3
"""Download real hourly PV output for a location from PVGIS (EU Joint Research Centre).

The simulation uses these files automatically (solar_source: auto) instead of its
built-in London model, so the generation follows the *actual* weather of the simulated date.

    python scripts/fetch_pvgis.py --year 2012            # London, matches the demo dataset year
    python scripts/fetch_pvgis.py --year 2012 --year 2013 --lat 55.95 --lon -3.19   # Edinburgh

Files are written to dataset/pvgis_<lat>_<lon>_<year>.csv for a 1 kWp system and scaled to
`prosumer.system_kwp` at run time. PVGIS-SARAH covers Europe for about 2005-2020; see
https://joint-research-centre.ec.europa.eu/pvgis for the data ranges currently offered.
"""
import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

URL = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, action="append", required=True, help="Year to fetch (repeatable)")
    parser.add_argument("--lat", type=float, default=51.5074, help="Latitude (default: London)")
    parser.add_argument("--lon", type=float, default=-0.1278, help="Longitude (default: London)")
    parser.add_argument("--tilt", type=float, default=35.0, help="Panel tilt in degrees (default 35)")
    parser.add_argument("--azimuth", type=float, default=180.0,
                        help="Panel direction, clockwise from north (default 180 = south)")
    parser.add_argument("--loss", type=float, default=14.0, help="System losses in %% (PVGIS default 14)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "dataset",
                        help="Output directory (default: unified-main/dataset)")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    status = 0
    for year in args.year:
        query = urllib.parse.urlencode({
            "lat": args.lat, "lon": args.lon, "startyear": year, "endyear": year,
            "pvcalculation": 1, "peakpower": 1, "loss": args.loss,
            "angle": args.tilt, "aspect": args.azimuth - 180,  # PVGIS: 0 = south
            "outputformat": "csv", "browser": 0,
        })
        target = args.out / f"pvgis_{args.lat:.2f}_{args.lon:.2f}_{year}.csv"
        print(f"Fetching {year} for ({args.lat}, {args.lon}) ...")
        try:
            with urllib.request.urlopen(f"{URL}?{query}", timeout=120) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            print(f"  PVGIS rejected the request ({e.code}): {e.read()[:300].decode(errors='replace')}")
            status = 1
            continue
        except urllib.error.URLError as e:
            print(f"  Could not reach PVGIS: {e.reason}")
            status = 1
            continue
        if b"time,P" not in body:
            print(f"  Unexpected response: {body[:300].decode(errors='replace')}")
            status = 1
            continue
        target.write_bytes(body)
        print(f"  saved {target} ({len(body) // 1024} KB)")
    return status


if __name__ == "__main__":
    sys.exit(main())
