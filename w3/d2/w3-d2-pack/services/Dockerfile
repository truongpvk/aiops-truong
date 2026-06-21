FROM python:3.11-slim

# Install network tools for chaos injection (tc, iptables, stress-ng)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iptables stress-ng curl procps coreutils && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir prometheus_client

WORKDIR /app
COPY mock_service.py .

CMD ["python", "mock_service.py"]
