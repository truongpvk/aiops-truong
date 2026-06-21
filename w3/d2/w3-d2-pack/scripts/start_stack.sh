#!/usr/bin/env bash
# start_stack.sh — Start the 10-service chaos engineering stack
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Starting Chaos Engineering Stack ==="
echo "Project dir: $PROJECT_DIR"

# 1. Build and start all services
cd "$PROJECT_DIR"
docker compose up -d --build

echo ""
echo "Waiting for services to be healthy..."

# 2. Wait for all service healthchecks
MAX_WAIT=180
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    HEALTHY=$(docker compose ps --format json 2>/dev/null | python3 -c "
import sys, json
healthy = 0
total = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        svc = json.loads(line)
        total += 1
        state = svc.get('Health', svc.get('State', ''))
        if 'healthy' in state.lower() or 'running' in state.lower():
            healthy += 1
    except:
        pass
print(f'{healthy}/{total}')
" 2>/dev/null || echo "0/0")

    echo "  Health: $HEALTHY (${ELAPSED}s elapsed)"

    if echo "$HEALTHY" | grep -qE "^1[0-9]/1[0-9]$|^[0-9]/[0-9]$"; then
        # All healthy
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

# 3. Wait for AIOps pipeline
echo ""
echo "Waiting for AIOps pipeline..."
ELAPSED=0
while [ $ELAPSED -lt 60 ]; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "  ✓ AIOps pipeline ready"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

# 4. Wait for Prometheus
echo "Waiting for Prometheus..."
ELAPSED=0
while [ $ELAPSED -lt 60 ]; do
    if curl -sf http://localhost:9090/-/ready > /dev/null 2>&1; then
        echo "  ✓ Prometheus ready"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo ""
echo "=== Stack ready ==="
echo "  Frontend:     http://localhost:8080"
echo "  AIOps:        http://localhost:8000"
echo "  Prometheus:   http://localhost:9090"
echo "  Grafana:      http://localhost:3000"
echo "  Alertmanager: http://localhost:9093"
