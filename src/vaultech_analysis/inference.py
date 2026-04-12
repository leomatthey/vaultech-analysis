"""
Inference service for predicting total piece travel time.

Supports two modes controlled by the SAGEMAKER_ENDPOINT_NAME env var:
- Local mode (default): loads the XGBoost model from disk
- Remote mode: calls the SageMaker endpoint via boto3

Usage as CLI:
    uv run python -m vaultech_analysis.inference --die-matrix 5052 --strike2 18.3 --oee 13.5

Usage as module (for Streamlit):
    from vaultech_analysis.inference import Predictor
    predictor = Predictor()
    result = predictor.predict(die_matrix=5052, lifetime_2nd_strike_s=18.3, oee_cycle_time_s=13.5)
"""

import argparse
import json
import os
import time
from pathlib import Path

import boto3
import pandas as pd
from xgboost import XGBRegressor


MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
GOLD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "gold" / "pieces.parquet"


class Predictor:
    """Loads the trained model and provides predictions.

    Dual-mode: if SAGEMAKER_ENDPOINT_NAME is set, predictions are made via
    the SageMaker endpoint (remote mode). Otherwise, the model is loaded
    from disk (local mode). This allows:
    - Local development and tests to work without AWS credentials
    - The Fargate-deployed app to use the SageMaker endpoint
    """

    def __init__(self, model_dir: Path = MODEL_DIR, gold_file: Path = GOLD_FILE):
        # Check for remote mode via environment variable
        self._endpoint_name = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
        self._region = os.environ.get("AWS_DEFAULT_REGION", "eu-west-1")
        self._remote = self._endpoint_name is not None

        # In remote mode, create a SageMaker runtime client instead of
        # loading the model binary. The client is reused for all predictions.
        if self._remote:
            self._runtime_client = boto3.client(
                "sagemaker-runtime", region_name=self._region
            )
            self._model = None
        else:
            self._model = XGBRegressor()
            self._model.load_model(model_dir / "xgboost_bath_predictor.ubj")
            self._runtime_client = None

        # Metadata is needed in both modes: feature ordering, valid matrices,
        # OEE median for imputation, and model metrics for the response.
        with open(model_dir / "model_metadata.json") as f:
            self._metadata = json.load(f)

        self._features: list[str] = self._metadata["features"]
        self._oee_median: float = self._metadata["oee_median_for_imputation"]
        self._valid_matrices: set[int] = set(self._metadata["die_matrices"])
        self._metrics: dict = self._metadata["metrics"]

        # Reference profiles from gold parquet — used by the Streamlit detail
        # panel in both modes to show piece vs matrix reference.
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

        In remote mode, includes extra keys for the debug panel:
        sagemaker_payload, sagemaker_raw_response, sagemaker_latency_ms.
        """
        if die_matrix not in self._valid_matrices:
            return {
                "error": (
                    f"Unknown die_matrix {die_matrix}. "
                    f"Valid matrices: {sorted(self._valid_matrices)}"
                )
            }

        oee_for_model = (
            oee_cycle_time_s if oee_cycle_time_s is not None else self._oee_median
        )

        if self._remote:
            # Remote mode: call SageMaker endpoint with CSV payload
            payload = f"{die_matrix},{lifetime_2nd_strike_s},{oee_for_model}"

            start = time.perf_counter()
            response = self._runtime_client.invoke_endpoint(
                EndpointName=self._endpoint_name,
                ContentType="text/csv",
                Body=payload,
            )
            latency_ms = (time.perf_counter() - start) * 1000

            raw_response = response["Body"].read().decode("utf-8").strip()
            prediction = float(raw_response)

            return {
                "predicted_bath_time_s": round(prediction, 2),
                "die_matrix": die_matrix,
                "lifetime_2nd_strike_s": lifetime_2nd_strike_s,
                "oee_cycle_time_s": oee_cycle_time_s,
                "model_metrics": self._metrics,
                "sagemaker_payload": payload,
                "sagemaker_raw_response": raw_response,
                "sagemaker_latency_ms": round(latency_ms, 1),
            }
        else:
            # Local mode: run model.predict() directly
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

        In remote mode, sends all rows as multi-line CSV in a single
        invoke_endpoint call (~2.7MB for 167k rows, within the 6MB limit).
        In local mode, uses the model directly (vectorized).
        """
        X = df[self._features].copy()
        X["oee_cycle_time_s"] = X["oee_cycle_time_s"].fillna(self._oee_median)

        if self._remote:
            # Send in chunks to stay within endpoint payload limits.
            # Each row is ~20 bytes; chunks of 5000 rows (~100KB) are safe.
            CHUNK_SIZE = 5000
            all_predictions = []

            for start in range(0, len(X), CHUNK_SIZE):
                chunk = X.iloc[start:start + CHUNK_SIZE]
                csv_lines = chunk.apply(
                    lambda r: f"{int(r['die_matrix'])},{r['lifetime_2nd_strike_s']},{r['oee_cycle_time_s']}",
                    axis=1,
                )
                payload = "\n".join(csv_lines)

                response = self._runtime_client.invoke_endpoint(
                    EndpointName=self._endpoint_name,
                    ContentType="text/csv",
                    Body=payload,
                )
                raw = response["Body"].read().decode("utf-8").strip()
                all_predictions.extend(float(v) for v in raw.split("\n"))

            return pd.Series(all_predictions, index=df.index, name="predicted_bath_time_s")
        else:
            predictions = self._model.predict(X)
            return pd.Series(predictions, index=df.index, name="predicted_bath_time_s")

    def get_reference_cumulative(self, die_matrix: int) -> dict:
        """Return median cumulative times for the given die matrix."""
        return self._reference_cumulative.get(die_matrix, {})

    def get_reference_partials(self, die_matrix: int) -> dict:
        """Return median partial times for the given die matrix."""
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
