# Back End

Backend service is implemented by `../app.py` (FastAPI).

## Responsibilities
- Telemetry ingest endpoint(s)
- WebSocket live stream (`/ws`)
- Simulation start/stop/clear APIs
- SQLite persistence and log file writes
- Query APIs for logs, laps, and GPS data

## Local Run
```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```
