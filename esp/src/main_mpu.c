#include <Wire.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>

#define IMU_ADDR 0x68
#define REG_WHO_AM_I     0x75
#define REG_PWR_MGMT_1   0x6B
#define REG_ACCEL_CONFIG 0x1C
#define REG_GYRO_CONFIG  0x1B
#define REG_CONFIG       0x1A
#define REG_ACCEL_XOUT_H 0x3B

// ===================== Wi-Fi =====================
const char* WIFI_SSID = "ESP_TEST";
const char* WIFI_PASS = "55555444";

// ===================== Backend =====================
const char* SERVER_URL = "http://222.167.211.89:8000/ingest/windows";
const char* DEVICE_SERIAL = "car_01";

// ===================== GPS =====================
TinyGPSPlus gps;
HardwareSerial GPSserial(2);
static const int GPS_RX = 16;   // ESP32 RX2 <- TX GPS
static const int GPS_TX = 17;   // можно не подключать физически

// ===================== IMU =====================
const float ACC_LSB_PER_G = 16384.0f;     // ±2g
const float GYRO_LSB_PER_DPS = 131.0f;    // ±250 dps
const float G_TO_MS2 = 9.80665f;

const float ACC_OFFSET_X = 0.100338f;
const float ACC_OFFSET_Y = -0.020033f;
const float ACC_OFFSET_Z = -0.053435f;

const float GYRO_OFFSET_X = 0.171639f;
const float GYRO_OFFSET_Y = -0.561817f;
const float GYRO_OFFSET_Z = -0.711623f;

const float ACC_SCALE_FACTOR = 1.000000f;

// ===================== Timing =====================
const unsigned long SAMPLE_INTERVAL_MS = 50;   // 20 Гц
const unsigned long WINDOW_MS = 2000;          // 2 секунды
const unsigned long WIFI_RETRY_MS = 5000;

unsigned long lastSampleMs = 0;
unsigned long windowStartMs = 0;
unsigned long lastWifiRetryMs = 0;

// ===================== Window buffers =====================
const int MAX_SAMPLES = 64;
float accDynBuf[MAX_SAMPLES];
float jerkBuf[MAX_SAMPLES];

int sampleCount = 0;
int peakCount = 0;
float prevAccDyn = 0.0f;
bool hasPrevAccDyn = false;

// ===================== Current live telemetry =====================
bool imuOk = false;
float ax_ms2 = 0.0f, ay_ms2 = 0.0f, az_ms2 = 0.0f;
float gx_dps = 0.0f, gy_dps = 0.0f, gz_dps = 0.0f;
float accMag = 0.0f, accDyn = 0.0f;

double curLat = 0.0;
double curLng = 0.0;
int curSats = 0;
double curSpeedKmph = 0.0;
double curHdop = 99.9;
bool gpsValid = false;

// ===================== Window timestamps =====================
String windowGpsStartIso = "";
bool windowHasValidStartTime = false;

// ===================== Trip =====================
int currentTripId = -1;

// ============================================================
// Helpers
// ============================================================

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.print("Connecting to Wi-Fi: ");
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
    Serial.print("Wi-Fi connected. IP: ");
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

void writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

bool readRegisters(uint8_t startReg, uint8_t count, uint8_t *dest) {
  Wire.beginTransmission(IMU_ADDR);
  Wire.write(startReg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received = Wire.requestFrom(IMU_ADDR, count);
  if (received != count) {
    while (Wire.available()) Wire.read();
    return false;
  }

  for (uint8_t i = 0; i < count; i++) {
    if (!Wire.available()) return false;
    dest[i] = Wire.read();
  }

  return true;
}

int16_t makeInt16(uint8_t highByte, uint8_t lowByte) {
  return (int16_t)((highByte << 8) | lowByte);
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
  if (!gps.date.isValid() || !gps.time.isValid()) {
    return "";
  }

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

void resetWindow() {
  sampleCount = 0;
  peakCount = 0;
  hasPrevAccDyn = false;
  windowGpsStartIso = "";
  windowHasValidStartTime = false;
  windowStartMs = millis();
}

void captureWindowStartTimeIfNeeded() {
  if (!windowHasValidStartTime && gps.date.isValid() && gps.time.isValid()) {
    windowGpsStartIso = gpsIsoNow();
    if (windowGpsStartIso.length() > 0) {
      windowHasValidStartTime = true;
    }
  }
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

  if (currentTripId > 0) {
    doc["trip_id"] = currentTripId;
  } else {
    doc["trip_id"] = nullptr;
  }

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

// ============================================================
// Setup
// ============================================================

void setup() {
  Serial.begin(115200);
  delay(2000);

  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(20);
  delay(100);

  uint8_t whoami = 0;
  if (readRegisters(REG_WHO_AM_I, 1, &whoami)) {
    Serial.print("IMU WHO_AM_I = 0x");
    Serial.println(whoami, HEX);
  } else {
    Serial.println("IMU not responding");
  }

  writeRegister(REG_PWR_MGMT_1, 0x00);
  delay(100);

  writeRegister(REG_GYRO_CONFIG, 0x00);   // ±250 dps
  delay(10);

  writeRegister(REG_ACCEL_CONFIG, 0x00);  // ±2g
  delay(10);

  writeRegister(REG_CONFIG, 0x03);        // DLPF
  delay(10);

  GPSserial.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);

  connectWiFi();
  resetWindow();

  Serial.println("Device started");
}

// ============================================================
// Main loop
// ============================================================

void loop() {
  ensureWiFi();

  while (GPSserial.available()) {
    gps.encode(GPSserial.read());
  }

  bool gpsPosValid   = gps.location.isValid();
  bool gpsTimeValid  = gps.date.isValid() && gps.time.isValid();
  bool gpsSpeedValid = gps.speed.isValid();
  bool gpsHdopValid  = gps.hdop.isValid();
  bool gpsSatsValid  = gps.satellites.isValid();

  curLat = gpsPosValid ? gps.location.lat() : 0.0;
  curLng = gpsPosValid ? gps.location.lng() : 0.0;
  curSats = gpsSatsValid ? gps.satellites.value() : 0;
  curHdop = gpsHdopValid ? gps.hdop.hdop() : 99.9;

  gpsValid = gpsPosValid &&
             gpsTimeValid &&
             gpsHdopValid &&
             gpsSatsValid &&
             curSats >= 4 &&
             curHdop <= 3.0;

  if (gpsValid && gpsSpeedValid) {
    curSpeedKmph = gps.speed.kmph();

    // защита от совсем мусорной скорости при плохом фиксе
    if (curSpeedKmph < 0.0 || curSpeedKmph > 250.0) {
      curSpeedKmph = 0.0;
    }
  } else {
    curSpeedKmph = 0.0;
  }

  captureWindowStartTimeIfNeeded();

  if (millis() - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = millis();

    uint8_t rawData[14];
    imuOk = false;

    if (readRegisters(REG_ACCEL_XOUT_H, 14, rawData)) {
      int16_t ax_raw = makeInt16(rawData[0], rawData[1]);
      int16_t ay_raw = makeInt16(rawData[2], rawData[3]);
      int16_t az_raw = makeInt16(rawData[4], rawData[5]);

      int16_t gx_raw = makeInt16(rawData[8], rawData[9]);
      int16_t gy_raw = makeInt16(rawData[10], rawData[11]);
      int16_t gz_raw = makeInt16(rawData[12], rawData[13]);

      float ax_g = ax_raw / ACC_LSB_PER_G;
      float ay_g = ay_raw / ACC_LSB_PER_G;
      float az_g = az_raw / ACC_LSB_PER_G;

      float gx_raw_dps = gx_raw / GYRO_LSB_PER_DPS;
      float gy_raw_dps = gy_raw / GYRO_LSB_PER_DPS;
      float gz_raw_dps = gz_raw / GYRO_LSB_PER_DPS;

      // Калибровка accel
      ax_g -= ACC_OFFSET_X;
      ay_g -= ACC_OFFSET_Y;
      az_g -= ACC_OFFSET_Z;

      ax_g *= ACC_SCALE_FACTOR;
      ay_g *= ACC_SCALE_FACTOR;
      az_g *= ACC_SCALE_FACTOR;

      // Калибровка gyro
      gx_dps = gx_raw_dps - GYRO_OFFSET_X;
      gy_dps = gy_raw_dps - GYRO_OFFSET_Y;
      gz_dps = gz_raw_dps - GYRO_OFFSET_Z;

      ax_ms2 = ax_g * G_TO_MS2;
      ay_ms2 = ay_g * G_TO_MS2;
      az_ms2 = az_g * G_TO_MS2;

      accMag = sqrt(ax_ms2 * ax_ms2 + ay_ms2 * ay_ms2 + az_ms2 * az_ms2);
      accDyn = accMag - G_TO_MS2;

      if (sampleCount < MAX_SAMPLES) {
        accDynBuf[sampleCount] = accDyn;

        float jerk = 0.0f;
        if (hasPrevAccDyn) {
          jerk = (accDyn - prevAccDyn) / (SAMPLE_INTERVAL_MS / 1000.0f);
        }
        jerkBuf[sampleCount] = jerk;

        if (fabs(accDyn) > 2.0f) {
          peakCount++;
        }

        prevAccDyn = accDyn;
        hasPrevAccDyn = true;
        sampleCount++;
      }

      imuOk = true;
    }

    if (imuOk) {
      Serial.print("IMU accDyn=");
      Serial.print(accDyn, 3);
      Serial.print(" speed=");
      Serial.print(curSpeedKmph, 2);
      Serial.print(" sats=");
      Serial.println(curSats);
    } else {
      Serial.println("IMU read failed");
    }
  }

  if (millis() - windowStartMs >= WINDOW_MS) {
    String tEnd = gpsIsoNow();

    float a_rms = calcRMS(accDynBuf, sampleCount);
    float a_p95 = calcPercentile95Abs(accDynBuf, sampleCount);
    float a_max = calcMaxAbs(accDynBuf, sampleCount);
    float jerk_p95 = calcPercentile95Abs(jerkBuf, sampleCount);

    double speedMps = curSpeedKmph / 3.6;

    Serial.print("Window done. samples=");
    Serial.print(sampleCount);
    Serial.print(" gpsValid=");
    Serial.print(gpsValid ? 1 : 0);
    Serial.print(" tStartValid=");
    Serial.println(windowHasValidStartTime ? 1 : 0);

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

      if (ok) {
        Serial.println("Window sent");
      } else {
        Serial.println("Window send failed");
      }
    } else {
      Serial.println("Window skipped: invalid GPS time or no samples");
    }

    resetWindow();
  }
}