import sqlite3
import os
import json
from datetime import datetime, timezone

DB_PATH = os.getenv("ASENSE_DB_PATH", "asense.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Sensor readings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sensor_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        temperature_c REAL NOT NULL,
        humidity_pct REAL NOT NULL,
        uptime_ms INTEGER,
        sequence INTEGER,
        received_at TEXT NOT NULL
    );
    """)

    # Device state table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS device_state (
        device_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        online INTEGER NOT NULL DEFAULT 0,
        last_seen_at TEXT NOT NULL,
        reported_state TEXT,
        updated_at TEXT NOT NULL
    );
    """)

    # Commands audit table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS commands (
        command_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        requested_state TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        confirmed_at TEXT,
        error TEXT
    );
    """)

    # Events audit table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        summary TEXT NOT NULL,
        details_json TEXT
    );
    """)

    # Proactive recommendations & alerts table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proactive_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,
        message TEXT NOT NULL,
        device_id TEXT,
        recommended_state TEXT,
        status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'dismissed'
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );
    """)

    # Preferences & Automation rules
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS automation_rules (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        config_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()

def log_event(event_type: str, severity: str, summary: str, details: dict = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_utc = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details) if details else "{}"
    cursor.execute("""
    INSERT INTO events (occurred_at, event_type, severity, summary, details_json)
    VALUES (?, ?, ?, ?, ?)
    """, (now_utc, event_type, severity, summary, details_str))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
