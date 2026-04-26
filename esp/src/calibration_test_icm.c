#include <Wire.h>
#include <ICM_20948.h>
#include <math.h>

ICM_20948_I2C icm;

const float ACC_OFFSET_X = -0.134725f;
const float ACC_OFFSET_Y = -0.047205f;
const float ACC_OFFSET_Z = -0.096021f;

const float GYRO_OFFSET_X = -0.257328f;
const float GYRO_OFFSET_Y = -1.312046f;
const float GYRO_OFFSET_Z = -0.128443f;

const float ACC_SCALE_FACTOR = 1.0000f;

const int WINDOW_SIZE = 100;
float magBuffer[WINDOW_SIZE];
int sampleIndex = 0;

void printStats(float *buf, int n) {
  float sum = 0.0f;
  float minV = buf[0];
  float maxV = buf[0];

  for (int i = 0; i < n; i++) {
    sum += buf[i];
    if (buf[i] < minV) minV = buf[i];
    if (buf[i] > maxV) maxV = buf[i];
  }

  float mean = sum / n;

  float var = 0.0f;
  for (int i = 0; i < n; i++) {
    float d = buf[i] - mean;
    var += d * d;
  }
  var /= n;
  float stddev = sqrt(var);

  Serial.println("----- CALIBRATED WINDOW STATS -----");
  Serial.print("Mean |a| [g]: ");
  Serial.println(mean, 5);
  Serial.print("Min  |a| [g]: ");
  Serial.println(minV, 5);
  Serial.print("Max  |a| [g]: ");
  Serial.println(maxV, 5);
  Serial.print("Std  |a| [g]: ");
  Serial.println(stddev, 5);
  Serial.print("Mean deviation from 1g: ");
  Serial.println(fabs(mean - 1.0f), 5);
  Serial.println("-----------------------------------");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(21, 22);
  Wire.setClock(100000);

  bool initialized = false;

  for (int i = 0; i < 5; i++) {
    icm.begin(Wire, 0);
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

  Serial.println("ICM-20948 calibrated test OK");
  Serial.println("Keep sensor still on a flat surface.");
}

void loop() {
  if (icm.dataReady()) {
    icm.getAGMT();

    // SparkFun accel -> milli-g, gyro -> dps
    float ax_raw_g = icm.accX() / 1000.0f;
    float ay_raw_g = icm.accY() / 1000.0f;
    float az_raw_g = icm.accZ() / 1000.0f;

    float gx_raw = icm.gyrX();
    float gy_raw = icm.gyrY();
    float gz_raw = icm.gyrZ();

    float ax = (ax_raw_g - ACC_OFFSET_X) * ACC_SCALE_FACTOR;
    float ay = (ay_raw_g - ACC_OFFSET_Y) * ACC_SCALE_FACTOR;
    float az = (az_raw_g - ACC_OFFSET_Z) * ACC_SCALE_FACTOR;

    float gx = gx_raw - GYRO_OFFSET_X;
    float gy = gy_raw - GYRO_OFFSET_Y;
    float gz = gz_raw - GYRO_OFFSET_Z;

    float accMag = sqrt(ax * ax + ay * ay + az * az);

    Serial.print("ax=");
    Serial.print(ax, 4);
    Serial.print(" g, ay=");
    Serial.print(ay, 4);
    Serial.print(" g, az=");
    Serial.print(az, 4);
    Serial.print(" g, |a|=");
    Serial.print(accMag, 5);
    Serial.print(" g | GYRO ");
    Serial.print(gx, 3);
    Serial.print(" ");
    Serial.print(gy, 3);
    Serial.print(" ");
    Serial.println(gz, 3);

    magBuffer[sampleIndex] = accMag;
    sampleIndex++;

    if (sampleIndex >= WINDOW_SIZE) {
      printStats(magBuffer, WINDOW_SIZE);
      sampleIndex = 0;
    }
  }

  delay(100);
}
