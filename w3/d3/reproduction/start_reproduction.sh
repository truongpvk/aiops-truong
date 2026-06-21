#!/usr/bin/env bash
# start_reproduction.sh — Spin up the Cloudflare regex reproduction stack
# Usage: bash start_reproduction.sh

set -euo pipefail

echo "[+] Starting Cloudflare WAF regex reproduction stack..."
docker compose -f "$(dirname "$0")/../reproduction/docker-compose.yml" up -d --build

echo "[+] Waiting for edge-server healthcheck..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo "[✓] Edge server healthy after ${i}s"
        break
    fi
    sleep 1
done

echo "[+] Waiting for Prometheus..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:9090/-/ready > /dev/null 2>&1; then
        echo "[✓] Prometheus ready after ${i}s"
        break
    fi
    sleep 1
done

echo "[✓] Reproduction stack is running."
echo "    Edge server: http://localhost:8080"
echo "    Prometheus:   http://localhost:9090"
echo ""
echo "[→] Next: run inject.sh to trigger the failure mode."
