"""
Inference service for predicting total piece travel time.

Loads the trained XGBoost model and provides predictions.

Usage as CLI:
    uv run python -m vaultech_analysis.inference --die-matrix 5052 --strike2 18.3 --oee 13.5

Usage as module (for Streamlit):
    from vaultech_analysis.inference import Predictor
    predictor = Predictor()
    result = predictor.predict(die_matrix=5052, lifetime_2nd_strike_s=18.3, oee_cycle_time_s=13.5)
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from xgboost import XGBRegressor


MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
GOLD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "gold" / "pieces.parquet"


class Predictor:
    """Loads the trained model and provides predictions.

    Loads three things at init:
      1. The trained XGBoost model (xgboost_bath_predictor.json)
      2. The model metadata (features, metrics, valid matrices, OEE median)
      3. Reference cumulative + partial profiles per die matrix (for Streamlit)

    Loading once at init means downstream consumers (CLI, Streamlit, tests)
    don't pay the cost on every prediction call.
    """

    def __init__(self, model_dir: Path = MODEL_DIR, gold_file: Path = GOLD_FILE):
        # 1. Load the trained XGBoost model written by notebook 05 in
        #    native JSON format. Native JSON is portable across XGBoost
        #    versions and is what the SageMaker container expects in Task 10.
        self._model = XGBRegressor()
        self._model.load_model(model_dir / "xgboost_bath_predictor.json")

        # 2. Load the metadata produced alongside the model.
        #    This drives feature ordering, matrix validation, and the
        #    OEE imputation value used both here and in tests.
        with open(model_dir / "model_metadata.json") as f:
            self._metadata = json.load(f)

        self._features: list[str] = self._metadata["features"]
        self._oee_median: float = self._metadata["oee_median_for_imputation"]
        self._valid_matrices: set[int] = set(self._metadata["die_matrices"])
        self._metrics: dict = self._metadata["metrics"]

        # 3. Compute reference profiles per die matrix from the gold parquet.
        #    The Streamlit detail panel (Task 8) uses these to show
        #    "how does this piece compare to the matrix's typical piece".
        #    Stored as nested dicts so consumers can do
        #    predictor.get_reference_cumulative(5052)["lifetime_bath_s"].
        self._reference_cumulative: dict = {}
        self._reference_partials: dict = {}

        if gold_file is not None and gold_file.exists():
            df = pd.read_parquet(gold_file)
            cumulative_cols = [
                "lifetime_2nd_strike_s", "lifetime_3rd_strike_s",
                "lifetime_4th_strike_s", "lifetime_auxiliary_press_s",
                "lifetime_bath_s",
            ]
            partial_cols = [
                "partial_furnace_to_2nd_strike_s",
                "partial_2nd_to_3rd_strike_s",
                "partial_3rd_to_4th_strike_s",
                "partial_4th_strike_to_auxiliary_press_s",
                "partial_auxiliary_press_to_bath_s",
            ]
            self._reference_cumulative = (
                df.groupby("die_matrix")[cumulative_cols].median().to_dict("index")
            )
            self._reference_partials = (
                df.groupby("die_matrix")[partial_cols].median().to_dict("index")
            )

    def predict(
        self,
        die_matrix: int,
        lifetime_2nd_strike_s: float,
        oee_cycle_time_s: float | None = None,
    ) -> dict:
        """Predict total bath time from early-stage features.

        Args:
            die_matrix: integer matrix ID (must be one of the trained matrices)
            lifetime_2nd_strike_s: cumulative time at the 2nd strike (~17-19s typical)
            oee_cycle_time_s: rolling production rate; if None, the median is
                used internally but the response echoes None to make missingness
                visible to the caller.

        Returns:
            dict with the prediction, the inputs (echoed), and the model metrics.
            Returns {"error": "..."} for unknown die_matrix values rather than
            silently producing garbage predictions.
        """
        # Validate die_matrix early — return a clean error dict so callers
        # don't have to wrap predict() in try/except for the common case
        # of an invalid matrix coming from a UI dropdown or external system.
        if die_matrix not in self._valid_matrices:
            return {
                "error": (
                    f"Unknown die_matrix {die_matrix}. "
                    f"Valid matrices: {sorted(self._valid_matrices)}"
                )
            }

        # OEE imputation. The model needs a real number, but we deliberately
        # echo the original value (possibly None) in the response so the
        # consumer can tell whether OEE was provided or imputed.
        oee_for_model = (
            oee_cycle_time_s if oee_cycle_time_s is not None else self._oee_median
        )

        # Build a single-row DataFrame in the exact column order the model
        # was trained on. XGBoost binds features by name when given a
        # DataFrame, but ordering by self._features is defensive.
        row = pd.DataFrame([{
            "die_matrix": die_matrix,
            "lifetime_2nd_strike_s": lifetime_2nd_strike_s,
            "oee_cycle_time_s": oee_for_model,
        }])[self._features]

        prediction = float(self._model.predict(row)[0])

        return {
            "predicted_bath_time_s": round(prediction, 2),
            "die_matrix": die_matrix,
            "lifetime_2nd_strike_s": lifetime_2nd_strike_s,
            "oee_cycle_time_s": oee_cycle_time_s,
            "model_metrics": self._metrics,
        }

    def predict_batch(self, df: pd.DataFrame) -> pd.Series:
        """Predict bath time for a DataFrame of pieces.

        Vectorized: one model call for all rows. Missing OEE values are
        filled with the median (same imputation rule as single-row predict).
        Returns a Series of predictions indexed like the input DataFrame.

        This is what Streamlit calls to get a "predicted_bath_s" column on
        the entire gold dataset in one shot.
        """
        # Copy so we don't mutate the caller's DataFrame
        X = df[self._features].copy()
        X["oee_cycle_time_s"] = X["oee_cycle_time_s"].fillna(self._oee_median)

        predictions = self._model.predict(X)
        return pd.Series(predictions, index=df.index, name="predicted_bath_time_s")

    def get_reference_cumulative(self, die_matrix: int) -> dict:
        """Return median cumulative times for the given die matrix.

        Used by the Streamlit detail panel to compare a piece against
        its matrix's typical cumulative profile.
        """
        return self._reference_cumulative.get(die_matrix, {})

    def get_reference_partials(self, die_matrix: int) -> dict:
        """Return median partial times for the given die matrix.

        Used by the Streamlit detail panel to compare a piece against
        its matrix's typical partial (per-segment) profile.
        """
        return self._reference_partials.get(die_matrix, {})


def main():
    """CLI entry point: predict for a single piece and print JSON to stdout."""
    parser = argparse.ArgumentParser(
        description="Predict total bath time for a forging line piece."
    )
    parser.add_argument(
        "--die-matrix", type=int, required=True,
        help="Die matrix ID (e.g. 4974, 5052, 5090, 5091)",
    )
    parser.add_argument(
        "--strike2", type=float, required=True,
        help="Cumulative time at the 2nd strike, in seconds",
    )
    parser.add_argument(
        "--oee", type=float, default=None,
        help="OEE cycle time in seconds (optional, defaults to median)",
    )
    args = parser.parse_args()

    predictor = Predictor()
    result = predictor.predict(
        die_matrix=args.die_matrix,
        lifetime_2nd_strike_s=args.strike2,
        oee_cycle_time_s=args.oee,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
