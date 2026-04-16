#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT/.venv312"
PYTHON_BIN="$VENV_DIR/bin/python"
RUNTIME_DIR="$ROOT/.runtime"
WIFI_PID_FILE="$RUNTIME_DIR/vehicle_wifi_failover.pid"

mkdir -p "$RUNTIME_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -m pip install -r "$ROOT/requirements.txt"

if command -v nmcli >/dev/null 2>&1; then
  nohup "$ROOT/tools/vehicle_wifi_failover.sh" >> "$RUNTIME_DIR/vehicle_wifi_failover.log" 2>&1 &
  echo $! > "$WIFI_PID_FILE"
fi

export ENABLE_SERIAL_READER=1
export TELEM_SERIAL_MODE=vehicle
export TELEM_CA_BAUD="${TELEM_CA_BAUD:-9600}"
export TELEM_GPS_BAUD="${TELEM_GPS_BAUD:-9600}"
export TELEM_CA_PORT="${TELEM_CA_PORT:-}"
export TELEM_GPS_PORT="${TELEM_GPS_PORT:-}"

cd "$ROOT"
exec "$PYTHON_BIN" -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
