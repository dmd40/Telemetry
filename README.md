# Telemetry System

Early development project for vehicle telemetry collection and live viewing.

## Project Sections

### Front End
- Path: `static/`
- Live dashboard, lap plot, GPS map, and logs pages
- See: `static/README.md`

### Back End
- Main API/app: `backend/app.py` (FastAPI + WebSocket)
- Handles ingest, simulation, persistence, and query endpoints
- Supports a vehicle-Pi mode that can read CA + GPS directly from USB serial and feed the same API/UI
- See: `backend/README.md`

### ESP Script
- Path: `ArduinoIDE/`
- ESP8266 telemetry sender sketches and variants
- See: `ArduinoIDE/README.md`

## Quick Start (one-click)

Use:
- `tools/Start Telemetry.cmd`
- `tools/Stop Telemetry.cmd`

`tools/Start Telemetry.cmd`:
- starts API on `http://127.0.0.1:8000`
- can enable Tailscale Funnel (if configured)
- prints local/public URLs

`tools/Stop Telemetry.cmd`:
- stops API process
- disables Funnel on port 443

## Manual Start

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

## Vehicle Pi Mode

Set these on the Pi when the CA and GPS are plugged into USB:

- `ENABLE_SERIAL_READER=1`
- `TELEM_SERIAL_MODE=vehicle`
- `vehicle_wifi_failover.sh` can be used to hop between the two driver hotspots automatically
- Optional overrides if auto-detect picks the wrong device:
  - `TELEM_CA_PORT`
  - `TELEM_GPS_PORT`
  - `TELEM_CA_BAUD` (default `9600`)
  - `TELEM_GPS_BAUD` (default `9600`)

## Manual Funnel

```powershell
tailscale funnel --bg --yes 8000
tailscale funnel status
```

## Data Logging + Retention

- Samples append to dated logs: `logs/telemetry_YYYY-MM-DD.tsv`
- DB/log entries older than 14 days are auto-pruned
- Optional env controls:
  - `RETENTION_DAYS` (default `14`)
  - `RETENTION_PRUNE_INTERVAL_SEC` (default `300`)
  - `TELEM_LOG_DIR` (default `logs`)
