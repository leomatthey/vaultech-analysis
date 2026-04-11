"""
Forging Line — Piece Travel Time Dashboard

Displays processed pieces with predicted bath time and per-stage
timing detail.

Usage:
    uv run streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from vaultech_analysis.inference import Predictor

GOLD_FILE = PROJECT_ROOT / "data" / "gold" / "pieces.parquet"

# Column definitions — process order
PARTIAL_COLS = [
    "partial_furnace_to_2nd_strike_s",
    "partial_2nd_to_3rd_strike_s",
    "partial_3rd_to_4th_strike_s",
    "partial_4th_strike_to_auxiliary_press_s",
    "partial_auxiliary_press_to_bath_s",
]
PARTIAL_LABELS = [
    "Furnace → 2nd strike",
    "2nd strike → 3rd strike",
    "3rd strike → 4th strike",
    "4th strike → Aux. press",
    "Aux. press → Bath",
]
CUMULATIVE_COLS = [
    "lifetime_2nd_strike_s",
    "lifetime_3rd_strike_s",
    "lifetime_4th_strike_s",
    "lifetime_auxiliary_press_s",
    "lifetime_bath_s",
]
CUMULATIVE_LABELS = [
    "2nd strike (1st op)",
    "3rd strike (2nd op)",
    "4th strike (drill)",
    "Auxiliary press",
    "Bath",
]


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_resource
def load_predictor():
    """Load the Predictor once — holds the XGBoost model, metadata, and
    reference profiles per die matrix. cache_resource because it's a heavy
    stateful object that should be shared across reruns."""
    return Predictor()


@st.cache_data
def load_data(_predictor):
    """Read the gold parquet and add prediction columns. Leading underscore
    on _predictor tells Streamlit to skip hashing (Predictor is not hashable).
    Runs once; all filtering happens in-memory on the returned DataFrame."""
    df = pd.read_parquet(GOLD_FILE)
    df["predicted_bath_s"] = _predictor.predict_batch(df)
    df["prediction_error_s"] = df["lifetime_bath_s"] - df["predicted_bath_s"]
    return df


@st.cache_data
def compute_p90(df):
    """90th percentile bath time per matrix — threshold for 'slow' pieces."""
    return df.groupby("die_matrix")["lifetime_bath_s"].quantile(0.9).to_dict()


# ---------------------------------------------------------------------------
# Page config and data loading
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Forging Line Dashboard", layout="wide")
st.title("Forging Line — Piece Travel Time Dashboard")

predictor = load_predictor()
df = load_data(predictor)
p90 = compute_p90(df)


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

# Die matrix — multiselect so users can compare any combination of matrices
matrices = sorted(df["die_matrix"].unique().tolist())
selected_matrices = st.sidebar.multiselect(
    "Die Matrix",
    options=matrices,
    default=matrices,
    format_func=lambda x: str(x),
)

# Apply matrix filter first so the date range adjusts to the selected matrices
filtered_df = df.copy()
if selected_matrices:
    filtered_df = filtered_df[filtered_df["die_matrix"].isin(selected_matrices)]

# Date range — bounds adjust to the matrix-filtered data
min_date = filtered_df["timestamp"].min().date()
max_date = filtered_df["timestamp"].max().date()
date_range = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

# Slow pieces toggle
show_slow = st.sidebar.checkbox("Show slow pieces only (bath > p90)")

# Apply remaining filters
if len(date_range) == 2:
    start_date, end_date = date_range
    filtered_df = filtered_df[
        filtered_df["timestamp"].dt.date.between(start_date, end_date)
    ]

if show_slow:
    filtered_df = filtered_df[
        filtered_df.apply(
            lambda r: r["lifetime_bath_s"] > p90.get(r["die_matrix"], float("inf")),
            axis=1,
        )
    ]

# Guard for empty result
if filtered_df.empty:
    st.warning("No pieces match the current filters.")
    st.stop()


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Pieces", f"{len(filtered_df):,}")
col2.metric("Median Bath Time", f"{filtered_df['lifetime_bath_s'].median():.1f} s")
col3.metric("Median Predicted", f"{filtered_df['predicted_bath_s'].median():.1f} s")
col4.metric("MAE", f"{filtered_df['prediction_error_s'].abs().mean():.2f} s")


# ---------------------------------------------------------------------------
# Pieces table with row selection
# ---------------------------------------------------------------------------

display_cols = [
    "timestamp", "piece_id", "die_matrix",
    "lifetime_bath_s", "predicted_bath_s", "prediction_error_s",
    "oee_cycle_time_s",
]

event = st.dataframe(
    filtered_df[display_cols].reset_index(drop=True),
    on_select="rerun",
    selection_mode="single-row",
    height=400,
    hide_index=True,
    column_config={
        "timestamp": st.column_config.DatetimeColumn(
            "Timestamp", format="YYYY-MM-DD HH:mm:ss",
        ),
        "piece_id": "Piece ID",
        "die_matrix": "Die Matrix",
        "lifetime_bath_s": st.column_config.NumberColumn(
            "Bath Time (s)", format="%.1f",
        ),
        "predicted_bath_s": st.column_config.NumberColumn(
            "Predicted (s)", format="%.1f",
        ),
        "prediction_error_s": st.column_config.NumberColumn(
            "Error (s)", format="%.2f",
        ),
        "oee_cycle_time_s": st.column_config.NumberColumn(
            "OEE (s)", format="%.1f",
        ),
    },
    key="pieces_table",
)


# ---------------------------------------------------------------------------
# Piece detail panel
# ---------------------------------------------------------------------------

selected_rows = event.selection.rows
if not selected_rows:
    st.info("Select a piece from the table above to see its per-stage timing detail.")
    st.stop()

# Get the selected piece from the filtered (and reset-index) DataFrame
piece = filtered_df.reset_index(drop=True).iloc[selected_rows[0]]
matrix = int(piece["die_matrix"])

st.subheader(f"Piece {piece['piece_id']} — Matrix {matrix}")

ref_cum = predictor.get_reference_cumulative(matrix)
ref_par = predictor.get_reference_partials(matrix)

# --- Helper: style deviation tables with background color ---
def _style_status(df_styled):
    """Apply green/red background to the Status column."""
    def _color(val):
        if val == "OK":
            return "background-color: #d4edda; color: #155724"
        elif val == "Slow":
            return "background-color: #f8d7da; color: #721c24"
        return ""
    return df_styled.map(_color, subset=["Status"])


# --- Cumulative times vs reference ---
st.markdown("#### Cumulative travel times vs reference")

cum_rows = []
for col, label in zip(CUMULATIVE_COLS, CUMULATIVE_LABELS):
    actual = piece[col]
    reference = ref_cum.get(col)
    deviation = actual - reference if (pd.notna(actual) and reference is not None) else None
    status = None
    if deviation is not None:
        status = "Slow" if deviation > 0.5 else "OK"
    cum_rows.append({
        "Stage": label,
        "Actual (s)": round(actual, 2) if pd.notna(actual) else None,
        "Reference (s)": round(reference, 2) if reference is not None else None,
        "Deviation (s)": round(deviation, 2) if deviation is not None else None,
        "Status": status,
    })

cum_df = pd.DataFrame(cum_rows)
st.dataframe(_style_status(cum_df.style), hide_index=True, use_container_width=True)

# --- Partial times vs reference ---
st.markdown("#### Partial times vs reference")

par_rows = []
for col, label in zip(PARTIAL_COLS, PARTIAL_LABELS):
    actual = piece[col]
    reference = ref_par.get(col)
    deviation = actual - reference if (pd.notna(actual) and reference is not None) else None
    status = None
    if deviation is not None:
        status = "Slow" if deviation > 0.5 else "OK"
    par_rows.append({
        "Segment": label,
        "Actual (s)": round(actual, 2) if pd.notna(actual) else None,
        "Reference (s)": round(reference, 2) if reference is not None else None,
        "Deviation (s)": round(deviation, 2) if deviation is not None else None,
        "Status": status,
    })

par_df = pd.DataFrame(par_rows)
st.dataframe(_style_status(par_df.style), hide_index=True, use_container_width=True)

# --- Process synoptic bar chart ---
st.markdown("#### Process synoptic: Actual vs Reference")

chart_actuals = [piece[c] for c in PARTIAL_COLS]
chart_refs = [ref_par.get(c, 0) for c in PARTIAL_COLS]

chart_data = pd.DataFrame({
    "Segment": PARTIAL_LABELS * 2,
    "Time (s)": chart_actuals + chart_refs,
    "Type": ["Actual"] * len(PARTIAL_COLS) + ["Reference"] * len(PARTIAL_COLS),
})

# Drop rows with NaN times (missing 4th strike partial)
chart_data = chart_data.dropna(subset=["Time (s)"])

chart = (
    alt.Chart(chart_data)
    .mark_bar()
    .encode(
        x=alt.X("Segment:N", sort=PARTIAL_LABELS, title="Process Segment"),
        y=alt.Y("Time (s):Q", title="Time (s)"),
        color=alt.Color(
            "Type:N",
            scale=alt.Scale(
                domain=["Actual", "Reference"],
                range=["#FF6B6B", "#4ECDC4"],
            ),
        ),
        xOffset="Type:N",
    )
    .properties(height=350)
)

st.altair_chart(chart, use_container_width=True)
