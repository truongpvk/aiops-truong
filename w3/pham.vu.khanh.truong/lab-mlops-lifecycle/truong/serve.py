"""
serve.py — FastAPI model server loading model from MLflow Registry alias @production.

Endpoints:
    POST /predict          — {features: [...]}  → {prediction, score, version}
    GET  /health/active-version — returns current model version being served
    POST /reload           — reloads model from registry (used after alias swap)

Usage:
    uv run python serve.py
"""

import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import mlflow.pyfunc
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_URI = "models:/anomaly-detector@production"
FEATURES = ["latency_p99", "error_rate", "rps"]

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "serve_request_total", "Total number of /predict requests"
)
REQUEST_LATENCY = Histogram(
    "serve_predict_latency_seconds",
    "Predict endpoint latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# ---------------------------------------------------------------------------
# Global model state
# ---------------------------------------------------------------------------
_model_state: Dict[str, Any] = {"model": None, "version": "unknown"}


def _load_model():
    """Load (or reload) model from registry alias @production."""
    import mlflow

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    model = mlflow.pyfunc.load_model(MODEL_URI)

    # Extract version from model metadata
    try:
        version = model.metadata.run_id[:8]  # fallback
        # Try to get registered model version
        from mlflow import MlflowClient
        client = MlflowClient()
        mv = client.get_model_version_by_alias("anomaly-detector", "production")
        version = mv.version
    except Exception:
        version = "unknown"

    _model_state["model"] = model
    _model_state["version"] = str(version)
    print(f"[serve] Loaded model version {version} from {MODEL_URI}")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_model()
    yield


app = FastAPI(title="Anomaly Detector Serve", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PredictRequest(BaseModel):
    features: List[float]  # [latency_p99, error_rate, rps]


class PredictResponse(BaseModel):
    prediction: int
    score: float
    version: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    import pandas as pd
    import time

    model = _model_state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    if len(req.features) != len(FEATURES):
        raise HTTPException(
            status_code=422,
            detail=f"Expected {len(FEATURES)} features: {FEATURES}",
        )

    REQUEST_COUNT.inc()
    start = time.time()

    df = pd.DataFrame([req.features], columns=FEATURES)
    raw_pred = model.predict(df)
    # IsolationForest returns 1 (normal) or -1 (anomaly) — convert to 0/1
    prediction = int(1 if int(raw_pred[0]) == -1 else 0)

    # decision_function via sklearn underlying model
    try:
        sklearn_model = model._model_impl  # unwrap pyfunc
        score = float(sklearn_model.decision_function(df)[0])
    except Exception:
        score = 0.0

    elapsed = time.time() - start
    REQUEST_LATENCY.observe(elapsed)

    return PredictResponse(
        prediction=prediction,
        score=score,
        version=_model_state["version"],
    )


@app.get("/health/active-version")
def health_active_version():
    return {"version": _model_state["version"], "model_uri": MODEL_URI}


@app.post("/reload")
def reload():
    try:
        _load_model()
        return {"status": "reloaded", "version": _model_state["version"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    return {"status": "ok", "version": _model_state["version"]}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("serve:app", host="0.0.0.0", port=8000, reload=False)
