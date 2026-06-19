# How to Run — MLOps Lifecycle Pipeline

## Prerequisites

```bash
# Start the Docker stack (MLflow + PostgreSQL + Prometheus + Pushgateway + Grafana)
bash scripts/start_stack.sh

# Install Python dependencies
uv pip install 'mlflow==2.13.2' 'evidently==0.4.40' scikit-learn pandas numpy fastapi uvicorn prometheus_client requests
```

## Step-by-step run

```bash
export MLFLOW_TRACKING_URI=http://localhost:5000

# 1. Train v1 and register with alias @production
uv run python pipeline.py --data data/baseline.csv

# 2. Start the model server (in a separate terminal)
uv run python serve.py

# 3. Verify serve is up
curl -s http://localhost:8000/health/active-version

# 4. Run drift detection (data only)
uv run python drift_detector.py \
  --reference data/baseline.csv \
  --current   data/drifted.csv \
  --check-mode data \
  --threshold 0.15

# 5. Run drift detection (combined — required for Stress 1 acceptance)
uv run python drift_detector.py \
  --reference data/baseline.csv \
  --current   data/drifted.csv \
  --check-mode combined \
  --labeled-current data/drifted.csv \
  --model-uri models:/anomaly-detector@production \
  --threshold 0.15

# 6. Run full retrain orchestration (includes holdout + post-deploy monitoring + auto-rollback)
uv run python retrain.py \
  --reference        data/baseline.csv \
  --current          data/drifted.csv \
  --holdout          data/holdout.csv \
  --post-deploy-eval data/post_deploy_eval.csv \
  --threshold 0.15

# 7. View audit log
cat outputs/audit_log.jsonl

# 8. Open Grafana dashboard
# http://localhost:3000 → "AIOps MLOps Lifecycle"
```

## Teardown

```bash
bash scripts/stop_stack.sh
```
