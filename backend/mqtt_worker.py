import os
import json
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
from database import get_db_connection, log_event

# Load environment variables from .env
load_dotenv()

logger = logging.getLogger("asense_mqtt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "asense")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

# In-memory track of pending command confirmations: {command_id: {"event": threading.Event, "result": None}}
pending_commands = {}

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to Mosquitto broker.")
        # Subscribe to all A-SENSE topics
        client.subscribe("asense/v1/#", qos=1)
        logger.info("Subscribed to asense/v1/#")
    else:
        logger.error(f"Failed to connect to Mosquitto, rc={rc}")

def handle_telemetry(payload_str: str):
    try:
        data = json.loads(payload_str)
        device_id = data.get("device_id", "sensor-01")
        temp = float(data.get("temperature_c"))
        hum = float(data.get("humidity_pct"))
        uptime_ms = data.get("uptime_ms")
        sequence = data.get("sequence")

        # Range validation (DHT11 reasonable physical bounds)
        if not (-10.0 <= temp <= 60.0) or not (0.0 <= hum <= 100.0):
            logger.warning(f"Discarding out-of-bounds telemetry: temp={temp}, hum={hum}")
            return

        now_utc = datetime.now(timezone.utc).isoformat()

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sensor_readings (device_id, temperature_c, humidity_pct, uptime_ms, sequence, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (device_id, temp, hum, uptime_ms, sequence, now_utc))

        # Update device state
        cur.execute("""
            INSERT INTO device_state (device_id, kind, online, last_seen_at, reported_state, updated_at)
            VALUES (?, 'sensor', 1, ?, 'active', ?)
            ON CONFLICT(device_id) DO UPDATE SET
                online=1,
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at
        """, (device_id, now_utc, now_utc))

        conn.commit()
        conn.close()
        logger.debug(f"Saved reading: {temp}°C, {hum}% from {device_id}")

    except Exception as e:
        logger.error(f"Error processing telemetry: {e}")

def handle_status(topic: str, payload_str: str):
    # topic: asense/v1/devices/{device_id}/status
    parts = topic.split("/")
    if len(parts) >= 4:
        device_id = parts[3]
        status = payload_str.strip()
        online = 1 if status == "online" else 0
        now_utc = datetime.now(timezone.utc).isoformat()

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO device_state (device_id, kind, online, last_seen_at, reported_state, updated_at)
            VALUES (?, 'device', ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                online=excluded.online,
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at
        """, (device_id, online, now_utc, status, now_utc))
        conn.commit()
        conn.close()

        log_event("DEVICE_STATUS", "INFO" if online else "WARNING", 
                  f"Device {device_id} is now {status}", {"device_id": device_id, "status": status})
        logger.info(f"Device status: {device_id} -> {status}")

def handle_actuator_state(topic: str, payload_str: str):
    # topic: asense/v1/actuators/{relay_id}/state
    parts = topic.split("/")
    if len(parts) >= 4:
        relay_id = parts[3]
        try:
            data = json.loads(payload_str)
            state = data.get("state")
            now_utc = datetime.now(timezone.utc).isoformat()

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO device_state (device_id, kind, online, last_seen_at, reported_state, updated_at)
                VALUES (?, 'relay', 1, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    online=1,
                    reported_state=excluded.reported_state,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
            """, (relay_id, now_utc, state, now_utc))
            conn.commit()
            conn.close()
            logger.info(f"Relay state update: {relay_id} -> {state}")
        except Exception as e:
            logger.error(f"Error parsing actuator state: {e}")

def handle_ack(topic: str, payload_str: str):
    # topic: asense/v1/events/{relay_id}/ack
    try:
        data = json.loads(payload_str)
        cmd_id = data.get("command_id")
        result = data.get("result")
        state = data.get("state")
        error = data.get("error")
        now_utc = datetime.now(timezone.utc).isoformat()

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE commands
            SET status=?, confirmed_at=?, error=?
            WHERE command_id=?
        """, (result, now_utc, error, cmd_id))
        conn.commit()
        conn.close()

        # If a thread was waiting on this command_id, unblock it
        if cmd_id in pending_commands:
            pending_commands[cmd_id]["result"] = data
            pending_commands[cmd_id]["event"].set()

        logger.info(f"Command ack processed: {cmd_id} -> {result} ({state})")
    except Exception as e:
        logger.error(f"Error handling ack: {e}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore")

    if topic.endswith("/telemetry"):
        handle_telemetry(payload)
    elif topic.endswith("/status"):
        handle_status(topic, payload)
    elif "/actuators/" in topic and topic.endswith("/state"):
        handle_actuator_state(topic, payload)
    elif topic.endswith("/ack"):
        handle_ack(topic, payload)

def create_mqtt_client():
    try:
        # Paho MQTT v2.x compatibility
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        # Fallback to older Paho MQTT v1.x
        client = mqtt.Client()

    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    return client
