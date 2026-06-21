FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn requests pyyaml prometheus_client

WORKDIR /app
COPY aiops_pipeline.py .

EXPOSE 8000
CMD ["uvicorn", "aiops_pipeline:app", "--host", "0.0.0.0", "--port", "8000"]
