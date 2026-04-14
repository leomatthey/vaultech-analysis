"""Generate validation_pieces.csv and validation_expected.json.

Constructs 10 validation pieces programmatically from reference_times.json,
then runs each through diagnose() to produce the expected API output.

Usage:
    python api/scripts/generate_validation.py
"""

import csv
import json
import math
import sys
from pathlib import Path

# Allow importing from api/src/
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR / "src"))

from diagnose import CAUSE_TABLE, diagnose

REF_PATH = API_DIR / "reference_times.json"
CSV_PATH = API_DIR / "validation_pieces.csv"
JSON_PATH = API_DIR / "validation_expected.json"

OFFSET = 2.0  # Added to reference to create a penalized segment (> 1.0, <= 5.0)


def build_cumulative(refs: dict, penalized_segments: list[str]) -> dict:
    """Build cumulative lifetime values from reference partials + offsets.

    For each segment, the partial time is reference + OFFSET if the segment
    is in penalized_segments, otherwise exactly the reference value.
    Cumulative times are the running sum of partials.

    Returns dict with the 5 lifetime_* keys.
    """
    seg_names = [
        "furnace_to_2nd_strike",
        "2nd_to_3rd_strike",
        "3rd_to_4th_strike",
        "4th_strike_to_aux_press",
        "aux_press_to_bath",
    ]

    partials = []
    for seg in seg_names:
        base = refs[seg]
        partials.append(base + OFFSET if seg in penalized_segments else base)

    # Convert partials → cumulative
    c2 = partials[0]
    c3 = c2 + partials[1]
    c4 = c3 + partials[2]
    caux = c4 + partials[3]
    cbath = caux + partials[4]

    return {
        "lifetime_2nd_strike_s": round(c2, 1),
        "lifetime_3rd_strike_s": round(c3, 1),
        "lifetime_4th_strike_s": round(c4, 1),
        "lifetime_auxiliary_press_s": round(caux, 1),
        "lifetime_bath_s": round(cbath, 1),
    }


# --- Define the 10 validation pieces ---

PIECE_DEFINITIONS = [
    # P001-P004: All-OK, one per matrix
    {"piece_id": "P001", "die_matrix": 4974, "penalized": []},
    {"piece_id": "P002", "die_matrix": 5052, "penalized": []},
    {"piece_id": "P003", "die_matrix": 5090, "penalized": []},
    {"piece_id": "P004", "die_matrix": 5091, "penalized": []},
    # P005-P009: Single segment penalized
    {"piece_id": "P005", "die_matrix": 4974, "penalized": ["furnace_to_2nd_strike"]},
    {"piece_id": "P006", "die_matrix": 5052, "penalized": ["2nd_to_3rd_strike"]},
    {"piece_id": "P007", "die_matrix": 5090, "penalized": ["3rd_to_4th_strike"]},
    {"piece_id": "P008", "die_matrix": 5091, "penalized": ["4th_strike_to_aux_press"]},
    {"piece_id": "P009", "die_matrix": 4974, "penalized": ["aux_press_to_bath"]},
    # P010: Multi-segment + NULL (handled separately)
    {"piece_id": "P010", "die_matrix": 5090, "penalized": ["furnace_to_2nd_strike", "2nd_to_3rd_strike"], "null_cumulative": "lifetime_4th_strike_s"},
]


def main():
    reference_times = json.loads(REF_PATH.read_text())

    # --- Part A: Build validation_pieces.csv ---
    csv_rows = []
    fieldnames = [
        "piece_id", "die_matrix",
        "lifetime_2nd_strike_s", "lifetime_3rd_strike_s",
        "lifetime_4th_strike_s", "lifetime_auxiliary_press_s",
        "lifetime_bath_s",
    ]

    for defn in PIECE_DEFINITIONS:
        matrix = defn["die_matrix"]
        refs = reference_times[str(matrix)]
        penalized = defn["penalized"]
        null_col = defn.get("null_cumulative")

        if null_col:
            # P010: build cumulatives for the non-null parts manually
            # furnace_to_2nd_strike partial = ref + OFFSET (penalized)
            p1 = refs["furnace_to_2nd_strike"] + OFFSET
            # 2nd_to_3rd_strike partial = ref + OFFSET (penalized)
            p2 = refs["2nd_to_3rd_strike"] + OFFSET
            # aux_press_to_bath partial = ref (OK)
            p5 = refs["aux_press_to_bath"]

            c2 = round(p1, 1)
            c3 = round(c2 + p2, 1)
            # c4 is NULL — we skip it
            # caux needs a value so aux_press_to_bath can compute
            caux = round(c3 + refs["3rd_to_4th_strike"] + refs["4th_strike_to_aux_press"], 1)
            cbath = round(caux + p5, 1)

            row = {
                "piece_id": defn["piece_id"],
                "die_matrix": matrix,
                "lifetime_2nd_strike_s": c2,
                "lifetime_3rd_strike_s": c3,
                "lifetime_4th_strike_s": "",  # NULL
                "lifetime_auxiliary_press_s": caux,
                "lifetime_bath_s": cbath,
            }
        else:
            cumulatives = build_cumulative(refs, penalized)
            row = {
                "piece_id": defn["piece_id"],
                "die_matrix": matrix,
                **cumulatives,
            }

        csv_rows.append(row)

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"Wrote {CSV_PATH}")

    # --- Part B: Generate validation_expected.json ---
    # Re-read the CSV and run diagnose() on each row
    expected = []
    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            piece = {
                "piece_id": row["piece_id"],
                "die_matrix": int(row["die_matrix"]),
            }
            for col in ["lifetime_2nd_strike_s", "lifetime_3rd_strike_s",
                        "lifetime_4th_strike_s", "lifetime_auxiliary_press_s",
                        "lifetime_bath_s"]:
                val = row[col].strip()
                piece[col] = float(val) if val != "" else None

            result = diagnose(piece, reference_times, CAUSE_TABLE)
            expected.append(result)

    JSON_PATH.write_text(json.dumps(expected, indent=2) + "\n")
    print(f"Wrote {JSON_PATH}")

    # --- Quick verification ---
    for item in expected:
        pid = item["piece_id"]
        delay = item["delay"]
        pen_segs = [s["segment"] for s in item["segments"] if s["penalized"] is True]
        null_segs = [s["segment"] for s in item["segments"] if s["penalized"] is None]
        print(f"  {pid}: delay={delay}, penalized={pen_segs}, null={null_segs}, causes={len(item['probable_causes'])}")


if __name__ == "__main__":
    main()
