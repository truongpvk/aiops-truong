"""
retrain.py — Orchestrator: drift detect → train v2 → staging → approval gate
             → promote → /reload → post-deploy monitoring → auto-rollback.

Usage:
    uv run python retrain.py \
        --reference data/baseline.csv \
        --current   data/drifted.csv \
        --holdout   data/holdout.csv \
        --post-deploy-eval data/post_deploy_eval.csv

Flags:
    --auto-approve          skip the [y/N] gate (for CI)
    --serve-url             URL of serve.py (default: http://localhost:8000)
    --threshold             drift score threshold (default: 0.15)
    --post-deploy-cycles    polling cycles for post-deploy monitor (default: 24)
    --rollback-precision    precision floor before rollback (default: 0.65)
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import mlflow
import mlflow.sklearn
import pandas as pd
import requests
from mlflow import MlflowClient
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score

from drift_detector import detect_drift, DEFAULT_THRESHOLD, DEFAULT_PERF_THRESHOLD

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_NAME = "anomaly-detector"
FEATURES = ["latency_p99", "error_rate", "rps"]
CONTAMINATION = 0.05
N_ESTIMATORS = 100
RANDOM_STATE = 42
AUDIT_LOG = Path("outputs/audit_log.jsonl")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_audit(event: dict):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")


def _push_metric(name: str, value: float, job: str = "retrain"):
    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        reg = CollectorRegistry()
        g = Gauge(name, name, registry=reg)
        g.set(value)
        push_to_gateway("localhost:9091", job=job, registry=reg)
    except Exception as exc:
        print(f"[retrain] Pushgateway warning ({name}): {exc}")


def _reload_serve(serve_url: str):
    try:
        r = requests.post(f"{serve_url}/reload", timeout=10)
        print(f"[retrain] /reload → {r.json()}")
    except Exception as exc:
        print(f"[retrain] Warning: could not reload serve.py: {exc}")


def _get_active_production_version(client: MlflowClient) -> str:
    mv = client.get_model_version_by_alias(MODEL_NAME, "production")
    return mv.version


def _get_active_staging_version(client: MlflowClient) -> str:
    mv = client.get_model_version_by_alias(MODEL_NAME, "staging")
    return mv.version


# ---------------------------------------------------------------------------
# Train v2 on sliding window (baseline + drifted)
# ---------------------------------------------------------------------------
def train_v2(reference_df: pd.DataFrame, current_df: pd.DataFrame) -> str:
    """Train IsolationForest v2 on combined sliding-window data; register as @staging."""
    combined_df = pd.concat([reference_df, current_df], ignore_index=True)
    X = combined_df[FEATURES].values

    mlflow.set_experiment("anomaly-detector-retrain")
    with mlflow.start_run() as run:
        mlflow.log_param("contamination", CONTAMINATION)
        mlflow.log_param("n_estimators", N_ESTIMATORS)
        mlflow.log_param("random_state", RANDOM_STATE)
        mlflow.log_param("training_strategy", "sliding_window_baseline_plus_drift")
        mlflow.log_param("baseline_rows", len(reference_df))
        mlflow.log_param("current_rows", len(current_df))
        mlflow.log_param("combined_rows", len(combined_df))

        model = IsolationForest(
            contamination=CONTAMINATION,
            n_estimators=N_ESTIMATORS,
            random_state=RANDOM_STATE,
        )
        model.fit(X)

        predictions = model.predict(X)
        anomaly_rate = float((predictions == -1).sum() / len(predictions))
        mlflow.log_metric("train_anomaly_rate", anomaly_rate)
        mlflow.log_metric("feature_count", len(FEATURES))

        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )
        run_id = run.info.run_id

    client = MlflowClient()
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    latest = max(versions, key=lambda v: int(v.version))
    client.set_registered_model_alias(MODEL_NAME, "staging", latest.version)
    print(f"[retrain] v2 registered as '{MODEL_NAME}' v{latest.version} with alias @staging")
    return str(latest.version)


# ---------------------------------------------------------------------------
# Holdout validation
# ---------------------------------------------------------------------------
def evaluate_on_holdout(
    model_uri: str,
    holdout_df: pd.DataFrame,
) -> tuple[float, float]:
    """Evaluate model on holdout data; return (precision, recall)."""
    import mlflow.pyfunc

    model = mlflow.pyfunc.load_model(model_uri)
    X = holdout_df[FEATURES]
    y_true = holdout_df["anomaly_label"].values

    raw_pred = model.predict(X)
    y_pred = pd.Series(raw_pred).apply(lambda p: 1 if int(p) == -1 else 0).values

    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall = float(recall_score(y_true, y_pred, zero_division=0))
    return precision, recall


# ---------------------------------------------------------------------------
# Post-deploy monitoring + auto-rollback
# ---------------------------------------------------------------------------
def post_deploy_monitor(
    eval_df: pd.DataFrame,
    v1_version: str,
    v2_version: str,
    client: MlflowClient,
    serve_url: str,
    cycles: int = 24,
    rollback_precision: float = 0.65,
):
    """
    Monitor v2 on eval_df for 'cycles' polling rounds.
    If precision < rollback_precision in any cycle, rollback v1 → @production and v2 → @archived.
    """
    import mlflow.pyfunc

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    model_uri = f"models:/{MODEL_NAME}@production"

    rollback_occurred = False
    trigger_precision = None
    trigger_cycle = None

    for cycle in range(1, cycles + 1):
        print(f"post_deploy_monitor Cycle {cycle:02d}/{cycles}")

        # reload model in case alias changed
        try:
            model = mlflow.pyfunc.load_model(model_uri)
        except Exception as exc:
            print(f"[retrain] Warning: could not load model for monitoring: {exc}")
            continue

        X = eval_df[FEATURES]
        y_true = eval_df["anomaly_label"].values

        raw_pred = model.predict(X)
        y_pred = pd.Series(raw_pred).apply(lambda p: 1 if int(p) == -1 else 0).values

        precision = float(precision_score(y_true, y_pred, zero_division=0))
        recall = float(recall_score(y_true, y_pred, zero_division=0))

        print(f"  precision={precision:.4f}  recall={recall:.4f}  threshold={rollback_precision}")
        _push_metric("post_deploy_precision", precision, job="post_deploy_monitor")

        if precision < rollback_precision:
            print(f"[retrain] Precision {precision:.4f} < {rollback_precision} — triggering rollback.")
            rollback_occurred = True
            trigger_precision = precision
            trigger_cycle = cycle
            break

        # Simulate wait between cycles (minimal for lab — skip actual sleep)
        # time.sleep(1)

    if rollback_occurred:
        # Demote v2 → @archived, restore v1 → @production
        client.set_registered_model_alias(MODEL_NAME, "archived", v2_version)
        client.delete_registered_model_alias(MODEL_NAME, "production")
        client.set_registered_model_alias(MODEL_NAME, "production", v1_version)
        _reload_serve(serve_url)

        audit_event = {
            "event": "auto_rollback_v2_to_v1",
            "demoted_version": v2_version,
            "restored_version": v1_version,
            "trigger_precision": trigger_precision,
            "cycle": trigger_cycle,
            "timestamp": datetime.utcnow().isoformat(),
        }
        _write_audit(audit_event)
        _push_metric("auto_rollback_count", 1, job="retrain")

        try:
            mlflow.log_metric("auto_rollback", 1)
            mlflow.log_metric("rollback_cycle", trigger_cycle)
            mlflow.log_metric("rollback_trigger_precision", trigger_precision)
        except Exception:
            pass

        print(f"Rollback complete. v1 restored to @production. v2 → @archived")
    else:
        print(f"[retrain] Post-deploy monitoring complete. v2 passed all {cycles} cycles.")
        audit_event = {
            "event": "post_deploy_passed",
            "v2_version": v2_version,
            "cycles": cycles,
            "timestamp": datetime.utcnow().isoformat(),
        }
        _write_audit(audit_event)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MLOps retrain orchestrator")
    parser.add_argument("--reference", default="data/baseline.csv")
    parser.add_argument("--current", default="data/drifted.csv")
    parser.add_argument("--holdout", default=None)
    parser.add_argument("--post-deploy-eval", default=None)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--perf-threshold", type=float, default=DEFAULT_PERF_THRESHOLD)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--serve-url", default="http://localhost:8000")
    parser.add_argument("--post-deploy-cycles", type=int, default=24)
    parser.add_argument("--rollback-precision", type=float, default=0.65)
    parser.add_argument(
        "--check-mode",
        choices=["data", "performance", "combined"],
        default="combined",
    )
    args = parser.parse_args()

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()

    reference_df = pd.read_csv(args.reference)
    current_df = pd.read_csv(args.current)

    # Rolling window: most recent 7 days (1008 rows at 10-min interval)
    current_df["timestamp"] = pd.to_datetime(current_df["timestamp"])
    current_df = current_df.sort_values("timestamp")
    window_end = current_df["timestamp"].max()
    window_start = window_end - pd.Timedelta(days=7)
    current_window = current_df[current_df["timestamp"] >= window_start].copy()
    print(f"[retrain] Rolling window: {window_start} → {window_end} ({len(current_window)} rows)")

    # --- Step 1: Detect drift ---
    print("[retrain] Running drift detection...")
    labeled_df = current_window if "anomaly_label" in current_window.columns else None
    model_uri_for_perf = f"models:/{MODEL_NAME}@production" if labeled_df is not None else None

    result = detect_drift(
        reference_df=reference_df,
        current_df=current_window,
        threshold=args.threshold,
        check_mode=args.check_mode,
        labeled_current=labeled_df,
        model_uri=model_uri_for_perf,
        perf_threshold=args.perf_threshold,
    )

    print(f"[retrain] Drift score: {result.score:.4f}  is_drift: {result.is_drift}")
    if result.precision is not None:
        print(f"[retrain] Perf precision: {result.precision:.4f}  perf_degraded: {result.perf_degraded}")

    _push_metric("drift_score", result.score)
    _push_metric("drift_flag", int(result.is_drift))
    _push_metric("retrain_triggered", 0)

    if not result.is_drift:
        print("[retrain] No drift detected. Pipeline complete — no retraining needed.")
        _write_audit({
            "event": "no_drift",
            "drift_score": result.score,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return

    # --- Step 2: Get current production version for potential rollback ---
    try:
        v1_version = _get_active_production_version(client)
        print(f"[retrain] Current production version: v{v1_version}")
    except Exception as exc:
        print(f"[retrain] Warning: could not get production version: {exc}")
        v1_version = "1"

    # --- Step 3: Train v2 on sliding window ---
    print("[retrain] Training v2 on sliding window (baseline + drifted)...")
    v2_version = train_v2(reference_df, current_window)
    _push_metric("retrain_triggered", 1)

    # --- Step 4: Holdout validation (Stress 2) ---
    if args.holdout:
        holdout_df = pd.read_csv(args.holdout)
        if "anomaly_label" not in holdout_df.columns:
            print("[retrain] Warning: holdout CSV has no 'anomaly_label' column — skipping holdout validation")
        else:
            v1_prec, v1_rec = evaluate_on_holdout(
                f"models:/{MODEL_NAME}@production", holdout_df
            )
            v2_prec, v2_rec = evaluate_on_holdout(
                f"models:/{MODEL_NAME}@staging", holdout_df
            )
            print(f"Holdout validation — v2 precision: {v2_prec:.4f}  recall: {v2_rec:.4f}")
            print(f"[retrain] v1 holdout precision: {v1_prec:.4f}  recall: {v1_rec:.4f}")
            if v2_prec < v1_prec:
                print(f"[retrain] WARNING: v2 precision ({v2_prec:.4f}) < v1 ({v1_prec:.4f}) on holdout")
            try:
                mlflow.log_metric("holdout_v2_precision", v2_prec)
                mlflow.log_metric("holdout_v2_recall", v2_rec)
                mlflow.log_metric("holdout_v1_precision", v1_prec)
            except Exception:
                pass

    # --- Step 5: Approval gate ---
    print(f"\nDrift detected. Model v2 registered as staging. Promote to production? [y/N]", end=" ", flush=True)

    if args.auto_approve:
        answer = "y"
        print("y (auto-approved)")
    else:
        answer = input().strip().lower()

    if answer != "y":
        print("[retrain] Promotion declined. v2 remains at @staging. Pipeline complete.")
        _write_audit({
            "event": "promotion_declined",
            "v2_version": v2_version,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return

    # --- Step 6: Promote staging → production ---
    client.delete_registered_model_alias(MODEL_NAME, "production")
    client.set_registered_model_alias(MODEL_NAME, "production", v2_version)
    print(f"[retrain] Promoted v{v2_version} to @production.")

    _write_audit({
        "event": "promoted_to_production",
        "v1_version": v1_version,
        "v2_version": v2_version,
        "drift_score": result.score,
        "timestamp": datetime.utcnow().isoformat(),
    })

    # --- Step 7: Reload serve.py ---
    _reload_serve(args.serve_url)

    # --- Step 8: Post-deploy monitoring + auto-rollback (Stress 3) ---
    if args.post_deploy_eval:
        eval_df = pd.read_csv(args.post_deploy_eval)
        if "anomaly_label" not in eval_df.columns:
            print("[retrain] Warning: post_deploy_eval has no 'anomaly_label' — skipping monitor")
        else:
            print(f"[retrain] Starting post-deploy monitoring ({args.post_deploy_cycles} cycles)...")
            post_deploy_monitor(
                eval_df=eval_df,
                v1_version=v1_version,
                v2_version=v2_version,
                client=client,
                serve_url=args.serve_url,
                cycles=args.post_deploy_cycles,
                rollback_precision=args.rollback_precision,
            )
    else:
        print("[retrain] No --post-deploy-eval provided. Skipping post-deploy monitoring.")

    print("[retrain] Orchestration complete.")


if __name__ == "__main__":
    main()
