#!/usr/bin/env bash
set -e

DRY_RUN=false
SERVICE=""
REPLICAS=2

while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --service)
      SERVICE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "Error: --service <name> is required"
  exit 1
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] would execute: docker compose up -d --scale $SERVICE=$REPLICAS"
  exit 0
fi

echo "Scaling service $SERVICE to $REPLICAS replicas..."
docker compose up -d --scale "$SERVICE=$REPLICAS"
echo "Service $SERVICE scaled successfully."
exit 0
