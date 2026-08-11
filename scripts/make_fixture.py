"""Generate the synthetic fixture used by tests and by `--offline` runs.

The fixture is NOT real data. It mimics the shape of the published dataset
(the same column names, the level/change series split, weekly Thursday dates)
so the pipeline and its tests can run without a network connection. Every
number in the repository's committed report comes from the real source.

Usage:
    python scripts/make_fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import FIXTURE_PATH  # noqa: E402

START = "2017-04-06"
GRADES = {
    # grade: (starting price, weekly volatility, floor, ceiling)
    "ron95": (2.05, 0.010, 1.90, 2.20),
    "ron97": (2.40, 0.055, 1.85, 5.10),
    "diesel": (2.05, 0.050, 1.90, 4.90),
}
SEED = 42


def build_frame() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(start=START, end=pd.Timestamp.today().normalize(), freq="W-THU")

    levels = {}
    for grade, (start_price, volatility, floor, ceiling) in GRADES.items():
        prices = [start_price]
        for _ in range(1, len(dates)):
            step = rng.normal(0, volatility)
            # Roughly a third of weeks hold steady, as the real series does.
            if rng.random() < 0.33:
                step = 0.0
            nxt = float(np.clip(prices[-1] + step, floor, ceiling))
            prices.append(round(nxt, 2))
        levels[grade] = prices

    level_frame = pd.DataFrame({"date": dates.date, "series_type": "level", **levels})

    change_frame = level_frame.copy()
    change_frame["series_type"] = "change"
    for grade in GRADES:
        change_frame[grade] = level_frame[grade].diff().round(2).fillna(0.0)

    return pd.concat([level_frame, change_frame], ignore_index=True)


def main() -> None:
    frame = build_frame()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(FIXTURE_PATH, index=False)
    print(f"Wrote {len(frame):,} synthetic rows to {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
