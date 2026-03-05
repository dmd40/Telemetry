# Telemetry System

Early development project for vehicle telemetry collection and live viewing.

## Project Sections

### Front End
- Path: `static/`
- Live dashboard, lap plot, GPS map, and logs pages
- See: `static/README.md`

### Back End
- Main API/app: `app.py` (FastAPI + WebSocket)
- Handles ingest, simulation, persistence, and query endpoints
- See: `backend/README.md`

### ESP Script
- Path: `ArduinoIDE/`
- ESP8266 telemetry sender sketches and variants
- See: `ArduinoIDE/README.md`

## Quick Start (one-click)

Use:
- `Start Telemetry.cmd`
- `Stop Telemetry.cmd`

`Start Telemetry.cmd`:
- starts API on `http://127.0.0.1:8000`
- can enable Tailscale Funnel (if configured)
- prints local/public URLs

`Stop Telemetry.cmd`:
- stops API process
- disables Funnel on port 443

## Manual Start

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

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
