"""Pure diagnosis function for forging line delay detection.

This module contains the core diagnosis logic with zero framework dependencies.
The diagnose() function takes a piece dict, reference times, and a cause table,
and returns a structured diagnosis indicating which segments caused a delay.
"""

SEGMENTS = [
    "furnace_to_2nd_strike",
    "2nd_to_3rd_strike",
    "3rd_to_4th_strike",
    "4th_strike_to_aux_press",
    "aux_press_to_bath",
]

CAUSE_TABLE = {
    "furnace_to_2nd_strike": [
        "Billet pick", "gripper close", "grip retries",
        "trajectory", "permissions", "queues",
    ],
    "2nd_to_3rd_strike": [
        "Retraction", "gripper", "press/PLC handshake",
        "wait points", "regrip",
    ],
    "3rd_to_4th_strike": [
        "Retraction", "conservative trajectory", "synchronization",
        "positioning", "confirmations",
    ],
    "4th_strike_to_aux_press": [
        "Pick micro-corrections", "transfer",
        "queue at Auxiliary Press entry", "interlocks",
    ],
    "aux_press_to_bath": [
        "Retraction", "transport", "bath queues",
        "permissions", "bath deposit",
    ],
}


def _safe_subtract(a, b):
    """Subtract b from a, returning None if either operand is None."""
    if a is None or b is None:
        return None
    return a - b


def diagnose(piece: dict, reference_times: dict, cause_table: dict) -> dict:
    """Diagnose a single piece for delay.

    Args:
        piece: dict with keys piece_id, die_matrix, and 5 optional lifetime_* floats.
        reference_times: dict keyed by die_matrix (str) → segment → median float.
        cause_table: dict keyed by segment → list of probable cause strings.

    Returns:
        Response dict per the exam §1.4 schema.
    """
    die_matrix = piece["die_matrix"]
    refs = reference_times[str(die_matrix)]

    # Extract cumulative times (None if missing or null)
    c2 = piece.get("lifetime_2nd_strike_s")
    c3 = piece.get("lifetime_3rd_strike_s")
    c4 = piece.get("lifetime_4th_strike_s")
    caux = piece.get("lifetime_auxiliary_press_s")
    cbath = piece.get("lifetime_bath_s")

    # Compute partial times from cumulative (§1.5)
    partials = {
        "furnace_to_2nd_strike": c2,
        "2nd_to_3rd_strike": _safe_subtract(c3, c2),
        "3rd_to_4th_strike": _safe_subtract(c4, c3),
        "4th_strike_to_aux_press": _safe_subtract(caux, c4),
        "aux_press_to_bath": _safe_subtract(cbath, caux),
    }

    # Evaluate each segment
    segments = []
    probable_causes = []

    for seg in SEGMENTS:
        actual = partials[seg]
        reference = refs[seg]

        if actual is None:
            segments.append({
                "segment": seg,
                "actual_s": None,
                "reference_s": reference,
                "deviation_s": None,
                "penalized": None,
            })
            continue

        actual_rounded = round(actual, 1)
        deviation = round(actual - reference, 1) + 0.0  # +0.0 normalizes -0.0 to 0.0

        if deviation > 5.0:
            penalized = None
        elif deviation > 1.0:
            penalized = True
        else:
            penalized = False

        segments.append({
            "segment": seg,
            "actual_s": actual_rounded,
            "reference_s": reference,
            "deviation_s": deviation,
            "penalized": penalized,
        })

        if penalized is True:
            probable_causes.extend(cause_table[seg])

    delay = any(s["penalized"] is True for s in segments)

    return {
        "piece_id": piece["piece_id"],
        "die_matrix": die_matrix,
        "delay": delay,
        "segments": segments,
        "probable_causes": probable_causes,
    }
