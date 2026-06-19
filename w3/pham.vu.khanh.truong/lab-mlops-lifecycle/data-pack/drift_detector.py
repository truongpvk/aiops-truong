"""
drift_detector.py — Evidently-based drift detection with combined data + performance check.

Modes:
    --check-mode data         DataDriftPreset only (feature distribution)
    --check-mode performance  Precision/recall via anomaly_label column
    --check-mode combined     data OR performance (triggers on either)

Usage:
    uv run python drift_detector.py \
        --reference data/baseline.csv \
        --current   data/drifted.csv \
        --threshold 0.15 \
        --check-mode combined \
        --labeled-current data/drifted.csv \
        --model-uri models:/anomaly-detector@production
"""

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mlflow
import pandas as pd
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FEATURES = ["latency_p99", "error_rate", "rps"]
DEFAULT_THRESHOLD = 0.15
DEFAULT_PERF_THRESHOLD = 0.70
REPORT_DIR = Path("outputs/drift_reports")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class DriftResult:
    score: float
    is_drift: bool
    report_path: Optional[str]
    precision: Optional[float] = None
    recall: Optional[float] = None
    perf_degraded: Optional[bool] = None


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def detect_data_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    report_path: Optional[Path] = None,
) -> tuple[float, bool, str]:
    """Run Evidently DataDriftPreset; return (score, is_drift, html_path)."""
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference_df[FEATURES], current_data=current_df[FEATURES])

    result_dict = report.as_dict()
    # Extract share_of_drifted_columns
    drift_score = 0.0
    try:
        for metric in result_dict["metrics"]:
            if metric.get("metric") == "DatasetDriftMetric":
                drift_score = float(
                    metric["result"].get("drift_share", 0.0)
                )
                break
    except Exception:
        pass

    # Save HTML report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if report_path is None:
        import time
        ts = int(time.time())
        report_path = REPORT_DIR / f"drift_report_{ts}.html"
    report.save_html(str(report_path))

    is_drift = drift_score > threshold
    return drift_score, is_drift, str(report_path)


def detect_performance_drift(
    model_uri: str,
    labeled_df: pd.DataFrame,
    perf_threshold: float = DEFAULT_PERF_THRESHOLD,
) -> tuple[float, float, bool]:
    """Run model on labeled_df, compute precision/recall; return (precision, recall, degraded)."""
    import mlflow.pyfunc
    from sklearn.metrics import precision_score, recall_score

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    model = mlflow.pyfunc.load_model(model_uri)

    X = labeled_df[FEATURES]
    y_true_raw = labeled_df["anomaly_label"].values  # 0/1 where 1 = anomaly

    raw_pred = model.predict(X)
    # IsolationForest: 1=normal, -1=anomaly → convert to 0/1 labels
    y_pred = (pd.Series(raw_pred).apply(lambda p: 1 if int(p) == -1 else 0)).values

    precision = float(precision_score(y_true_raw, y_pred, zero_division=0))
    recall = float(recall_score(y_true_raw, y_pred, zero_division=0))
    degraded = precision < perf_threshold
    return precision, recall, degraded


def detect_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    check_mode: str = "data",
    labeled_current: Optional[pd.DataFrame] = None,
    model_uri: Optional[str] = None,
    perf_threshold: float = DEFAULT_PERF_THRESHOLD,
) -> DriftResult:
    """
    Unified drift detection function.

    Returns DriftResult with score, is_drift flag, report_path,
    and optionally precision/recall/perf_degraded.
    """
    drift_score, data_is_drift, report_path = detect_data_drift(
        reference_df, current_df, threshold
    )

    precision = None
    recall = None
    perf_degraded = None

    if check_mode in ("performance", "combined"):
        if labeled_current is None or model_uri is None:
            print("[drift_detector] WARNING: performance check requested but missing labeled_current or model_uri — skipping perf check")
        else:
            precision, recall, perf_degraded = detect_performance_drift(
                model_uri, labeled_current, perf_threshold
            )

    # Determine final is_drift flag
    if check_mode == "data":
        is_drift = data_is_drift
    elif check_mode == "performance":
        is_drift = bool(perf_degraded)
    else:  # combined
        is_drift = data_is_drift or bool(perf_degraded)

    return DriftResult(
        score=drift_score,
        is_drift=is_drift,
        report_path=report_path,
        precision=precision,
        recall=recall,
        perf_degraded=perf_degraded,
    )


# ---------------------------------------------------------------------------
# MLflow logging helper
# ---------------------------------------------------------------------------
def log_drift_to_mlflow(result: DriftResult, step: Optional[int] = None):
    """Log drift metrics to active MLflow run (if any)."""
    try:
        mlflow.log_metric("drift_score", result.score, step=step)
        mlflow.log_metric("drift_flag", int(result.is_drift), step=step)
        if result.precision is not None:
            mlflow.log_metric("perf_precision", result.precision, step=step)
        if result.recall is not None:
            mlflow.log_metric("perf_recall", result.recall, step=step)
    except Exception as exc:
        print(f"[drift_detector] MLflow log warning: {exc}")


# ---------------------------------------------------------------------------
# Pushgateway helper (optional, won't crash if unavailable)
# ---------------------------------------------------------------------------
def push_drift_metrics(result: DriftResult):
    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        reg = CollectorRegistry()
        g_score = Gauge("drift_score", "Drift score", registry=reg)
        g_flag = Gauge("drift_flag", "Drift detected flag", registry=reg)
        g_score.set(result.score)
        g_flag.set(int(result.is_drift))
        if result.precision is not None:
            g_prec = Gauge("perf_precision", "Model precision on labeled current", registry=reg)
            g_prec.set(result.precision)
        push_to_gateway("localhost:9091", job="drift_detector", registry=reg)
    except Exception as exc:
        print(f"[drift_detector] Pushgateway warning (skipped): {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Drift detector")
    parser.add_argument("--reference", default="data/baseline.csv")
    parser.add_argument("--current", default="data/drifted.csv")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--check-mode",
        choices=["data", "performance", "combined"],
        default="data",
    )
    parser.add_argument("--labeled-current", default=None,
                        help="CSV with anomaly_label column for performance check")
    parser.add_argument("--model-uri", default=None,
                        help="MLflow model URI for performance check, e.g. models:/anomaly-detector@production")
    parser.add_argument("--perf-threshold", type=float, default=DEFAULT_PERF_THRESHOLD)
    args = parser.parse_args()

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    reference_df = pd.read_csv(args.reference)
    current_df = pd.read_csv(args.current)
    labeled_df = pd.read_csv(args.labeled_current) if args.labeled_current else None

    mlflow.set_experiment("drift-detection")
    with mlflow.start_run():
        mlflow.log_param("check_mode", args.check_mode)
        mlflow.log_param("threshold", args.threshold)
        mlflow.log_param("reference", args.reference)
        mlflow.log_param("current", args.current)

        result = detect_drift(
            reference_df=reference_df,
            current_df=current_df,
            threshold=args.threshold,
            check_mode=args.check_mode,
            labeled_current=labeled_df,
            model_uri=args.model_uri,
            perf_threshold=args.perf_threshold,
        )

        log_drift_to_mlflow(result)
        push_drift_metrics(result)

        mlflow.log_artifact(result.report_path, artifact_path="drift_reports")

    print(f"Drift score: {result.score:.4f}  (threshold={args.threshold})")
    print(f"Drift detected: {result.is_drift}")
    if result.precision is not None:
        print(f"Perf precision: {result.precision:.4f}  recall: {result.recall:.4f}")
        print(f"Perf degraded: {result.perf_degraded}")
    print(f"Report saved: {result.report_path}")


if __name__ == "__main__":
    main()
