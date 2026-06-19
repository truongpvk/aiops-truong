#!/usr/bin/env bash
# inject_fault.sh — inject chaos into a running container (Docker Exec Version)
#
# Usage:
#   bash inject_fault.sh <fault_type> <container_name> [param]
#
# Fault types:
#   latency  <container> <delay>   e.g. latency payment-svc 500ms
#   kill     <container>           stop the container (simulates crash)
#   pause    <container>           pause container (simulates freeze)
#   resume   <container>           resume a paused container
#   recover  <container>           restart a stopped/killed container
#   clear-latency <container>      remove all tc network latency rules
#
# Examples:
#   bash inject_fault.sh latency ronki-payment-svc 500ms
#   bash inject_fault.sh clear-latency ronki-payment-svc

set -euo pipefail

FAULT="${1:-}"
CONTAINER="${2:-}"
PARAM="${3:-}"

if [[ -z "$FAULT" || -z "$CONTAINER" ]]; then
  echo "Usage: $0 <fault_type> <container_name> [param]"
  echo "       fault_type: latency | kill | pause | resume | recover | clear-latency | --concurrent"
  exit 1
fi

# Tự động thêm prefix 'ronki-' nếu người dùng chỉ nhập tên ngắn
if ! docker inspect "$CONTAINER" > /dev/null 2>&1; then
  CONTAINER="ronki-${CONTAINER}"
fi

# Kiểm tra container có tồn tại trong Docker hay không
if ! docker inspect "$CONTAINER" > /dev/null 2>&1; then
  echo "[inject_fault] ERROR: container '$CONTAINER' not found."
  echo "               Running containers:"
  docker ps --format '  {{.Names}}'
  exit 1
fi

# Hàm tiện ích kiểm tra container có đang ở trạng thái "running" hay không
# Vì 'docker exec' chỉ chạy được khi container đang hoạt động.
check_container_running() {
  local status
  status=$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo "false")
  if [[ "$status" != "true" ]]; then
    echo "[inject_fault] ERROR: Container '$CONTAINER' hiện không chạy hoặc đang bị paused/stopped."
    echo "               'docker exec' yêu cầu container phải ở trạng thái Running."
    exit 1
  fi
}

case "$FAULT" in
  latency)
    DELAY="${PARAM:-200ms}"
    check_container_running

    # Chuẩn hóa định dạng chuỗi: Đảm bảo chuyển e.g. "500ms" hoặc "500" thành "500ms"
    DELAY_MS="${DELAY//ms/}"
    echo "[inject_fault] Adding ${DELAY_MS}ms network latency to $CONTAINER via docker exec tc..."

    # Sử dụng docker exec --privileged với quyền root để cấu hình tc ngay bên trong container
    # Thử add rule mới, nếu trùng thì thực hiện lệnh change.
    docker exec --privileged -u root "$CONTAINER" tc qdisc add dev eth0 root netem delay "${DELAY_MS}ms" 2>/dev/null \
      || docker exec --privileged -u root "$CONTAINER" tc qdisc change dev eth0 root netem delay "${DELAY_MS}ms"

    echo "[inject_fault] Latency ${DELAY_MS}ms applied to $CONTAINER."
    echo "               To remove: bash $0 clear-latency $CONTAINER"
    ;;

  clear-latency)
    check_container_running
    echo "[inject_fault] Removing tc netem rules from $CONTAINER..."
    
    # Xóa cấu hình tc traffic control trong container
    docker exec --privileged -u root "$CONTAINER" tc qdisc del dev eth0 root 2>/dev/null || true
    echo "[inject_fault] Latency rules cleared for $CONTAINER."
    ;;

  kill)
    echo "[inject_fault] Stopping container $CONTAINER (simulate crash)..."
    docker stop "$CONTAINER"
    echo "[inject_fault] $CONTAINER stopped."
    ;;

  pause)
    echo "[inject_fault] Pausing container $CONTAINER..."
    docker pause "$CONTAINER"
    echo "[inject_fault] $CONTAINER paused. Resume with: $0 resume $CONTAINER"
    ;;

  resume)
    echo "[inject_fault] Resuming container $CONTAINER..."
    docker unpause "$CONTAINER"
    echo "[inject_fault] $CONTAINER resumed."
    ;;

  recover)
    echo "[inject_fault] Starting container $CONTAINER..."
    docker start "$CONTAINER"
    echo "[inject_fault] $CONTAINER started."
    ;;

  --concurrent)
    SVC1="${CONTAINER}"
    SVC2="${PARAM}"
    if [[ -z "$SVC1" || -z "$SVC2" ]]; then
      echo "[inject_fault] --concurrent requires exactly 2 container names"
      echo "               Usage: $0 --concurrent <container1> <container2>"
      exit 1
    fi
    echo "[inject_fault] Injecting latency fault concurrently on $SVC1 and $SVC2..."
    (bash "$0" latency "$SVC1" 500ms) &
    PID1=$!
    (bash "$0" latency "$SVC2" 500ms) &
    PID2=$!
    wait "$PID1"
    wait "$PID2"
    echo "[inject_fault] Concurrent fault injection complete on $SVC1 and $SVC2."
    ;;

  *)
    echo "[inject_fault] Unknown fault type: $FAULT"
    echo "               Supported: latency | kill | pause | resume | recover | clear-latency | --concurrent"
    exit 1
    ;;
esac