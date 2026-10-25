"""Example read-only inspection helper, invoked with JSON argv arguments."""
import argparse
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv")
    args = parser.parse_args()
    frame = pd.read_csv(Path(args.csv))
    print({"rows": len(frame), "columns": list(frame.columns)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
