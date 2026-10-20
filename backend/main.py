import os
import json
import uuid
import threading
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import init_db, get_db_connection, log_event
from mqtt_worker import create_mqtt_client, pending_commands
import agent

logger = logging.getLogger("asense_api")

mqtt_client = None

def execute_relay_command(device_id: str, state: str):
    """Internal verified relay command helper shared by REST API and Agent."""
    if device_id not in ["relay-01", "relay-02"]:
        raise ValueError("Unknown actuator device")
    if not mqtt_client or not mqtt_client.is_connected():
        raise RuntimeError("MQTT broker disconnected")

    cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
    now_utc = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO commands (command_id, device_id, requested_state, source, status, requested_at)
        VALUES (?, ?, ?, 'orchestrator', 'pending', ?)
    """, (cmd_id, device_id, state, now_utc))
    conn.commit()
    conn.close()

    ack_event = threading.Event()
    pending_commands[cmd_id] = {"event": ack_event, "result": None}

    topic = f"asense/v1/actuators/{device_id}/set"
    payload = {
        "command_id": cmd_id,
        "state": state,
        "requested_by": "asense_agent",
        "issued_at": now_utc
    }
    mqtt_client.publish(topic, json.dumps(payload), qos=1)

    confirmed = ack_event.wait(timeout=3.5)
    ack_data = pending_commands.pop(cmd_id, {}).get("result")

    if not confirmed or not ack_data:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE commands SET status='timeout' WHERE command_id=?", (cmd_id,))
        conn.commit()
        conn.close()
        raise TimeoutError(f"Device {device_id} timed out acknowledging command")

    if ack_data.get("result") != "accepted":
        raise RuntimeError(f"Device rejected command: {ack_data.get('error')}")

    return {
        "command_id": cmd_id,
        "device_id": device_id,
        "confirmed_state": ack_data.get("state")
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database, MQTT Worker, Agent executor, and Heartbeat
    init_db()
    agent.register_command_executor(execute_relay_command)
    
    # Start Heartbeat Cron & Cycle Engine
    import heartbeat
    heartbeat.register_command_executor(execute_relay_command)
    heartbeat.start_heartbeat()

    # Start Telegram Bot integration
    import telegram_bot
    telegram_bot.register_handlers(agent.chat_with_agent, execute_relay_command)
    telegram_bot.start_telegram_bot()

    global mqtt_client
    mqtt_client = create_mqtt_client()
    try:
        broker_host = os.getenv("MQTT_BROKER", "127.0.0.1")
        broker_port = int(os.getenv("MQTT_PORT", 1883))
        mqtt_client.connect(broker_host, broker_port, 60)
        mqtt_client.loop_start()
        logger.info("MQTT loop started successfully.")
    except Exception as e:
        logger.error(f"Failed to start MQTT client: {e}")
    yield
    # Shutdown
    import heartbeat
    heartbeat.stop_heartbeat()
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logger.info("MQTT client stopped.")

app = FastAPI(title="A-SENSE Local API", lifespan=lifespan)

# Mount static files for dashboard
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_dashboard():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "A-SENSE API is running. Dashboard static files not found."}

@app.get("/api/health")
def get_health():
    db_ok = False
    try:
        conn = get_db_connection()
        conn.cursor().execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        pass

    mqtt_ok = mqtt_client.is_connected() if mqtt_client else False

    return {
        "status": "healthy" if (db_ok and mqtt_ok) else "degraded",
        "database": "connected" if db_ok else "unavailable",
        "mqtt_broker": "connected" if mqtt_ok else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/state")
def get_state():
    conn = get_db_connection()
    cur = conn.cursor()

    # Get latest sensor reading
    cur.execute("""
        SELECT * FROM sensor_readings
        ORDER BY id DESC LIMIT 1
    """)
    latest_reading = cur.fetchone()

    # Get device states
    cur.execute("SELECT * FROM device_state")
    devices = [dict(row) for row in cur.fetchall()]
    conn.close()

    reading_dict = dict(latest_reading) if latest_reading else None

    # Calculate freshness (seconds ago)
    freshness_sec = None
    if reading_dict and reading_dict.get("received_at"):
        try:
            rx_time = datetime.fromisoformat(reading_dict["received_at"])
            freshness_sec = round((datetime.now(timezone.utc) - rx_time).total_seconds(), 1)
        except Exception:
            pass

    return {
        "latest_reading": reading_dict,
        "freshness_sec": freshness_sec,
        "devices": devices,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/readings")
def get_readings(limit: int = 50):
    limit = min(max(1, limit), 200) # bounded limit
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM sensor_readings
        ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(rows), "readings": rows}

@app.get("/api/events")
def get_events(limit: int = 50):
    limit = min(max(1, limit), 100)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM events
        ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(rows), "events": rows}

class RelayCommandRequest(BaseModel):
    state: str = Field(..., pattern="^(ON|OFF)$")

@app.post("/api/devices/{device_id}/command")
def send_device_command(device_id: str, req: RelayCommandRequest):
    try:
        res = execute_relay_command(device_id, req.state)
        return {
            "status": "success",
            "command_id": res["command_id"],
            "device_id": device_id,
            "confirmed_state": res["confirmed_state"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/heartbeat/tasks")
def list_scheduled_tasks():
    import heartbeat
    return {"tasks": heartbeat.get_active_tasks()}

class AutoModeRequest(BaseModel):
    enabled: bool

@app.get("/api/automation/auto-mode")
def get_auto_mode():
    import heartbeat
    return {"auto_mode": heartbeat.get_auto_mode()}

@app.post("/api/automation/auto-mode")
def set_auto_mode(req: AutoModeRequest):
    import heartbeat
    new_val = heartbeat.set_auto_mode(req.enabled)
    return {"status": "success", "auto_mode": new_val}

@app.get("/api/proactive/alerts")
def get_pending_alerts():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM proactive_alerts WHERE status = 'pending' ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"alerts": rows}

@app.post("/api/proactive/alerts/{alert_id}/approve")
def approve_proactive_alert(alert_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM proactive_alerts WHERE id = ? AND status = 'pending'", (alert_id,))
    alert = cur.fetchone()

    if not alert:
        conn.close()
        raise HTTPException(status_code=404, detail="Alert not found or already resolved")

    device_id = alert["device_id"]
    recommended_state = alert["recommended_state"]

    # Execute physical action
    try:
        res = execute_relay_command(device_id, recommended_state)
        now_utc = datetime.now(timezone.utc).isoformat()
        cur.execute("UPDATE proactive_alerts SET status = 'approved', resolved_at = ? WHERE id = ?", (now_utc, alert_id))
        conn.commit()
        conn.close()
        return {"status": "approved", "result": res}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/proactive/alerts/{alert_id}/dismiss")
def dismiss_proactive_alert(alert_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).isoformat()
    cur.execute("UPDATE proactive_alerts SET status = 'dismissed', resolved_at = ? WHERE id = ?", (now_utc, alert_id))
    conn.commit()
    conn.close()
    return {"status": "dismissed"}

class AgentChatRequest(BaseModel):
    message: str

@app.post("/api/agent/chat")
def chat_with_assistant(req: AgentChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    return agent.chat_with_agent(req.message)

