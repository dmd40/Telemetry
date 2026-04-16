# Back End

Backend service is implemented by `app.py` (FastAPI).

## Responsibilities
- Telemetry ingest endpoint(s)
- WebSocket live stream (`/ws`)
- Simulation start/stop/clear APIs
- SQLite persistence and log file writes
- Query APIs for logs, laps, and GPS data
- Vehicle-Pi serial collection mode (`TELEM_SERIAL_MODE=vehicle`) that reads CA + GPS from USB serial and feeds the same ingest pipeline

## Local Run
```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

## Vehicle Pi Run

Use this when the Pi is collecting data locally from USB serial devices:

```bash
export ENABLE_SERIAL_READER=1
export TELEM_SERIAL_MODE=vehicle
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Optional overrides:
- `TELEM_CA_PORT`
- `TELEM_GPS_PORT`
- `TELEM_CA_BAUD`
- `TELEM_GPS_BAUD`
