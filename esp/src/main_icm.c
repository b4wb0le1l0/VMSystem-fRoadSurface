#include <Wire.h>
#include <ICM_20948.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>

ICM_20948_I2C icm;

// ===== Wi-Fi =====
const char* WIFI_SSID = "ESP_TEST";
const char* WIFI_PASS = "55555444";

// ===== Backend =====
const char* SERVER_URL = "http://192.168.43.92:8000/ingest/windows";
const char* DEVICE_SERIAL = "car_01_icm";

// ===== GPS =====
TinyGPSPlus gps;
HardwareSerial GPSserial(2);
static const int GPS_RX = 16;
static const int GPS_TX = 17;

// ===== ICM calibration =====
const float ACC_OFFSET_X = -0.134725f;
const float ACC_OFFSET_Y = -0.047205f;
const float ACC_OFFSET_Z = -0.096021f;

const float GYRO_OFFSET_X = -0.257328f;
const float GYRO_OFFSET_Y = -1.312046f;
const float GYRO_OFFSET_Z = -0.128443f;

const float ACC_SCALE_FACTOR = 1.0f;

// ===== Timing =====
const unsigned long SAMPLE_INTERVAL_MS = 50;
const unsigned long WINDOW_MS = 2000;
const unsigned long WIFI_RETRY_MS = 5000;

// ===== Window buffers =====
const int MAX_SAMPLES = 64;
float accDynBuf[MAX_SAMPLES];
float jerkBuf[MAX_SAMPLES];

int sampleCount = 0;
int peakCount = 0;
float prevAccDyn = 0.0f;
bool hasPrevAccDyn = false;

// ===== Live telemetry =====
bool imuOk = false;
float ax_g = 0.0f, ay_g = 0.0f, az_g = 0.0f;
float gx_dps = 0.0f, gy_dps = 0.0f, gz_dps = 0.0f;
float accMag = 0.0f, accDyn = 0.0f;

double curLat = 0.0;
double curLng = 0.0;
int curSats = 0;
double curSpeedKmph = 0.0;
double curHdop = 99.9;
bool gpsValid = false;

// ===== Window timestamps =====
String windowGpsStartIso = "";
bool windowHasValidStartTime = false;

// ===== Trip =====
int currentTripId = -1;

// ===== Time markers =====
unsigned long lastSampleMs = 0;
unsigned long windowStartMs = 0;
unsigned long lastWifiRetryMs = 0;

// --------------------------------------------------
// Helpers
// --------------------------------------------------

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.print("Wi-Fi connect: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi OK, IP=");
    Serial.println(WiFi.localIP());
  } else {
    Serial.print("Wi-Fi failed, status=");
    Serial.println(WiFi.status());
  }
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiRetryMs < WIFI_RETRY_MS) return;
  lastWifiRetryMs = millis();
  connectWiFi();
}

float calcRMS(const float *arr, int n) {
  if (n <= 0) return 0.0f;
  float sumSq = 0.0f;
  for (int i = 0; i < n; i++) sumSq += arr[i] * arr[i];
  return sqrt(sumSq / n);
}

float calcMaxAbs(const float *arr, int n) {
  float m = 0.0f;
  for (int i = 0; i < n; i++) {
    float v = fabs(arr[i]);
    if (v > m) m = v;
  }
  return m;
}

void sortArray(float *arr, int n) {
  for (int i = 0; i < n - 1; i++) {
    for (int j = i + 1; j < n; j++) {
      if (arr[j] < arr[i]) {
        float t = arr[i];
        arr[i] = arr[j];
        arr[j] = t;
      }
    }
  }
}

float calcPercentile95Abs(const float *arr, int n) {
  if (n <= 0) return 0.0f;
  float tmp[MAX_SAMPLES];
  for (int i = 0; i < n; i++) tmp[i] = fabs(arr[i]);
  sortArray(tmp, n);
  int idx = (int)floor(0.95f * (n - 1));
  if (idx < 0) idx = 0;
  if (idx >= n) idx = n - 1;
  return tmp[idx];
}

String gpsIsoNow() {
  if (!gps.date.isValid() || !gps.time.isValid()) return "";

  char buf[25];
  snprintf(
    buf, sizeof(buf),
    "%04d-%02d-%02dT%02d:%02d:%02dZ",
    gps.date.year(),
    gps.date.month(),
    gps.date.day(),
    gps.time.hour(),
    gps.time.minute(),
    gps.time.second()
  );
  return String(buf);
}

void captureWindowStartTimeIfNeeded() {
  if (!windowHasValidStartTime && gps.date.isValid() && gps.time.isValid()) {
    windowGpsStartIso = gpsIsoNow();
    if (windowGpsStartIso.length() > 0) {
      windowHasValidStartTime = true;
    }
  }
}

void resetWindow() {
  sampleCount = 0;
  peakCount = 0;
  hasPrevAccDyn = false;
  windowGpsStartIso = "";
  windowHasValidStartTime = false;
  windowStartMs = millis();
}

bool sendWindow(
  const String& tStart,
  const String& tEnd,
  double lat,
  double lon,
  double speedMps,
  double hdop,
  int sats,
  float a_rms,
  float a_p95,
  float a_max,
  float jerk_p95,
  int peaks
) {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  StaticJsonDocument<768> doc;
  doc["device_serial"] = DEVICE_SERIAL;
  if (currentTripId > 0) doc["trip_id"] = currentTripId;
  else doc["trip_id"] = nullptr;

  JsonArray windows = doc.createNestedArray("windows");
  JsonObject w = windows.createNestedObject();

  w["t_start"] = tStart;
  w["t_end"] = tEnd;
  w["lat"] = lat;
  w["lon"] = lon;
  w["speed_mps"] = speedMps;
  w["hdop"] = hdop;
  w["sats"] = sats;
  w["a_rms"] = a_rms;
  w["a_p95"] = a_p95;
  w["a_max"] = a_max;
  w["jerk_p95"] = jerk_p95;
  w["peaks"] = peaks;
  w["e_0_5"] = 0.0;
  w["e_5_12"] = 0.0;
  w["e_12_30"] = 0.0;

  String body;
  serializeJson(doc, body);

  int code = http.POST(body);
  String resp = http.getString();
  http.end();

  Serial.print("POST code=");
  Serial.print(code);
  Serial.print(" response=");
  Serial.println(resp);

  if (code >= 200 && code < 300) {
    StaticJsonDocument<256> respDoc;
    DeserializationError err = deserializeJson(respDoc, resp);
    if (!err && respDoc["trip_id"].is<int>()) {
      currentTripId = respDoc["trip_id"].as<int>();
    }
    return true;
  }

  return false;
}

// --------------------------------------------------
// Setup
// --------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);
  Wire.setClock(100000);

  bool initialized = false;
  for (int i = 0; i < 5; i++) {
    icm.begin(Wire, 0);   // AD0 = GND
    if (icm.status == ICM_20948_Stat_Ok) {
      initialized = true;
      break;
    }
    delay(500);
  }

  if (!initialized) {
    Serial.print("ICM init failed: ");
    Serial.println(icm.statusString());
    while (1) delay(100);
  }

  Serial.println("ICM OK");

  GPSserial.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);

  connectWiFi();
  resetWindow();

  Serial.println("Device started");
}

// --------------------------------------------------
// Loop
// --------------------------------------------------

void loop() {
  ensureWiFi();

  while (GPSserial.available()) {
    gps.encode(GPSserial.read());
  }

  gpsValid = gps.location.isValid();
  curLat = gpsValid ? gps.location.lat() : 0.0;
  curLng = gpsValid ? gps.location.lng() : 0.0;
  curSats = gps.satellites.isValid() ? gps.satellites.value() : 0;
  curSpeedKmph = gps.speed.isValid() ? gps.speed.kmph() : 0.0;
  curHdop = gps.hdop.isValid() ? gps.hdop.hdop() : 99.9;

  captureWindowStartTimeIfNeeded();

  if (millis() - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = millis();

    // читаем без dataReady()
    icm.getAGMT();

    float ax_raw_g = icm.accX() / 1000.0f;
    float ay_raw_g = icm.accY() / 1000.0f;
    float az_raw_g = icm.accZ() / 1000.0f;

    float gx_raw = icm.gyrX();
    float gy_raw = icm.gyrY();
    float gz_raw = icm.gyrZ();

    ax_g = (ax_raw_g - ACC_OFFSET_X) * ACC_SCALE_FACTOR;
    ay_g = (ay_raw_g - ACC_OFFSET_Y) * ACC_SCALE_FACTOR;
    az_g = (az_raw_g - ACC_OFFSET_Z) * ACC_SCALE_FACTOR;

    gx_dps = gx_raw - GYRO_OFFSET_X;
    gy_dps = gy_raw - GYRO_OFFSET_Y;
    gz_dps = gz_raw - GYRO_OFFSET_Z;

    accMag = sqrt(ax_g * ax_g + ay_g * ay_g + az_g * az_g);
    accDyn = accMag - 1.0f;   // в g

    if (sampleCount < MAX_SAMPLES) {
      accDynBuf[sampleCount] = accDyn;

      float jerk = 0.0f;
      if (hasPrevAccDyn) {
        jerk = (accDyn - prevAccDyn) / (SAMPLE_INTERVAL_MS / 1000.0f);
      }
      jerkBuf[sampleCount] = jerk;

      if (fabs(accDyn) > 0.20f) peakCount++;

      prevAccDyn = accDyn;
      hasPrevAccDyn = true;
      sampleCount++;
    }

    imuOk = true;
  }

  if (millis() - windowStartMs >= WINDOW_MS) {
    String tEnd = gpsIsoNow();

    float a_rms = calcRMS(accDynBuf, sampleCount);
    float a_p95 = calcPercentile95Abs(accDynBuf, sampleCount);
    float a_max = calcMaxAbs(accDynBuf, sampleCount);
    float jerk_p95 = calcPercentile95Abs(jerkBuf, sampleCount);
    double speedMps = curSpeedKmph / 3.6;

    // краткий статус окна
    Serial.print("WIN ");
    Serial.print("samples=");
    Serial.print(sampleCount);
    Serial.print(" gps=");
    Serial.print(gpsValid ? 1 : 0);
    Serial.print(" sats=");
    Serial.print(curSats);
    Serial.print(" hdop=");
    Serial.print(curHdop, 1);
    Serial.print(" lat=");
    Serial.print(curLat, 6);
    Serial.print(" lon=");
    Serial.print(curLng, 6);
    Serial.print(" speed=");
    Serial.print(curSpeedKmph, 2);
    Serial.print(" a_rms=");
    Serial.print(a_rms, 4);
    Serial.print(" a_p95=");
    Serial.print(a_p95, 4);
    Serial.print(" a_max=");
    Serial.print(a_max, 4);
    Serial.print(" peaks=");
    Serial.println(peakCount);

    if (gpsValid && windowHasValidStartTime && tEnd.length() > 0 && sampleCount > 0) {
      bool ok = sendWindow(
        windowGpsStartIso,
        tEnd,
        curLat,
        curLng,
        speedMps,
        curHdop,
        curSats,
        a_rms,
        a_p95,
        a_max,
        jerk_p95,
        peakCount
      );

      Serial.println(ok ? "Window sent" : "Window send failed");
    } else {
      Serial.println("Window skipped: GPS not valid yet");
    }

    resetWindow();
  }
}