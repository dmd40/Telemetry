import asyncio
import json
import os
import sqlite3
import time
from typing import Optional, Dict, Any, List

import serial
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = "telemetry.db"

# -----------------------------
# Config - adjust via environment, change throughout different vehicels 
# We should keep each car we have, seperate files/databases etc.
# i.e VOLT = car_1 , SPECTRE - car_2 etc. , and VOID = car_3
# -----------------------------
SERIAL_PORT = os.environ.get("TELEM_PORT", "COM5")   # ONLY OPERABLE IN WINDOWS 
BAUD = int(os.environ.get("TELEM_BAUD", "115200"))

# Torque model (only used if incoming packet data doesn't include torque)
# torque (Nm) ≈ k_t * I_motor * gear_ratio * drivetrain_eff
# You must set k_t and gear_ratio for your motor/drivetrain to be meaningful.
K_T_NM_PER_AMP = float(os.environ.get("K_T", "0.06"))      # Example only
GEAR_RATIO = float(os.environ.get("GEAR_RATIO", "10.0"))   # Example only
DRIVETRAIN_EFF = float(os.environ.get("EFF", "0.9"))      # Example only

# Lap detection:
# Best practice is to do lap counting on-vehicle with a start/finish GPS gate and transmit lap id.
# If not available, you can implement server-side lap detection later.
# -----------------------------

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

clients: set[WebSocket] = set()
latest_sample: Dict[str, Any] = {}

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
            lon REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_lap_t ON samples(lap, t_ms)")
    conn.commit()
    conn.close()

init_db()

def insert_sample(s: Dict[str, Any]):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO samples (t_ms, lap, V, A, Ah, mph, torque, lat, lon)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ))
    conn.commit()
    conn.close()

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
        SELECT t_ms, V, A, mph, torque
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

# -----------------------------
# Telemetry parsing / enrichment
# -----------------------------
def enrich_sample(raw: Dict[str, Any]) -> Dict[str, Any]:
    # Normalize time key
    t_val = raw.get("t")
    if t_val is None:
        # fallback: server time in ms
        t_val = int(time.time() * 1000)
    t_ms = int(t_val)

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
    }

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
        if simulation_mode:
            await asyncio.sleep(0.1)
            continue
            
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
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

                sample = enrich_sample(raw)
                latest_sample = sample

                insert_sample(sample)
                await broadcast(sample)

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
        
        sample = {
            "t_ms": elapsed_ms + start_time,
            "lap": lap,
            "V": round(voltage, 1),
            "A": round(current, 1),
            "Ah": round(amp_hours, 2),
            "mph": round(speed, 1),
            "torque": round(torque, 1),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        }
        
        latest_sample = sample
        insert_sample(sample)
        await broadcast(sample)
        
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

@app.get("/api/simulation/status")
def get_simulation_status():
    return {"running": simulation_mode}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(serial_reader())

# -----------------------------
#syntax points
@app.get("/")
def root():
    return {"ok": True, "pages": ["/static/index.html", "/static/lap.html", "/static/gps.html"]}

@app.get("/api/latest")
def api_latest():
    return latest_sample or {}

@app.get("/api/laps")
def api_laps():
    return {"laps": get_laps(), "latest": get_latest_lap()}

@app.get("/api/lap/{lap_id}/timeseries")
def api_lap_timeseries(lap_id: int):
    return {"lap": lap_id, "data": get_lap_timeseries(lap_id)}

@app.get("/api/lap/{lap_id}/gps")
def api_lap_gps(lap_id: int):
    return {"lap": lap_id, "points": get_lap_gps(lap_id)}

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        # sends the latest info immediately
        if latest_sample:
            await websocket.send_text(json.dumps(latest_sample))
        while True:
            await websocket.receive_text()  # keep alive (client can send pings)
    except WebSocketDisconnect:
        clients.discard(websocket)
