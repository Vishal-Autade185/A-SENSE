// ==============================================================================
// A-SENSE Relay Node Firmware (Dual Relay ESP32)
// Follows the MQTT contract defined in product.md:
//   - Device Status / LWT: asense/v1/devices/relay-node/status
//
//   Relay 1 (Pin 13 - primary "relay-01"):
//     - Command: asense/v1/actuators/relay-01/set
//     - State:   asense/v1/actuators/relay-01/state
//     - Ack:     asense/v1/events/relay-01/ack
//
//   Relay 2 (Pin 12 - auxiliary "relay-02"):
//     - Command: asense/v1/actuators/relay-02/set
//     - State:   asense/v1/actuators/relay-02/state
//     - Ack:     asense/v1/events/relay-02/ack
// ==============================================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ------------------------------------------------------------------------------
// 1. CONFIGURATION
// ------------------------------------------------------------------------------
const char* WIFI_SSID     = "omkar_5g";
const char* WIFI_PASSWORD = "Rutejpark403";

const char* MQTT_BROKER   = "192.168.1.18"; // Raspberry Pi IP
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "asense";
const char* MQTT_PASSWORD = "project";

const char* DEVICE_ID     = "relay-node";
const char* TOPIC_STATUS  = "asense/v1/devices/relay-node/status";

// Hardware Pins
#define PIN_RELAY_1 13
#define PIN_RELAY_2 14 // Changed from 12 to 14 because GPIO 12 is an ESP32 strapping pin for flash voltage

// Relay Polarity:
// Most 5V optocoupler relay boards for Arduino/ESP32 are ACTIVE-LOW (LOW = ON, HIGH = OFF).
// If your board turns ON when HIGH, set this to false.
const bool RELAY_ACTIVE_LOW = true;

// Helper macros for pin states
#define RELAY_LEVEL_ON  (RELAY_ACTIVE_LOW ? LOW : HIGH)
#define RELAY_LEVEL_OFF (RELAY_ACTIVE_LOW ? HIGH : LOW)

// ------------------------------------------------------------------------------
// 2. STATE TRACKING
// ------------------------------------------------------------------------------
struct RelayControl {
  const char* id;
  uint8_t pin;
  bool is_on;
  const char* topic_set;
  const char* topic_state;
  const char* topic_ack;
};

RelayControl relays[2] = {
  { "relay-01", PIN_RELAY_1, false, "asense/v1/actuators/relay-01/set", "asense/v1/actuators/relay-01/state", "asense/v1/events/relay-01/ack" },
  { "relay-02", PIN_RELAY_2, false, "asense/v1/actuators/relay-02/set", "asense/v1/actuators/relay-02/state", "asense/v1/events/relay-02/ack" }
};

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ------------------------------------------------------------------------------
// 3. PUBLISHING HELPERS
// ------------------------------------------------------------------------------
void publishRelayState(RelayControl& r, const char* commandId = nullptr) {
  StaticJsonDocument<192> doc;
  if (commandId && strlen(commandId) > 0) {
    doc["command_id"] = commandId;
  }
  doc["state"] = r.is_on ? "ON" : "OFF";
  doc["reported_at"] = millis();

  char buffer[192];
  serializeJson(doc, buffer);
  // Retain = true so new clients know current physical relay state immediately
  mqttClient.publish(r.topic_state, buffer, true);
  Serial.printf("[%s] State published: %s\n", r.id, buffer);
}

void publishRelayAck(RelayControl& r, const char* commandId, const char* result, const char* errorMsg = nullptr) {
  StaticJsonDocument<256> doc;
  doc["command_id"] = (commandId && strlen(commandId) > 0) ? commandId : "unknown";
  doc["result"] = result; // "accepted" or "rejected"
  doc["state"] = r.is_on ? "ON" : "OFF";
  if (errorMsg) {
    doc["error"] = errorMsg;
  }

  char buffer[256];
  serializeJson(doc, buffer);
  mqttClient.publish(r.topic_ack, buffer, false);
  Serial.printf("[%s] Ack published: %s\n", r.id, buffer);
}

// ------------------------------------------------------------------------------
// 4. COMMAND HANDLER
// ------------------------------------------------------------------------------
void handleCommand(RelayControl& r, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, payload, length);

  if (err) {
    Serial.printf("[%s] JSON parse failed: %s\n", r.id, err.c_str());
    publishRelayAck(r, "", "rejected", "Invalid JSON payload");
    return;
  }

  const char* cmdId = doc["command_id"] | "";
  const char* requestedState = doc["state"] | "";

  Serial.printf("[%s] Received cmd_id: '%s', requested state: '%s'\n", r.id, cmdId, requestedState);

  // Validate command
  if (strcmp(requestedState, "ON") == 0) {
    r.is_on = true;
    digitalWrite(r.pin, RELAY_LEVEL_ON);
    publishRelayAck(r, cmdId, "accepted");
    publishRelayState(r, cmdId);
  } else if (strcmp(requestedState, "OFF") == 0) {
    r.is_on = false;
    digitalWrite(r.pin, RELAY_LEVEL_OFF);
    publishRelayAck(r, cmdId, "accepted");
    publishRelayState(r, cmdId);
  } else {
    Serial.printf("[%s] Rejected invalid state value: %s\n", r.id, requestedState);
    publishRelayAck(r, cmdId, "rejected", "Invalid state. Must be 'ON' or 'OFF'");
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  for (int i = 0; i < 2; i++) {
    if (strcmp(topic, relays[i].topic_set) == 0) {
      handleCommand(relays[i], payload, length);
      return;
    }
  }
}

// ------------------------------------------------------------------------------
// 5. WI-FI & MQTT LIFECYCLE
// ------------------------------------------------------------------------------
void setupWiFi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to Wi-Fi SSID: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n[Wi-Fi] Connected!");
  Serial.print("[Wi-Fi] IP Address: ");
  Serial.println(WiFi.localIP());
}

void reconnectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] Connecting relay node to broker...");

    // Last Will: announce offline if disconnected unexpectedly
    const char* willTopic = TOPIC_STATUS;
    int willQoS = 1;
    bool willRetain = true;
    const char* willMessage = "offline";

    if (mqttClient.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD, willTopic, willQoS, willRetain, willMessage)) {
      Serial.println(" connected!");

      // 1. Announce online status
      mqttClient.publish(TOPIC_STATUS, "online", true);

      // 2. Subscribe to command topics for both relays
      for (int i = 0; i < 2; i++) {
        mqttClient.subscribe(relays[i].topic_set, 1);
        Serial.printf("[MQTT] Subscribed to %s\n", relays[i].topic_set);
        // Publish current confirmed boot state (OFF)
        publishRelayState(relays[i]);
      }
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" -> Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

// ------------------------------------------------------------------------------
// 6. SETUP & MAIN LOOP
// ------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=================================");
  Serial.println("A-SENSE Relay Node Initializing");
  Serial.println("=================================");

  // Enforce SAFE BOOT: Set output levels OFF BEFORE configuring pinMode
  for (int i = 0; i < 2; i++) {
    digitalWrite(relays[i].pin, RELAY_LEVEL_OFF);
    pinMode(relays[i].pin, OUTPUT);
    digitalWrite(relays[i].pin, RELAY_LEVEL_OFF);
    relays[i].is_on = false;
    Serial.printf("[Hardware] Initialized %s on GPIO %d -> Default OFF\n", relays[i].id, relays[i].pin);
  }

  setupWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    setupWiFi();
  }

  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop();
}
