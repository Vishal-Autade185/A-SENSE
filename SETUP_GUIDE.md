# A-SENSE: Complete Hardware & Software Deployment Guide

> **A step-by-step blueprint to reproduce and deploy the A-SENSE Edge-Native Smart Home System on any Raspberry Pi and ESP32 hardware.**

---

## 🛠️ 1. Bill of Materials (Hardware Requirements)

| Item | Component | Specification | Quantity | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Central Hub SBC** | Raspberry Pi 5 (4GB or 8GB RAM) | 1 | Raspberry Pi 4 (4GB+) also supported |
| **2** | **Power Supply** | Official 27W USB-C PD Power Supply (5V/5A) | 1 | Ensures Pi 5 runs without throttling |
| **3** | **MicroSD Card / NVMe** | 32GB+ Class 10 / A2 MicroSD card | 1 | NVMe M.2 SSD via PCIe HAT recommended |
| **4** | **Microcontroller Nodes** | ESP32 DevKit V1 (30-pin or 38-pin) | 2 | Node 1: Sensor, Node 2: Actuator |
| **5** | **Environmental Sensor** | DHT11 or DHT22 Temperature & Humidity Sensor | 1 | 3-pin module with onboard pull-up resistor |
| **6** | **Relay Actuator Module** | 2-Channel 5V Optocoupler Relay Board | 1 | Active-LOW trigger board |
| **7** | **Prototyping & Wiring** | Solderless Breadboards & Jumper Wires (M-M, M-F) | 1 set | For clean bench testing |
| **8** | **USB Cables** | Micro-USB / USB-C Cables for ESP32 | 2 | For initial flashing and 5V power |

---

## 🔌 2. Hardware Wiring Schematics

### Node 1: Sensor Node (ESP32 + DHT11)
> ⚠️ **Critical Power Note:** Connect the DHT11 VCC to the ESP32 **VIN pin (5V)** instead of 3.3V. The internal 3.3V rail on ESP32 development boards experiences voltage brownouts during 2.4GHz Wi-Fi bursts, which causes DHT `NaN` read errors.

| DHT11 Pin | ESP32 Pin | Description |
| :--- | :--- | :--- |
| **VCC (+)** | **VIN (5V)** | 5V stable power input |
| **GND (-)** | **GND** | Common Ground |
| **DATA (Out)** | **GPIO 15** | Digital telemetry data line |

---

### Node 2: Actuator Node (ESP32 + Dual Relays)
> ⚠️ **Critical Strapping Pin Note:** Do **NOT** connect relays to **GPIO 12**. On the ESP32, GPIO 12 controls flash voltage strapping (1.8V vs 3.3V) at boot. Connecting an optocoupler input to GPIO 12 pulls it HIGH, permanently halting boot and bricking code flashing (`exit status 2`). Use **GPIO 13** and **GPIO 14**.

| Relay Module Pin | ESP32 Pin | Description |
| :--- | :--- | :--- |
| **VCC** | **VIN (5V)** | Relay coil power |
| **GND** | **GND** | Common ground with ESP32 |
| **IN1 (Relay 1 - Fan)** | **GPIO 13** | Active-LOW control line |
| **IN2 (Relay 2 - Aux)** | **GPIO 14** | Active-LOW control line |

---

## 💻 3. Setting Up the Central Hub (Raspberry Pi 5)

### Step 3.1: Install Operating System
1. Flash **Raspberry Pi OS (64-bit, Debian Bookworm)** using the official Raspberry Pi Imager.
2. In the OS customization settings:
   - Set hostname: `raspberrypi` (or custom)
   - Set username & password (e.g. `project` / `project`)
   - Configure local Wi-Fi credentials.
   - Enable **SSH**.

---

### Step 3.2: Install System Dependencies
Connect to the Pi via SSH:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl uvicorn python3-pip python3-venv mosquitto mosquitto-clients
```

---

### Step 3.3: Configure Mosquitto MQTT Broker
Create an authenticated MQTT configuration:
```bash
# 1. Generate password file for user 'asense'
sudo mosquitto_passwd -c /etc/mosquitto/passwd asense
# (Enter your password, e.g. 'project')

# 2. Configure listener
sudo bash -c 'cat <<EOF > /etc/mosquitto/conf.d/asense.conf
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
EOF'

# 3. Enable and restart Mosquitto
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
```

---

### Step 3.4: Install Ollama & Pull Local LLM
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the lightweight, high-reasoning model (~1.1 GB)
ollama pull qwen2.5:1.5b

# Verify it runs (exit with /bye):
ollama run qwen2.5:1.5b
```

---

### Step 3.5: Deploy A-SENSE Backend
```bash
# Clone the repository
cd ~
git clone https://github.com/Vishal-Autade185/A-SENSE.git asense-backend
cd asense-backend/backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python requirements
pip install fastapi uvicorn requests paho-mqtt python-dotenv

# Create .env file
cp .env.example .env
nano .env
```
Ensure `.env` matches your environment:
```ini
MQTT_BROKER=127.0.0.1
MQTT_PORT=1883
MQTT_USER=asense
MQTT_PASSWORD=project
ASENSE_DB_PATH=asense.db
OLLAMA_MODEL=qwen2.5:1.5b
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here   # Optional
TELEGRAM_CHAT_ID=your_telegram_chat_id_here       # Optional
```

Initialize the database schema:
```bash
python3 database.py
```

---

### Step 3.6: Configure Auto-Start on Boot (Systemd)
To ensure the backend runs 24/7 and restarts automatically on reboot:
```bash
sudo cp ~/asense-backend/asense.service /etc/systemd/system/asense.service
sudo systemctl daemon-reload
sudo systemctl enable asense.service
sudo systemctl start asense.service

# Check status:
sudo systemctl status asense.service
```

---

## ⚡ 4. Flashing the ESP32 Microcontroller Nodes

### Required Libraries in Arduino IDE / PlatformIO:
- `PubSubClient` by Nick O'Leary
- `ArduinoJson` by Benoit Blanchon (v6.x)
- `DHT sensor library` by Adafruit
- `WiFiManager` by tzapu

---

### Step 4.1: Flashing Sensor Node
1. Open [`firmware/sensor_node/sensor_node.ino`](file:///e:/mohite%20project/A-SENSE/firmware/sensor_node/sensor_node.ino) in Arduino IDE.
2. In the configuration section, update:
   ```cpp
   const char* MQTT_BROKER   = "192.168.1.18"; // Your Raspberry Pi IP address
   const char* MQTT_USER     = "asense";
   const char* MQTT_PASSWORD = "project";
   ```
3. Connect Node 1 via USB, select Board: `ESP32 Dev Module`, and hit **Upload**.

---

### Step 4.2: Flashing Relay Node
1. Open [`firmware/relay_node/relay_node.ino`](file:///e:/mohite%20project/A-SENSE/firmware/relay_node/relay_node.ino).
2. Set your Raspberry Pi IP:
   ```cpp
   const char* MQTT_BROKER   = "192.168.1.18"; // Your Raspberry Pi IP address
   const char* MQTT_USER     = "asense";
   const char* MQTT_PASSWORD = "project";
   ```
3. Connect Node 2 via USB and hit **Upload**.

---

### Step 4.3: Connecting ESP32s to Your Wi-Fi (WiFiManager)
1. Once powered, if the ESP32 cannot connect to a saved network, it broadcasts an open AP:
   - For Sensor: `A-SENSE-Sensor-Setup`
   - For Relay: `A-SENSE-Relay-Setup`
2. Connect to the AP on your smartphone or PC.
3. A portal page opens automatically at `http://192.168.4.1`.
4. Click **Configure WiFi**, select your home network, enter your Wi-Fi password, and press **Save**.
5. The ESP32 reboots and connects immediately to the network!

---

## 🌐 5. Verification & Testing

### 1. Access Web Dashboard
Open your web browser on any device on your local Wi-Fi:
```text
http://<YOUR_PI_IP>:8000
```
- Verify real-time temperature and humidity updating every 2–10 seconds.
- Toggle Relay 1 and Relay 2 manually to hear the physical clicking of the relay coils.

### 2. Verify Natural-Language Chat
Try these prompts in the chat box or on Telegram:
- *"What is the room temperature?"*
- *"Turn on the fan"*
- *"Turn off the fan at 5:00 AM"*
- *"What is the weather in Pune today?"*
- *"Remind me to buy groceries in 10 minutes"*
- *"Enable auto mode"*

---

## 🛡️ 6. Troubleshooting Checklist

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **DHT11 returns `NaN`** | 3.3V Wi-Fi brownout | Connect DHT11 VCC to **5V (VIN)** instead of 3.3V. |
| **ESP32 won't flash (`exit status 2`)** | Strapping pin GPIO 12 pulled HIGH | Move relay input from GPIO 12 to **GPIO 14**. |
| **MQTT Connection Refused (rc=-2)** | Mosquitto config blocking external IPs | Add `listener 1883 0.0.0.0` in `/etc/mosquitto/conf.d/asense.conf`. |
| **Model returns infinite reasoning loops** | Small model tool synthesis glitch | Use two-pass synthesis with `tools=[]` on the summary phase (already implemented in `agent.py`). |
| **System fails to boot backend** | Virtual environment path discrepancy | Verify path in `/etc/systemd/system/asense.service` matches your username. |
