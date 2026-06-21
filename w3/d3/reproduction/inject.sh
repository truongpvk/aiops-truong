#!/usr/bin/env bash
# inject.sh — Trigger catastrophic backtracking on the edge server
# Reproduces the Cloudflare 2019-07-02 WAF regex outage
# Usage: bash inject.sh

set -euo pipefail

EDGE_URL="http://localhost:8080"

echo "[+] Phase 1: Baseline — sending normal requests..."
for i in $(seq 1 5); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${EDGE_URL}/page")
    echo "    Request $i: HTTP $STATUS"
    sleep 1
done

echo ""
echo "[+] Phase 2: Deploying evil regex (simulating global WAF rule push)..."
# Set the flag file inside the container to enable evil regex
docker compose -f "$(dirname "$0")/../reproduction/docker-compose.yml" \
    exec -T edge-server touch /tmp/evil_regex_flag
echo "[!] Evil regex deployed — catastrophic backtracking ACTIVE"

sleep 2

echo ""
echo "[+] Phase 3: Sending adversarial payloads..."
echo "    These will trigger exponential regex matching → CPU 100%"

# Adversarial input that causes catastrophic backtracking
EVIL_PAYLOAD='x=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'

for i in $(seq 1 10); do
    echo -n "    Payload $i: "
    TIMEOUT_RESULT=$(timeout 10 curl -s -o /dev/null -w "%{http_code} %{time_total}s" \
        -X POST "${EDGE_URL}/waf-check" \
        -d "${EVIL_PAYLOAD}" 2>/dev/null || echo "TIMEOUT")
    echo "$TIMEOUT_RESULT"
    sleep 0.5
done

echo ""
echo "[+] Phase 4: Rollback — removing evil regex..."
docker compose -f "$(dirname "$0")/../reproduction/docker-compose.yml" \
    exec -T edge-server rm -f /tmp/evil_regex_flag
echo "[✓] Evil regex removed — recovery should begin"

sleep 5

echo ""
echo "[+] Phase 5: Post-rollback — verifying recovery..."
for i in $(seq 1 5); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${EDGE_URL}/page")
    echo "    Request $i: HTTP $STATUS"
    sleep 1
done

echo ""
echo "[✓] Injection complete. Run capture_timeline.py to record events."
