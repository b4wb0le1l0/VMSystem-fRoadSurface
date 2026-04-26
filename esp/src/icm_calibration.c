#include <Wire.h>
#include <ICM_20948.h>
#include <math.h>

ICM_20948_I2C icm;

const int N = 500;

float sumAx = 0, sumAy = 0, sumAz = 0;
float sumGx = 0, sumGy = 0, sumGz = 0;
float sumMag = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);
  Wire.setClock(100000);

  bool initialized = false;
  for (int i = 0; i < 5; i++) {
    icm.begin(Wire, 0);   // AD0 = GND -> 0x68
    if (icm.status == ICM_20948_Stat_Ok) {
      initialized = true;
      break;
    }
    delay(500);
  }

  if (!initialized) {
    Serial.print("ICM-20948 init failed, status: ");
    Serial.println(icm.statusString());
    while (1) delay(100);
  }

  Serial.println("ICM-20948 OK");
  delay(3000);

  int collected = 0;
  while (collected < N) {
    if (icm.dataReady()) {
      icm.getAGMT();

      float ax = icm.accX() / 1000.0f;
      float ay = icm.accY() / 1000.0f;
      float az = icm.accZ() / 1000.0f;

      float gx = icm.gyrX();
      float gy = icm.gyrY();
      float gz = icm.gyrZ();

      float mag = sqrt(ax * ax + ay * ay + az * az);

      sumAx += ax;
      sumAy += ay;
      sumAz += az;

      sumGx += gx;
      sumGy += gy;
      sumGz += gz;

      sumMag += mag;

      collected++;
      if (collected % 50 == 0) {
        Serial.print("Collected: ");
        Serial.println(collected);
      }
    }
    delay(10);
  }

  float meanAx = sumAx / N;
  float meanAy = sumAy / N;
  float meanAz = sumAz / N;

  float meanGx = sumGx / N;
  float meanGy = sumGy / N;
  float meanGz = sumGz / N;

  float meanMag = sumMag / N;

  float accOffsetX = meanAx;
  float accOffsetY = meanAy;
  float accOffsetZ = meanAz - 1.0f;

  float gyroOffsetX = meanGx;
  float gyroOffsetY = meanGy;
  float gyroOffsetZ = meanGz;

  float scaleFactor = 1.0f;
  if (meanMag > 0.0001f) {
    scaleFactor = 1.0f / meanMag;
  }

  Serial.println();
  Serial.println("===== CALIBRATION RESULT =====");
  Serial.print("Mean ax [g]: "); Serial.println(meanAx, 6);
  Serial.print("Mean ay [g]: "); Serial.println(meanAy, 6);
  Serial.print("Mean az [g]: "); Serial.println(meanAz, 6);
  Serial.print("Mean |a| [g]: "); Serial.println(meanMag, 6);
  Serial.println();

  Serial.print("Mean gx [dps]: "); Serial.println(meanGx, 6);
  Serial.print("Mean gy [dps]: "); Serial.println(meanGy, 6);
  Serial.print("Mean gz [dps]: "); Serial.println(meanGz, 6);
  Serial.println();

  Serial.println("Suggested offsets:");
  Serial.print("ACC_OFFSET_X = "); Serial.println(accOffsetX, 6);
  Serial.print("ACC_OFFSET_Y = "); Serial.println(accOffsetY, 6);
  Serial.print("ACC_OFFSET_Z = "); Serial.println(accOffsetZ, 6);
  Serial.print("GYRO_OFFSET_X = "); Serial.println(gyroOffsetX, 6);
  Serial.print("GYRO_OFFSET_Y = "); Serial.println(gyroOffsetY, 6);
  Serial.print("GYRO_OFFSET_Z = "); Serial.println(gyroOffsetZ, 6);
  Serial.println();

  Serial.print("Suggested ACC_SCALE = "); Serial.println(scaleFactor, 6);
  Serial.println("==============================");
}

void loop() {
}