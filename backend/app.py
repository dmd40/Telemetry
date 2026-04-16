import asyncio
import csv
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

try:
    import serial
except ImportError:
    serial = None

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
STATIC_DIR = PROJECT_ROOT / "static"
DB_PATH = os.environ.get("TELEM_DB_PATH", str(PROJECT_ROOT / "telemetry.db"))

# -----------------------------
# Config - adjust via environment, change throughout different vehicles 
# We should keep each car we have, separate files/databases etc.
# i.e VOLT = car_1 , SPECTRE - car_2 etc. , and VOID = car_3
# -----------------------------
SERIAL_PORT = os.environ.get("TELEM_PORT", "COM5")   # ONLY OPERABLE IN WINDOWS , SET COM TO YOUR SERIAL PORT, OR USB !!!!
SERIAL_BAUD = int(os.environ.get("TELEM_BAUD", "115200"))
ENABLE_SERIAL_READER = os.environ.get("ENABLE_SERIAL_READER", "0").strip().lower() not in {"0", "false", "no"}
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
CORS_ALLOW_ORIGINS = os.environ.get("CORS_ALLOW_ORIGINS", "*")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "14"))
RETENTION_PRUNE_INTERVAL_SEC = int(os.environ.get("RETENTION_PRUNE_INTERVAL_SEC", "300"))
LOG_DIR = os.environ.get("TELEM_LOG_DIR", str(PROJECT_ROOT / "logs"))
LOG_FILE_PREFIX = "telemetry_"
LOG_FILE_SUFFIX = ".tsv"
ALLOWED_LOG_METRICS = {"V", "A", "Ah", "mph", "torque"}
ALLOWED_LOG_GROUPS = {"none", "day", "lap"}
# You must set k_t and gear_ratio for motor / drive train data to be accurate and meaningful.

K_T_NM_PER_AMP = float(os.environ.get("K_T", "0.06"))      # Example only
GEAR_RATIO = float(os.environ.get("GEAR_RATIO", "10.0"))   # Example only
DRIVETRAIN_EFF = float(os.environ.get("EFF", "0.9"))      # Example only

# Lap detection:
# Best practice is to do lap counting on-vehicle with a start/finish GPS gate and transmit lap id.
# If not available, you can implement server-side lap detection later.
# -----------------------------

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if CORS_ALLOW_ORIGINS.strip() == "*":
    cors_origins = ["*"]
else:
    cors_origins = [o.strip() for o in CORS_ALLOW_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

clients: set[WebSocket] = set()
latest_sample: Dict[str, Any] = {}
db_lock = threading.Lock()
log_lock = threading.Lock()
prune_lock = threading.Lock()
last_prune_ms = 0
last_prune_report: Dict[str, Any] = {
    "last_run_ms": 0,
    "deleted_db_rows": 0,
    "deleted_log_files": 0,
}

# Simulation mode
simulation_mode = False
simulation_task = None

# -----------------------------
# Database helpers
# -----------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            t_ms INTEGER NOT NULL,
            lap INTEGER NOT NULL,
            V REAL,
            A REAL,
            Ah REAL,
            mph REAL,
            torque REAL,
            lat REAL,
            lon REAL,
            source TEXT,
            received_at_ms INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_lap_t ON samples(lap, t_ms)")

    # Lightweight migration path for existing DBs.
    cur.execute("PRAGMA table_info(samples)")
    cols = {str(r["name"]) for r in cur.fetchall()}
    if "received_at_ms" not in cols:
        cur.execute("ALTER TABLE samples ADD COLUMN received_at_ms INTEGER")
    if "source" not in cols:
        cur.execute("ALTER TABLE samples ADD COLUMN source TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_received_at ON samples(received_at_ms)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_source_received ON samples(source, received_at_ms)")

    now_ms = int(time.time() * 1000)
    cur.execute("UPDATE samples SET received_at_ms = ? WHERE received_at_ms IS NULL", (now_ms,))
    cur.execute("UPDATE samples SET source = 'unknown' WHERE source IS NULL OR source = ''")
    conn.commit()
    conn.close()

init_db()

def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def log_file_path_for_ms(ms: int) -> Path:
    day = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    return Path(LOG_DIR) / f"{LOG_FILE_PREFIX}{day}{LOG_FILE_SUFFIX}"

def append_sample_log(s: Dict[str, Any], received_at_ms: int):
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    path = log_file_path_for_ms(received_at_ms)
    write_header = not path.exists()

    row = [
        utc_iso(received_at_ms),
        received_at_ms,
        s.get("t_ms"),
        s.get("lap"),
        s.get("V"),
        s.get("A"),
        s.get("Ah"),
        s.get("mph"),
        s.get("torque"),
        s.get("lat"),
        s.get("lon"),
    ]

    with log_lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            if write_header:
                writer.writerow([
                    "received_at_iso_utc",
                    "received_at_ms",
                    "t_ms",
                    "lap",
                    "V",
                    "A",
                    "Ah",
                    "mph",
                    "torque",
                    "lat",
                    "lon",
                ])
            writer.writerow(row)

def prune_db_before(cutoff_ms: int) -> int:
    with db_lock:
        conn = db()
        cur = conn.cursor()
        cur.execute("DELETE FROM samples WHERE received_at_ms < ?", (cutoff_ms,))
        deleted = int(cur.rowcount if cur.rowcount is not None else 0)
        conn.commit()
        conn.close()
    return max(0, deleted)

def prune_log_files_before(cutoff_ms: int) -> int:
    cutoff_date = datetime.fromtimestamp(cutoff_ms / 1000.0, tz=timezone.utc).date()
    deleted = 0
    base = Path(LOG_DIR)
    if not base.exists():
        return 0

    for p in base.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_SUFFIX}"):
        name = p.name
        # telemetry_YYYY-MM-DD.tsv
        if not (name.startswith(LOG_FILE_PREFIX) and name.endswith(LOG_FILE_SUFFIX)):
            continue
        date_str = name[len(LOG_FILE_PREFIX):-len(LOG_FILE_SUFFIX)]
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff_date:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted

def maybe_prune_retention(now_ms: Optional[int] = None):
    global last_prune_ms, last_prune_report
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    if (now_ms - last_prune_ms) < (RETENTION_PRUNE_INTERVAL_SEC * 1000):
        return

    if not prune_lock.acquire(blocking=False):
        return
    try:
        if (now_ms - last_prune_ms) < (RETENTION_PRUNE_INTERVAL_SEC * 1000):
            return
        last_prune_ms = now_ms

        cutoff_ms = now_ms - (RETENTION_DAYS * 24 * 60 * 60 * 1000)
        deleted_db_rows = prune_db_before(cutoff_ms)
        deleted_log_files = prune_log_files_before(cutoff_ms)
        last_prune_report = {
            "last_run_ms": now_ms,
            "deleted_db_rows": deleted_db_rows,
            "deleted_log_files": deleted_log_files,
        }
    finally:
        prune_lock.release()

def insert_sample(s: Dict[str, Any]):
    now_ms = int(time.time() * 1000)
    with db_lock:
        conn = db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO samples (t_ms, lap, V, A, Ah, mph, torque, lat, lon, source, received_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(s.get("t_ms", 0)),
            int(s.get("lap", 0)),
            s.get("V"),
            s.get("A"),
            s.get("Ah"),
            s.get("mph"),
            s.get("torque"),
            s.get("lat"),
            s.get("lon"),
            s.get("source", "unknown"),
            now_ms,
        ))
        conn.commit()
        conn.close()

    append_sample_log(s, now_ms)
    maybe_prune_retention(now_ms)

def delete_samples_by_source(source_value: str) -> int:
    with db_lock:
        conn = db()
        cur = conn.cursor()
        cur.execute("DELETE FROM samples WHERE source = ?", (source_value,))
        deleted = int(cur.rowcount if cur.rowcount is not None else 0)
        conn.commit()
        conn.close()
    return max(0, deleted)

def delete_samples_by_sources(source_values: List[str]) -> int:
    clean = [str(v).strip() for v in source_values if str(v).strip()]
    if not clean:
        return 0
    placeholders = ",".join(["?"] * len(clean))
    with db_lock:
        conn = db()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM samples WHERE source IN ({placeholders})", tuple(clean))
        deleted = int(cur.rowcount if cur.rowcount is not None else 0)
        conn.commit()
        conn.close()
    return max(0, deleted)

def get_latest_sample_from_db() -> Dict[str, Any]:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t_ms, lap, V, A, Ah, mph, torque, lat, lon, source
        FROM samples
        ORDER BY received_at_ms DESC, id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}

def rebuild_log_files_from_db() -> int:
    base = Path(LOG_DIR)
    base.mkdir(parents=True, exist_ok=True)

    for p in base.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_SUFFIX}"):
        try:
            p.unlink()
        except OSError:
            pass

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t_ms, lap, V, A, Ah, mph, torque, lat, lon, source, received_at_ms
        FROM samples
        ORDER BY received_at_ms ASC, id ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    written = 0
    for row in rows:
        received_at_ms = row.get("received_at_ms")
        if received_at_ms is None:
            continue
        append_sample_log(row, int(received_at_ms))
        written += 1
    return written

def get_laps() -> List[int]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT lap FROM samples ORDER BY lap ASC")
    laps = [int(r["lap"]) for r in cur.fetchall()]
    conn.close()
    return laps

def get_lap_timeseries(lap: int) -> List[Dict[str, Any]]:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t_ms, V, A, Ah, mph, torque
        FROM samples
        WHERE lap = ?
        ORDER BY t_ms ASC
    """, (lap,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_lap_gps(lap: int) -> List[Dict[str, Any]]:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t_ms, lat, lon
        FROM samples
        WHERE lap = ? AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY t_ms ASC
    """, (lap,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_latest_lap() -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT MAX(lap) AS maxlap FROM samples")
    row = cur.fetchone()
    conn.close()
    if row and row["maxlap"] is not None:
        return int(row["maxlap"])
    return 0

def get_recent_logs(limit: int) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 2000))
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT received_at_ms, t_ms, lap, V, A, Ah, mph, torque, lat, lon, source
        FROM samples
        ORDER BY received_at_ms DESC, id DESC
        LIMIT ?
    """, (safe_limit,))
    rows = cur.fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    for row in out:
        ms = row.get("received_at_ms")
        row["received_at_iso_utc"] = utc_iso(ms) if ms else None
    return out

def _parse_lap_selector(raw: Optional[str]) -> List[int]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []

    values: set[int] = set()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo = int(lo_s.strip())
            hi = int(hi_s.strip())
            if lo > hi:
                lo, hi = hi, lo
            span = hi - lo
            if span > 2000:
                raise ValueError("lap range too large")
            for v in range(lo, hi + 1):
                values.add(v)
        else:
            values.add(int(part))

    if len(values) > 2000:
        raise ValueError("too many lap values")
    return sorted(values)

def _normalize_metric(metric: str) -> str:
    m = (metric or "V").strip()
    if m not in ALLOWED_LOG_METRICS:
        raise ValueError(f"unsupported metric: {m}")
    return m

def _normalize_group(group_by: str) -> str:
    g = (group_by or "none").strip().lower()
    if g not in ALLOWED_LOG_GROUPS:
        raise ValueError(f"unsupported group_by: {g}")
    return g

def _build_logs_where_sql(
    laps: Optional[str],
    from_ms: Optional[int],
    to_ms: Optional[int],
    metric: str,
    min_value: Optional[float],
    max_value: Optional[float],
) -> Tuple[str, List[Any], List[int]]:
    where_parts: List[str] = []
    params: List[Any] = []

    if from_ms is not None:
        where_parts.append("received_at_ms >= ?")
        params.append(int(from_ms))
    if to_ms is not None:
        where_parts.append("received_at_ms <= ?")
        params.append(int(to_ms))
    if from_ms is not None and to_ms is not None and int(from_ms) > int(to_ms):
        raise ValueError("from_ms must be <= to_ms")

    lap_values = _parse_lap_selector(laps)
    if lap_values:
        placeholders = ",".join(["?"] * len(lap_values))
        where_parts.append(f"lap IN ({placeholders})")
        params.extend(lap_values)

    if min_value is not None:
        where_parts.append(f"{metric} >= ?")
        params.append(float(min_value))
    if max_value is not None:
        where_parts.append(f"{metric} <= ?")
        params.append(float(max_value))
    if min_value is not None and max_value is not None and float(min_value) > float(max_value):
        raise ValueError("min_value must be <= max_value")

    where_sql = ""
    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)
    return where_sql, params, lap_values

def query_logs(
    limit: int,
    group_by: str = "none",
    metric: str = "V",
    laps: Optional[str] = None,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 5000))
    metric_col = _normalize_metric(metric)
    group_mode = _normalize_group(group_by)
    where_sql, params, lap_values = _build_logs_where_sql(
        laps=laps,
        from_ms=from_ms,
        to_ms=to_ms,
        metric=metric_col,
        min_value=min_value,
        max_value=max_value,
    )

    conn = db()
    cur = conn.cursor()

    if group_mode == "none":
        cur.execute(f"""
            SELECT received_at_ms, t_ms, lap, V, A, Ah, mph, torque, lat, lon, source
            FROM samples
            {where_sql}
            ORDER BY received_at_ms DESC, id DESC
            LIMIT ?
        """, (*params, safe_limit))
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            ms = row.get("received_at_ms")
            row["received_at_iso_utc"] = utc_iso(ms) if ms else None
        conn.close()
        return {
            "mode": group_mode,
            "metric": metric_col,
            "limit": safe_limit,
            "filters": {
                "laps": laps or "",
                "parsed_laps": lap_values,
                "from_ms": from_ms,
                "to_ms": to_ms,
                "min_value": min_value,
                "max_value": max_value,
            },
            "rows": rows,
        }

    group_expr = "lap" if group_mode == "lap" else "strftime('%Y-%m-%d', received_at_ms / 1000, 'unixepoch')"
    cur.execute(f"""
        SELECT
            {group_expr} AS group_key,
            COUNT(*) AS samples,
            MIN(received_at_ms) AS first_received_at_ms,
            MAX(received_at_ms) AS last_received_at_ms,
            MIN({metric_col}) AS min_metric,
            AVG({metric_col}) AS avg_metric,
            MAX({metric_col}) AS max_metric
        FROM samples
        {where_sql}
        GROUP BY group_key
        ORDER BY group_key DESC
        LIMIT ?
    """, (*params, safe_limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for row in rows:
        first_ms = row.get("first_received_at_ms")
        last_ms = row.get("last_received_at_ms")
        row["first_received_at_iso_utc"] = utc_iso(first_ms) if first_ms else None
        row["last_received_at_iso_utc"] = utc_iso(last_ms) if last_ms else None

    return {
        "mode": group_mode,
        "metric": metric_col,
        "limit": safe_limit,
        "filters": {
            "laps": laps or "",
            "parsed_laps": lap_values,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "min_value": min_value,
            "max_value": max_value,
        },
        "rows": rows,
    }

def get_logs_facets() -> Dict[str, Any]:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT lap, COUNT(*) AS samples
        FROM samples
        GROUP BY lap
        ORDER BY lap DESC
        LIMIT 500
    """)
    lap_rows = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT
            strftime('%Y-%m-%d', received_at_ms / 1000, 'unixepoch') AS day,
            COUNT(*) AS samples
        FROM samples
        GROUP BY day
        ORDER BY day DESC
        LIMIT 366
    """)
    day_rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"laps": lap_rows, "days": day_rows}

# -----------------------------
# Telemetry parsing / enrichment
# -----------------------------
def enrich_sample(raw: Dict[str, Any]) -> Dict[str, Any]:
    # Normalize time key.
    # Some devices send millis-since-boot values; treat those as non-epoch and
    # replace with server epoch time so charts/tables are coherent.
    t_val = raw.get("t")
    now_ms = int(time.time() * 1000)
    if t_val is None:
        t_ms = now_ms
    else:
        try:
            candidate = int(t_val)
        except Exception:
            candidate = now_ms

        # Jan 1 2001 UTC in ms.
        if candidate < 978307200000:
            t_ms = now_ms
        else:
            t_ms = candidate

    lap = int(raw.get("lap", 0))

    V = raw.get("V")
    A = raw.get("A")
    Ah = raw.get("Ah")

    mph = raw.get("mph")
    # Speed should be in mph
   
    torque = raw.get("torque")
    # If torque missing but current present, estimate it:
    if torque is None and A is not None:
        try:
            torque = float(A) * K_T_NM_PER_AMP * GEAR_RATIO * DRIVETRAIN_EFF
        except Exception:
            torque = None

    lat = raw.get("lat")
    lon = raw.get("lon")
    source = str(raw.get("source", "unknown")).strip() or "unknown"

    return {
        "t_ms": t_ms,
        "lap": lap,
        "V": V,
        "A": A,
        "Ah": Ah,
        "mph": mph,
        "torque": torque,
        "lat": lat,
        "lon": lon,
        "source": source[:64],
    }

async def ingest_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    global latest_sample
    sample = enrich_sample(raw)
    latest_sample = sample
    insert_sample(sample)
    await broadcast(sample)
    return sample

async def broadcast(sample: Dict[str, Any]):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(json.dumps(sample))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)

# -----------------------------
# Serial reader task
# -----------------------------
async def serial_reader():
    global latest_sample
    while True:
        if serial is None:
            await asyncio.sleep(1.0)
            continue

        if simulation_mode:
            await asyncio.sleep(0.1)
            continue
            
        try:
            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
            ser.reset_input_buffer()    

            while True:
                if simulation_mode:
                    break
                    
                line = ser.readline().decode(errors="ignore").strip()
                if not line:
                    await asyncio.sleep(0.001)
                    continue

                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    # log bad lines for debugging
                    continue

                await ingest_payload(raw)

        except Exception:
            # port unplugged, wrong port, etc.
            await asyncio.sleep(1.0)

async def simulation_runner():
    """Simulate telemetry data for testing - runs for 15 seconds"""
    global latest_sample, simulation_mode
    import math
    import random
    
    lap = 1
    start_time = int(time.time() * 1000)
    simulation_duration_ms = 15000  # 15 seconds (change as needed for testing)
    
    while simulation_mode:
        elapsed_ms = int(time.time() * 1000) - start_time
        t_sec = elapsed_ms / 1000.0
        
        # Stop after given time
        if elapsed_ms >= simulation_duration_ms:
            break
        
        # simulates a vehiecle varying speeed (simulation)
        throttle = 0.5 + 0.4 * math.sin(t_sec * 0.5)
        
        # Voltade drops slightly with throttle inputs (simulation)
        voltage = 48.0 - (throttle * 2.0)
        
        # Current correlates with throttle (simulation)
        current = throttle * 100.0 + random.gauss(0, 2)
        
        # Speed increases with throttle (simplified physics)
        speed = throttle * 40.0 + random.gauss(0, 1)
        
        # Amp-hours accumulate over time
        amp_hours = (elapsed_ms / 3600000.0) * 50.0
        
        # Torque from current estimation
        torque = current * K_T_NM_PER_AMP * GEAR_RATIO * DRIVETRAIN_EFF
        
        # Simulate GPS coordinates (spiraling pattern for now)
        lat = 40.7128 + (t_sec * 0.00001) * math.cos(t_sec * 0.1)
        lon = -74.0060 + (t_sec * 0.00001) * math.sin(t_sec * 0.1)
        
        payload = {
            "t": elapsed_ms + start_time,
            "lap": lap,
            "V": round(voltage, 1),
            "A": round(current, 1),
            "Ah": round(amp_hours, 2),
            "mph": round(speed, 1),
            "torque": round(torque, 1),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "source": "simulation",
        }
        await ingest_payload(payload)
        
        # Simulate at ~10 Hz
        await asyncio.sleep(0.1)
    
    # similation stop
    simulation_mode = False

@app.post("/api/simulation/start")
async def start_simulation():
    global simulation_mode, simulation_task
    if not simulation_mode:
        simulation_mode = True
        simulation_task = asyncio.create_task(simulation_runner())
        return {"status": "simulation started"}
    return {"status": "simulation already running"}

@app.post("/api/simulation/stop")
async def stop_simulation():
    global simulation_mode, simulation_task
    if simulation_mode:
        simulation_mode = False
        if simulation_task:
            await asyncio.sleep(0.2)  
        return {"status": "simulation stopped"}
    return {"status": "simulation not running"}

@app.post("/api/simulation/clear")
async def clear_simulation():
    global simulation_mode, simulation_task, latest_sample

    if simulation_mode:
        simulation_mode = False
        if simulation_task:
            await asyncio.sleep(0.25)

    # Also remove legacy rows that were created before source tagging existed.
    deleted_rows = delete_samples_by_sources(["simulation", "unknown"])
    rebuilt_rows = rebuild_log_files_from_db()
    latest_sample = get_latest_sample_from_db()

    return {
        "ok": True,
        "deleted_rows": deleted_rows,
        "log_rows_rebuilt": rebuilt_rows,
        "running": simulation_mode,
    }

@app.get("/api/simulation/status")
def get_simulation_status():
    return {"running": simulation_mode}

@app.on_event("startup")
async def startup_event():
    if ENABLE_SERIAL_READER and serial is not None:
        asyncio.create_task(serial_reader())
    maybe_prune_retention()

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "serial_reader": ENABLE_SERIAL_READER,
        "serial_module_present": serial is not None,
        "serial_port": SERIAL_PORT,
        "serial_baud": SERIAL_BAUD,
        "retention_days": RETENTION_DAYS,
        "log_dir": str(Path(LOG_DIR).resolve()),
        "last_prune": last_prune_report,
    }

# -----------------------------
#syntax points
@app.get("/")
def root():
    return {"ok": True, "pages": ["/static/index.html", "/static/lap.html", "/static/gps.html", "/static/logs.html"]}

@app.get("/api/latest")
def api_latest():
    return latest_sample or {}

@app.post("/api/ingest")
async def api_ingest(payload: Dict[str, Any], request: Request):
    if INGEST_TOKEN:
        presented = request.headers.get("x-ingest-token", "")
        if presented != INGEST_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid ingest token",
            )

    sample = await ingest_payload(payload)
    return {"ok": True, "t_ms": sample["t_ms"]}

@app.get("/api/laps")
def api_laps():
    return {"laps": get_laps(), "latest": get_latest_lap()}

@app.get("/api/lap/{lap_id}/timeseries")
def api_lap_timeseries(lap_id: int):
    return {"lap": lap_id, "data": get_lap_timeseries(lap_id)}

@app.get("/api/lap/{lap_id}/gps")
def api_lap_gps(lap_id: int):
    return {"lap": lap_id, "points": get_lap_gps(lap_id)}

@app.get("/api/logs/recent")
def api_logs_recent(limit: int = 300):
    return {"limit": max(1, min(int(limit), 2000)), "rows": get_recent_logs(limit)}

@app.get("/api/logs/query")
def api_logs_query(
    limit: int = 300,
    group_by: str = "none",
    metric: str = "V",
    laps: Optional[str] = None,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
):
    try:
        return query_logs(
            limit=limit,
            group_by=group_by,
            metric=metric,
            laps=laps,
            from_ms=from_ms,
            to_ms=to_ms,
            min_value=min_value,
            max_value=max_value,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

@app.get("/api/logs/facets")
def api_logs_facets():
    return get_logs_facets()

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        # sends the latest info immediately
        if latest_sample:
            await websocket.send_text(json.dumps(latest_sample))
        while True:
            await websocket.receive_text()  # keeps alive (client can send pings)
    except WebSocketDisconnect:
        clients.discard(websocket)
