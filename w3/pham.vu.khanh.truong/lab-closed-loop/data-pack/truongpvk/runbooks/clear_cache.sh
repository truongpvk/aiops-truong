#!/usr/bin/env bash
set -e

DRY_RUN=false
SERVICE=""

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
  echo "[DRY-RUN] would execute: clear cache for $SERVICE"
  exit 0
fi

echo "Clearing cache for service $SERVICE..."
# Simulated clear cache action
echo "Cache cleared for $SERVICE."
exit 0
