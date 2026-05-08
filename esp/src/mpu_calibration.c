#include <Wire.h>
#include <math.h>

static const int SDA_PIN = 21;
static const int SCL_PIN = 22;

uint8_t imuAddr = 0;

bool writeReg(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;

  size_t got = Wire.requestFrom((int)addr, (int)len, (int)true);
  if (got != len) return false;

  for (size_t i = 0; i < len; i++) {
    buf[i] = Wire.read();
  }
  return true;
}

bool readReg(uint8_t addr, uint8_t reg, uint8_t &val) {
  return readRegs(addr, reg, &val, 1);
}

bool probeAddr(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

bool findMPU() {
  const uint8_t addrs[] = {0x68, 0x69};

  for (uint8_t addr : addrs) {
    if (!probeAddr(addr)) continue;

    uint8_t who = 0;
    if (!readReg(addr, 0x75, who)) continue;

    Serial.print("Found device at 0x");
    Serial.print(addr, HEX);
    Serial.print(", WHO_AM_I = 0x");
    Serial.println(who, HEX);

    imuAddr = addr;
    return true;
  }

  return false;
}

bool initMPU() {
  if (imuAddr == 0) return false;

  if (!writeReg(imuAddr, 0x6B, 0x00)) return false; // wake up
  delay(100);

  if (!writeReg(imuAddr, 0x1B, 0x00)) return false; // gyro ±250 dps
  if (!writeReg(imuAddr, 0x1C, 0x00)) return false; // accel ±2g
  if (!writeReg(imuAddr, 0x1A, 0x03)) return false; // DLPF

  return true;
}

bool readMPUraw(int16_t &ax, int16_t &ay, int16_t &az,
                int16_t &gx, int16_t &gy, int16_t &gz) {
  uint8_t buf[14];
  if (!readRegs(imuAddr, 0x3B, buf, 14)) return false;

  ax = (int16_t)((buf[0] << 8) | buf[1]);
  ay = (int16_t)((buf[2] << 8) | buf[3]);
  az = (int16_t)((buf[4] << 8) | buf[5]);

  gx = (int16_t)((buf[8]  << 8) | buf[9]);
  gy = (int16_t)((buf[10] << 8) | buf[11]);
  gz = (int16_t)((buf[12] << 8) | buf[13]);

  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  Serial.println("=== MPU CALIBRATION ===");
  Serial.println("Put the sensor still in FINAL working position.");
  delay(3000);

  if (!findMPU()) {
    Serial.println("MPU not found");
    while (true) delay(1000);
  }

  if (!initMPU()) {
    Serial.println("MPU init failed");
    while (true) delay(1000);
  }

  const int N = 1500;
  long long sumAx = 0, sumAy = 0, sumAz = 0;
  long long sumGx = 0, sumGy = 0, sumGz = 0;

  int okCount = 0;

  for (int i = 0; i < N; i++) {
    int16_t ax, ay, az, gx, gy, gz;
    if (readMPUraw(ax, ay, az, gx, gy, gz)) {
      sumAx += ax;
      sumAy += ay;
      sumAz += az;
      sumGx += gx;
      sumGy += gy;
      sumGz += gz;
      okCount++;
    }
    delay(5);
  }

  if (okCount == 0) {
    Serial.println("No valid samples");
    while (true) delay(1000);
  }

  float meanAx = (float)sumAx / okCount;
  float meanAy = (float)sumAy / okCount;
  float meanAz = (float)sumAz / okCount;
  float meanGx = (float)sumGx / okCount;
  float meanGy = (float)sumGy / okCount;
  float meanGz = (float)sumGz / okCount;

  float ax_g = meanAx / 16384.0f;
  float ay_g = meanAy / 16384.0f;
  float az_g = meanAz / 16384.0f;

  float gx_dps = meanGx / 131.0f;
  float gy_dps = meanGy / 131.0f;
  float gz_dps = meanGz / 131.0f;

  float amag = sqrt(ax_g * ax_g + ay_g * ay_g + az_g * az_g);

  Serial.println();
  Serial.println("=== CALIBRATION RESULT ===");
  Serial.print("Mean ax [g]: "); Serial.println(ax_g, 6);
  Serial.print("Mean ay [g]: "); Serial.println(ay_g, 6);
  Serial.print("Mean az [g]: "); Serial.println(az_g, 6);

  Serial.println();
  Serial.print("Mean gx [dps]: "); Serial.println(gx_dps, 6);
  Serial.print("Mean gy [dps]: "); Serial.println(gy_dps, 6);
  Serial.print("Mean gz [dps]: "); Serial.println(gz_dps, 6);

  Serial.println();
  Serial.println("Suggested offsets for current orientation:");
  Serial.print("ACC_OFFSET_X = "); Serial.println(ax_g, 6);
  Serial.print("ACC_OFFSET_Y = "); Serial.println(ay_g, 6);

  Serial.print("ACC_OFFSET_Z = "); Serial.println(az_g + 1.0f, 6);

  Serial.print("GYRO_OFFSET_X = "); Serial.println(gx_dps, 6);
  Serial.print("GYRO_OFFSET_Y = "); Serial.println(gy_dps, 6);
  Serial.print("GYRO_OFFSET_Z = "); Serial.println(gz_dps, 6);

  Serial.print("Mean |a| [g]: "); Serial.println(amag, 6);
  Serial.print("Suggested ACC_SCALE = "); Serial.println(1.0f / amag, 6);
  Serial.println("==========================");
}

void loop() {
}