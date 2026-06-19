"""
pipeline.py — Train IsolationForest v1 on baseline.csv, log to MLflow, register with alias 'production'.

Usage:
    uv run python pipeline.py --data data/baseline.csv
"""

import argparse
import os

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow import MlflowClient
from sklearn.ensemble import IsolationForest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_NAME = "anomaly-detector"
FEATURES = ["latency_p99", "error_rate", "rps"]
CONTAMINATION = 0.05
N_ESTIMATORS = 100
RANDOM_STATE = 42


def train_and_register(data_path: str) -> str:
    """Train IsolationForest on data_path, log to MLflow, register as @production."""
    df = pd.read_csv(data_path)
    X = df[FEATURES].values

    mlflow.set_experiment("anomaly-detector-training")

    with mlflow.start_run() as run:
        # --- params ---
        mlflow.log_param("contamination", CONTAMINATION)
        mlflow.log_param("n_estimators", N_ESTIMATORS)
        mlflow.log_param("random_state", RANDOM_STATE)
        mlflow.log_param("data_path", data_path)
        mlflow.log_param("n_samples", len(df))

        # --- train ---
        model = IsolationForest(
            contamination=CONTAMINATION,
            n_estimators=N_ESTIMATORS,
            random_state=RANDOM_STATE,
        )
        model.fit(X)

        # --- metrics ---
        scores = model.decision_function(X)
        predictions = model.predict(X)  # 1 = normal, -1 = anomaly
        anomaly_count = (predictions == -1).sum()
        train_anomaly_rate = float(anomaly_count / len(predictions))
        mlflow.log_metric("train_anomaly_rate", train_anomaly_rate)
        mlflow.log_metric("feature_count", len(FEATURES))

        # --- artifact ---
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

        run_id = run.info.run_id
        print(f"[pipeline] Run ID: {run_id}")
        print(f"[pipeline] train_anomaly_rate={train_anomaly_rate:.4f}, features={FEATURES}")

    # --- set alias 'production' on the latest version ---
    client = MlflowClient()
    # get latest version registered for this model
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    latest = max(versions, key=lambda v: int(v.version))
    client.set_registered_model_alias(MODEL_NAME, "production", latest.version)
    print(f"[pipeline] Registered '{MODEL_NAME}' v{latest.version} with alias @production")
    return latest.version


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/baseline.csv")
    args = parser.parse_args()

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)

    version = train_and_register(args.data)
    print(f"[pipeline] Done. Model version {version} is now @production.")
