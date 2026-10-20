// ==============================================================================
// A-SENSE Sensor Node Firmware (DHT11 on ESP32)
// Follows the MQTT contract defined in product.md:
//   - Telemetry topic: asense/v1/sensors/room1/telemetry
//   - Availability/LWT topic: asense/v1/devices/sensor-01/status
// ==============================================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>

// ------------------------------------------------------------------------------
// 1. CONFIGURATION (Edit with your Wi-Fi & MQTT credentials)
// ------------------------------------------------------------------------------
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char* MQTT_BROKER   = "192.168.1.18"; // Raspberry Pi IP
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "asense";
const char* MQTT_PASSWORD = "YOUR_MQTT_PASSWORD";

// Hardware Settings
#define DHT_PIN  15
#define DHT_TYPE DHT11

// Device Identifiers & Topics (from product.md)
const char* DEVICE_ID       = "sensor-01";
const char* TOPIC_TELEMETRY = "asense/v1/sensors/room1/telemetry";
const char* TOPIC_STATUS    = "asense/v1/devices/sensor-01/status";

// Read Interval (in milliseconds)
// 10,000 ms = 10 seconds (conservative, well above DHT11's 2-second minimum)
const unsigned long READ_INTERVAL_MS = 10000;

// ------------------------------------------------------------------------------
// 2. GLOBALS
// ------------------------------------------------------------------------------
WiFiClient espClient;
PubSubClient mqttClient(espClient);
DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastReadTime = 0;
uint32_t sequenceNumber = 0;

// ------------------------------------------------------------------------------
// 3. WI-FI & MQTT FUNCTIONS
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
    Serial.print("[MQTT] Attempting connection to broker...");

    // Last Will and Testament (LWT):
    // If the ESP32 disconnects unexpectedly, the broker publishes "offline"
    // with QoS 1 and retain = true to the status topic.
    const char* willTopic = TOPIC_STATUS;
    int willQoS = 1;
    bool willRetain = true;
    const char* willMessage = "offline";

    if (mqttClient.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD, willTopic, willQoS, willRetain, willMessage)) {
      Serial.println(" connected!");

      // Publish initial "online" status with retain = true
      mqttClient.publish(TOPIC_STATUS, "online", true);
      Serial.println("[MQTT] Published status: online");
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" -> Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

// ------------------------------------------------------------------------------
// 4. MAIN SETUP & LOOP
// ------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=================================");
  Serial.println("A-SENSE Sensor Node Initializing");
  Serial.println("=================================");

  // Initialize DHT sensor
  dht.begin();
  Serial.println("[Hardware] DHT11 initialized on GPIO 15");

  // Setup Wi-Fi and MQTT
  setupWiFi();
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
}

void loop() {
  // Ensure Wi-Fi is connected
  if (WiFi.status() != WL_CONNECTED) {
    setupWiFi();
  }

  // Ensure MQTT is connected
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop();

  // Non-blocking periodic sensor reading
  unsigned long currentMillis = millis();
  if (currentMillis - lastReadTime >= READ_INTERVAL_MS) {
    lastReadTime = currentMillis;

    // Read values from DHT11
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius

    // Validate readings (reject NaN / non-finite values)
    if (isnan(humidity) || isnan(temperature)) {
      Serial.println("[DHT11 ERROR] Failed to read from sensor! Check wiring/power.");
      return;
    }

    sequenceNumber++;

    // Build JSON payload conforming to product.md contract
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["temperature_c"] = serialized(String(temperature, 1)); // 1 decimal place
    doc["humidity_pct"] = serialized(String(humidity, 1));
    doc["uptime_ms"] = currentMillis;
    doc["sequence"] = sequenceNumber;

    char jsonBuffer[256];
    serializeJson(doc, jsonBuffer);

    // Publish telemetry (QoS 0 for periodic readings)
    bool success = mqttClient.publish(TOPIC_TELEMETRY, jsonBuffer, false);

    if (success) {
      Serial.print("[Telemetry Sent] ");
      Serial.println(jsonBuffer);
    } else {
      Serial.println("[Telemetry Error] Failed to publish MQTT message.");
    }
  }
}
