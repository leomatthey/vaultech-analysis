"""Compute per-matrix reference (median) partial times from the gold dataset.

Reads the exam's pieces.parquet, computes medians per die_matrix for each
partial time column, maps column names to the exam's segment naming convention,
rounds to 1 decimal, and writes api/reference_times.json.

Usage:
    python api/scripts/compute_references.py
"""

import json
from pathlib import Path

import pandas as pd

PARQUET_PATH = Path(__file__).resolve().parent.parent.parent / "EXAM MATERIAL" / "pieces.parquet"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reference_times.json"

# Map parquet column names → exam segment names
COLUMN_TO_SEGMENT = {
    "partial_furnace_to_2nd_strike_s": "furnace_to_2nd_strike",
    "partial_2nd_to_3rd_strike_s": "2nd_to_3rd_strike",
    "partial_3rd_to_4th_strike_s": "3rd_to_4th_strike",
    "partial_4th_strike_to_auxiliary_press_s": "4th_strike_to_aux_press",
    "partial_auxiliary_press_to_bath_s": "aux_press_to_bath",
}


def main():
    df = pd.read_parquet(PARQUET_PATH)
    partial_cols = list(COLUMN_TO_SEGMENT.keys())

    medians = df.groupby("die_matrix")[partial_cols].median()

    reference_times = {}
    for matrix in sorted(medians.index):
        reference_times[str(matrix)] = {
            COLUMN_TO_SEGMENT[col]: round(float(medians.loc[matrix, col]), 1)
            for col in partial_cols
        }

    OUTPUT_PATH.write_text(json.dumps(reference_times, indent=2) + "\n")
    print(f"Wrote {OUTPUT_PATH}")
    print(json.dumps(reference_times, indent=2))


if __name__ == "__main__":
    main()
