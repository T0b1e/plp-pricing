"""Run the pipeline end to end:  python main.py [--from STEP]

  load      read Report AR_AP_*.xlsx                      (no API)
  parse     split route column into stops                 (no API)
  aliases   merge spellings of the same place             (no API)
  geocode   lat/lon per place    - Places / Geocoding API (cached)
  distance  road km per trip     - Routes API             (cached)
  export    supervisor trip table -> data/trip_table.xlsx
  model     fit km -> price per vehicle class             (no API)
  checksum  rows + money totals per file, every stage vs the xlsx

Then:  python -m src.estimate --km 120 --vehicle "10W reefer"
"""
import argparse
import sys

from src import aliases, checksum, distance, export, fit_model, geocode, load, parse_route
from src.files import DataFileError
from src.google_api import ApiDisabled, ApiError, MissingKey

# Problems the person running the pipeline can fix; shown as one line, not a traceback.
EXPECTED = (DataFileError, MissingKey, ApiDisabled, ApiError, FileNotFoundError)

STEPS = {
    "load": load.main,
    "parse": parse_route.main,
    "aliases": aliases.main,
    "geocode": geocode.main,
    "distance": distance.main,
    "export": export.main,
    "model": fit_model.main,
    "checksum": checksum.main,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=STEPS, default="load")
    ap.add_argument("--to", dest="stop", choices=STEPS, default="checksum")
    a = ap.parse_args()
    names = list(STEPS)
    if names.index(a.start) > names.index(a.stop):
        ap.error(f"--from {a.start} comes after --to {a.stop}")
    for name in names[names.index(a.start): names.index(a.stop) + 1]:
        print(f"\n=== {name} ===")
        resume = f"Fix it, then continue with:  python main.py --from {name}"
        try:
            result = STEPS[name]()
        except EXPECTED as e:
            print(f"\n!! {name} failed: {e}\n{resume}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print(f"\n!! interrupted during {name} (API results so far are cached).\n"
                  f"Continue with:  python main.py --from {name}", file=sys.stderr)
            return 130
        except Exception:
            print(f"\n!! {name} failed with an unexpected error (traceback below).\n{resume}", file=sys.stderr)
            raise
        if name == "checksum" and result is False:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
