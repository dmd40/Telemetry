#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/.runtime"
WIFI_PID_FILE="$RUNTIME_DIR/vehicle_wifi_failover.pid"

pkill -f "uvicorn backend.app:app" || true
pkill -f "uvicorn .*backend.app:app" || true

if [[ -f "$WIFI_PID_FILE" ]]; then
  WIFI_PID="$(cat "$WIFI_PID_FILE" 2>/dev/null || true)"
  if [[ -n "${WIFI_PID:-}" ]]; then
    kill "$WIFI_PID" >/dev/null 2>&1 || true
  fi
  rm -f "$WIFI_PID_FILE"
fi

pkill -f "vehicle_wifi_failover.sh" || true

if command -v tailscale >/dev/null 2>&1; then
  tailscale funnel --https=443 off >/dev/null 2>&1 || true
fi

rm -f "$ROOT/.runtime/telemetry_state.json"
echo "Telemetry stopped."
