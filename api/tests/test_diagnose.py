"""Tests for the diagnose() pure function.

24 unit tests (4 matrices x 6 scenarios) + 12 extra-credit tests + 10 golden tests.
All tests call diagnose() directly — no HTTP server needed.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR / "src"))

from diagnose import CAUSE_TABLE, SEGMENTS, diagnose

REFERENCE_TIMES_PATH = API_DIR / "reference_times.json"
VALIDATION_CSV_PATH = API_DIR / "validation_pieces.csv"
VALIDATION_JSON_PATH = API_DIR / "validation_expected.json"

OFFSET = 2.0  # deviation that triggers penalized=true (> 1.0 and <= 5.0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def reference_times():
    return json.loads(REFERENCE_TIMES_PATH.read_text())


@pytest.fixture(scope="session")
def cause_table():
    return CAUSE_TABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_piece(matrix: int, refs: dict, penalized_segs: list[str],
                null_cumulative: str | None = None) -> dict:
    """Build a piece dict with cumulative times from reference + offsets.

    Args:
        matrix: die_matrix value.
        refs: reference times for this matrix (segment -> float).
        penalized_segs: segments to penalize (add OFFSET to).
        null_cumulative: if set, this cumulative field is set to None.
    """
    partials = {}
    for seg in SEGMENTS:
        partials[seg] = refs[seg] + OFFSET if seg in penalized_segs else refs[seg]

    c2 = partials["furnace_to_2nd_strike"]
    c3 = c2 + partials["2nd_to_3rd_strike"]
    c4 = c3 + partials["3rd_to_4th_strike"]
    caux = c4 + partials["4th_strike_to_aux_press"]
    cbath = caux + partials["aux_press_to_bath"]

    piece = {
        "piece_id": "TEST",
        "die_matrix": matrix,
        "lifetime_2nd_strike_s": round(c2, 1),
        "lifetime_3rd_strike_s": round(c3, 1),
        "lifetime_4th_strike_s": round(c4, 1),
        "lifetime_auxiliary_press_s": round(caux, 1),
        "lifetime_bath_s": round(cbath, 1),
    }

    if null_cumulative:
        piece[null_cumulative] = None

    return piece


def round_floats(obj):
    """Recursively round all floats in a nested structure to 1 decimal."""
    if isinstance(obj, float):
        return round(obj, 1)
    if isinstance(obj, dict):
        return {k: round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# 24 unit tests: 4 matrices x 6 scenarios
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("matrix", [4974, 5052, 5090, 5091])
@pytest.mark.parametrize("scenario,penalized_seg", [
    ("all_ok", None),
    ("furnace_to_2nd_strike", "furnace_to_2nd_strike"),
    ("2nd_to_3rd_strike", "2nd_to_3rd_strike"),
    ("3rd_to_4th_strike", "3rd_to_4th_strike"),
    ("4th_strike_to_aux_press", "4th_strike_to_aux_press"),
    ("aux_press_to_bath", "aux_press_to_bath"),
])
def test_single_segment(matrix, scenario, penalized_seg, reference_times, cause_table):
    refs = reference_times[str(matrix)]
    penalized_list = [penalized_seg] if penalized_seg else []
    piece = build_piece(matrix, refs, penalized_list)

    result = diagnose(piece, reference_times, cause_table)

    if penalized_seg is None:
        # All-OK: no delay, no penalized segments, no causes
        assert result["delay"] is False
        for seg in result["segments"]:
            assert seg["penalized"] is False
        assert result["probable_causes"] == []
    else:
        # Single segment penalized
        assert result["delay"] is True
        for seg in result["segments"]:
            if seg["segment"] == penalized_seg:
                assert seg["penalized"] is True
                assert seg["deviation_s"] == OFFSET
            else:
                assert seg["penalized"] is False
        assert result["probable_causes"] == cause_table[penalized_seg]


# ---------------------------------------------------------------------------
# Extra credit: multi-segment, null propagation, sensor anomaly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("matrix", [4974, 5052, 5090, 5091])
def test_multi_segment_delay(matrix, reference_times, cause_table):
    """Two segments penalized simultaneously."""
    refs = reference_times[str(matrix)]
    penalized = ["furnace_to_2nd_strike", "aux_press_to_bath"]
    piece = build_piece(matrix, refs, penalized)

    result = diagnose(piece, reference_times, cause_table)

    assert result["delay"] is True
    penalized_segs = [s["segment"] for s in result["segments"] if s["penalized"] is True]
    assert penalized_segs == penalized
    expected_causes = cause_table["furnace_to_2nd_strike"] + cause_table["aux_press_to_bath"]
    assert result["probable_causes"] == expected_causes


@pytest.mark.parametrize("matrix", [4974, 5052, 5090, 5091])
def test_null_propagation(matrix, reference_times, cause_table):
    """Missing lifetime_4th_strike_s nullifies two adjacent segments."""
    refs = reference_times[str(matrix)]
    piece = build_piece(matrix, refs, [], null_cumulative="lifetime_4th_strike_s")

    result = diagnose(piece, reference_times, cause_table)

    for seg in result["segments"]:
        if seg["segment"] in ("3rd_to_4th_strike", "4th_strike_to_aux_press"):
            assert seg["actual_s"] is None
            assert seg["deviation_s"] is None
            assert seg["penalized"] is None
        else:
            assert seg["penalized"] is False

    assert result["delay"] is False
    assert result["probable_causes"] == []


@pytest.mark.parametrize("matrix", [4974, 5052, 5090, 5091])
def test_sensor_anomaly(matrix, reference_times, cause_table):
    """Deviation > 5.0 is treated as sensor anomaly (penalized=null, not true)."""
    refs = reference_times[str(matrix)]
    # Build a piece where furnace_to_2nd_strike has deviation > 5.0
    piece = build_piece(matrix, refs, [])
    piece["lifetime_2nd_strike_s"] = round(refs["furnace_to_2nd_strike"] + 5.5, 1)

    result = diagnose(piece, reference_times, cause_table)

    anomaly_seg = result["segments"][0]
    assert anomaly_seg["segment"] == "furnace_to_2nd_strike"
    assert anomaly_seg["penalized"] is None  # anomaly, not penalized

    # Only anomaly — no true penalization, so no delay
    assert result["delay"] is False
    assert result["probable_causes"] == []


# ---------------------------------------------------------------------------
# Golden test: 10 validation pieces
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def validation_data():
    """Load validation CSV and expected JSON."""
    pieces = []
    with open(VALIDATION_CSV_PATH) as f:
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
            pieces.append(piece)

    expected = json.loads(VALIDATION_JSON_PATH.read_text())
    return pieces, expected


@pytest.mark.parametrize("index", range(10))
def test_golden(index, reference_times, cause_table, validation_data):
    """Each validation piece must match the expected output after rounding to 1 decimal."""
    pieces, expected = validation_data
    piece = pieces[index]
    expected_output = expected[index]

    result = diagnose(piece, reference_times, cause_table)

    assert round_floats(result) == round_floats(expected_output)
