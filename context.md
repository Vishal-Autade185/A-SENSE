# A-SENSE Execution Context & Activity Log

**Project:** A-SENSE (Autonomous Multi-Agent Orchestration Framework for Proactive Home Management)  
**Host:** Raspberry Pi 5 (4 GB RAM, 64-bit OS `aarch64`, IP: `192.168.1.18`, User: `project`, Hostname: `project123`)  
**Nodes:** ESP32 Sensor Node (DHT11), ESP32 Relay Node (Dual Relay Module)  
**MQTT Broker:** Mosquitto running on Pi (`192.168.1.18:1883`, auth user: `asense`)  
**LLM Runtime:** Ollama on Pi (`qwen3.5:0.8b` CPU-only, loopback `127.0.0.1:11434`)  
**GitHub Repository:** `https://github.com/Vishal-Autade185/A-SENSE.git` (tracked on branch `main`)  

---

## 1. Milestones & Progress Tracker

| Milestone | Description | Status | Notes |
|---|---|---|---|
| **M0** | Pi Baseline Setup | **COMPLETED** | Pi OS updated, Mosquitto authenticated, Ollama + `qwen3.5:0.8b` verified. |
| **M1** | Sensor Node Firmware | **COMPLETED** | DHT11 on GPIO 15, VCC on 5V (`VIN`). Publishing telemetry every 10s with LWT. |
| **M2** | Relay Node Firmware | **COMPLETED** | Dual relays on GPIO 13 (`relay-01`) & GPIO 14 (`relay-02`). Safe boot OFF, MQTT command/ack verified. |
| **M3** | Pi Backend & Ingestion | **COMPLETED** | FastAPI running on port 8000, SQLite database active, MQTT worker connected, devices registered online. |
| **M4** | MCP Tools & Agent Loop | **COMPLETED** | Ollama reasoning (`qwen3.5:0.8b`) with allow-listed tools + deterministic safety fast-path for guaranteed physical switching. |
| **M5** | Dashboard & Automation | **COMPLETED** | LAN web dashboard, dynamic user preference thermostat rules, and 500ms background Heartbeat Cron Engine. |

---

## 2. Hardware Mapping & Pinouts

### Sensor Node (`sensor-01`)
- **Microcontroller:** ESP32 DevKit V1
- **Sensor:** DHT11 (Temperature & Humidity)
- **Data Pin:** GPIO 15
- **Power Pin:** `VIN` / 5V (Note: 3.3V caused dropouts/brownouts during Wi-Fi transmission; 5V is stable).
- **MQTT Topics:**
  - Telemetry: `asense/v1/sensors/room1/telemetry` (QoS 0)
  - Availability: `asense/v1/devices/sensor-01/status` (QoS 1, retain: true, LWT: `offline`)

### Relay Node (`relay-node`)
- **Microcontroller:** ESP32 DevKit V1
- **Relay 1 (`relay-01`):** GPIO 13
- **Relay 2 (`relay-02`):** GPIO 14 (Avoided strapping pin GPIO 12 which caused SPI flash errors).
- **Active Level:** Active-Low (`LOW = ON`, `HIGH = OFF`). Safe default at boot is `OFF`.
- **MQTT Topics:**
  - Status: `asense/v1/devices/relay-node/status`
  - Relay 1 Set: `asense/v1/actuators/relay-01/set`
  - Relay 1 State: `asense/v1/actuators/relay-01/state`
  - Relay 1 Ack: `asense/v1/events/relay-01/ack`
  - Relay 2 Set: `asense/v1/actuators/relay-02/set`
  - Relay 2 State: `asense/v1/actuators/relay-02/state`
  - Relay 2 Ack: `asense/v1/events/relay-02/ack`

---

## 3. Log of Key Actions Taken

1. **Product Specification Ingestion**:
   - Reviewed `product.md` source of truth for architectural constraints, MQTT contract, and safety guidelines.
2. **Sensor Node Creation & Debugging**:
   - Wrote `firmware/sensor_node/sensor_node.ino`.
   - Flashed to ESP32 with DHT11 on GPIO 15.
   - Identified power supply issue: 3.3V caused `online`/`offline` flapping and `isnan()` failures. Resolved by shifting VCC to 5V (`VIN`). Verified steady telemetry streams every 10s.
3. **Relay Node Creation & Hardware Flashing**:
   - Wrote `firmware/relay_node/relay_node.ino`.
   - Identified flashing error (`exit status 2` / communication failure with flash chip) due to GPIO 12 being an ESP32 strapping pin for flash voltage. Shifted Relay 2 to GPIO 14.
   - Verified both relays switch accurately via MQTT commands with mechanical clicks and state/ack confirmations.
4. **Backend Architecture & Deployment**:
   - Implemented SQLite schema (`database.py`), background ingestion worker (`mqtt_worker.py`), FastAPI service (`main.py`), and web dashboard (`index.html`).
   - Resolved `rc=5` (not authorized) by adding `load_dotenv()` to `mqtt_worker.py` and adding Paho MQTT v2 compatibility.
   - Fixed missing `import json` in `main.py`.
   - Verified end-to-end functionality on LAN: live telemetry ingestion, freshness counter, and verified 2-way physical relay command/acknowledgement.
5. **Heartbeat & Cron Engine (`heartbeat.py`)**:
   - Implemented background Heartbeat Engine ticking every 500ms for temporal automation and scheduled tasks.
   - Verified repeating on/off cycles (e.g. 5.0s interval for 3 cycles) with clockwork precision, logging each tick (ON -> OFF -> ON -> OFF), and safely leaving the relay OFF at completion.
   - Differentiated between cycle status queries (`is cycle working`) and scheduling commands.
   - Dynamic user preference temperature automation: configurable above/below thresholds with automatic actuation.
6. **Proactive Home Management Engine**:
   - Added `proactive_alerts` SQLite table for pending, approved, and dismissed recommendations.
   - Background Heartbeat periodically evaluates room environment (e.g. room temperature >= 32.5°C while cooling fan is OFF).
   - Generates proactive smart suggestions with recommended state.
   - Added REST endpoints: `GET /api/proactive/alerts`, `POST /api/proactive/alerts/{id}/approve`, `POST /api/proactive/alerts/{id}/dismiss`.
   - Built a dynamic proactive notification banner in `index.html` with one-click approval and dismissal.
