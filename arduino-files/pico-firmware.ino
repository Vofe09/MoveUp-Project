#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_TinyUSB.h>
#include <RP2040.h>
#include <math.h>

// ============================================================================
// MoveUp Gamepad - clean/stable firmware for Raspberry Pi Pico (RP2040)
//
// Hardware:
//   GP0  -> I2C SDA
//   GP1  -> I2C SCL
//   GP10 -> Button 1 (button to GND)
//   GP11 -> Button 2 (button to GND)
//   GP12 -> Button 3 (button to GND)
//   GP8  -> Button 4 (button to GND)
//
// I2C devices:
//   ADS1115 0x48 -> 4 joystick axes
//   MPU-6500 0x68/0x69 -> 3 gyro axes
//
// USB HID report:
//   1 byte  buttons
//   2 bytes X
//   2 bytes Y
//   2 bytes Z
//   2 bytes Rx
//   2 bytes Ry
//   2 bytes Rz
//   2 bytes Slider
//   Total payload = 15 bytes
//
// Important reliability rules:
//   - USB is initialized before sensor initialization.
//   - No infinite sensor wait loops.
//   - I2C timeout is enabled.
//   - Missing ADS1115/MPU-6500 does not stop USB startup.
//   - Hardware watchdog resets the RP2040 if the firmware really gets stuck.
//   - No Serial dependency and no wait-for-Serial in setup().
// ============================================================================

// ----------------------------------------------------------------------------
// Pin configuration
// ----------------------------------------------------------------------------
constexpr uint8_t PinI2cSda = 0;
constexpr uint8_t PinI2cScl = 1;

constexpr uint8_t PinButton1 = 10;
constexpr uint8_t PinButton2 = 11;
constexpr uint8_t PinButton3 = 12;
constexpr uint8_t PinButton4 = 8;

// ----------------------------------------------------------------------------
// I2C addresses
// ----------------------------------------------------------------------------
constexpr uint8_t Ads1115Address = 0x48;
constexpr uint8_t Mpu6500AddressLow = 0x68;
constexpr uint8_t Mpu6500AddressHigh = 0x69;

// ----------------------------------------------------------------------------
// Timing / reliability
// ----------------------------------------------------------------------------
constexpr uint32_t I2cTimeoutMs = 20;
constexpr uint32_t WatchdogTimeoutMs = 5000;

constexpr uint32_t JoystickUpdateIntervalMs = 10;
constexpr uint32_t MpuUpdateIntervalMs = 5;
constexpr uint32_t ButtonDebounceMs = 10;

constexpr uint16_t AdsConversionDelayUs = 1500;

// ----------------------------------------------------------------------------
// Joystick calibration / mapping
// ----------------------------------------------------------------------------
constexpr uint8_t JoystickCalibrationSamples = 64;
constexpr int32_t JoystickDefaultCenter = 16384;
constexpr int32_t JoystickHalfSpan = 14000;
constexpr int32_t JoystickDeadzone = 180;

// HID axis range
constexpr uint16_t HidMin = 0;
constexpr uint16_t HidCenter = 512;
constexpr uint16_t HidMax = 1023;

// ----------------------------------------------------------------------------
// Gyroscope calibration / mapping
// ----------------------------------------------------------------------------
constexpr uint32_t OrientationCalibrationDurationMs = 10000;
constexpr uint32_t OrientationCalibrationSampleIntervalMs = 5;
constexpr float GyroSensitivityLsbPerDps = 131.0f;
constexpr float ComplementaryFilterAlpha = 0.98f;
constexpr float TiltDeadzoneDegrees = 1.5f;
constexpr float TiltHalfRangeDegrees = 45.0f;

// ----------------------------------------------------------------------------
// ADS1115 registers / configuration
// ----------------------------------------------------------------------------
constexpr uint8_t AdsRegConversion = 0x00;
constexpr uint8_t AdsRegConfig = 0x01;

// OS = start single conversion
// PGA = +/-4.096V
// MODE = single-shot
// DR = 860 SPS
// Comparator disabled
constexpr uint16_t AdsBaseConfig = 0x8000 | 0x0200 | 0x0100 | 0x00E0 | 0x0003;

// MUX for AIN0, AIN1, AIN2, AIN3 single-ended
constexpr uint16_t AdsMuxSingleEnded[4] = {
  0x4000,
  0x5000,
  0x6000,
  0x7000
};

// ----------------------------------------------------------------------------
// MPU-6500 registers / configuration
// ----------------------------------------------------------------------------
constexpr uint8_t MpuRegSmplrtDiv = 0x19;
constexpr uint8_t MpuRegConfig = 0x1A;
constexpr uint8_t MpuRegGyroConfig = 0x1B;
constexpr uint8_t MpuRegAccelConfig = 0x1C;
constexpr uint8_t MpuRegAccelXoutH = 0x3B;
constexpr uint8_t MpuRegWhoAmI = 0x75;
constexpr uint8_t MpuRegPwrMgmt1 = 0x6B;

// Expected MPU-6500 WHO_AM_I value.
constexpr uint8_t Mpu6500WhoAmI = 0x70;

// ----------------------------------------------------------------------------
// HID descriptor
//
// Report layout must match HidGamepadReport exactly:
//   byte 0     = 4 buttons + 4 padding bits
//   bytes 1-14 = 7 x uint16_t axes, unsigned, 0..1023
// ----------------------------------------------------------------------------
uint8_t const HidReportDescriptor[] = {
  0x05, 0x01,                    // Usage Page (Generic Desktop)
  0x09, 0x05,                    // Usage (Game Pad)
  0xA1, 0x01,                    // Collection (Application)

  // Buttons 1..4
  0x05, 0x09,                    // Usage Page (Button)
  0x19, 0x01,                    // Usage Minimum (Button 1)
  0x29, 0x04,                    // Usage Maximum (Button 4)
  0x15, 0x00,                    // Logical Minimum (0)
  0x25, 0x01,                    // Logical Maximum (1)
  0x95, 0x04,                    // Report Count (4)
  0x75, 0x01,                    // Report Size (1)
  0x81, 0x02,                    // Input (Data, Variable, Absolute)

  // Padding to complete one byte
  0x95, 0x01,                    // Report Count (1)
  0x75, 0x04,                    // Report Size (4)
  0x81, 0x03,                    // Input (Constant)

  // Seven unsigned 16-bit axes: X Y Z Rx Ry Rz Slider
  0x05, 0x01,                    // Usage Page (Generic Desktop)
  0x19, 0x30,                    // Usage Minimum (X)
  0x29, 0x36,                    // Usage Maximum (Slider)
  0x15, 0x00,                    // Logical Minimum (0)
  0x26, 0xFF, 0x03,              // Logical Maximum (1023)
  0x75, 0x10,                    // Report Size (16)
  0x95, 0x07,                    // Report Count (7)
  0x81, 0x02,                    // Input (Data, Variable, Absolute)

  0xC0                           // End Collection
};

// ----------------------------------------------------------------------------
// HID report payload
// ----------------------------------------------------------------------------
struct __attribute__((packed)) HidGamepadReport {
  uint8_t buttons;
  uint16_t x;
  uint16_t y;
  uint16_t z;
  uint16_t rx;
  uint16_t ry;
  uint16_t rz;
  uint16_t slider;
};

static_assert(sizeof(HidGamepadReport) == 15, "Unexpected HID report size");

Adafruit_USBD_HID UsbHid;

// ----------------------------------------------------------------------------
// Sensor data structures
// ----------------------------------------------------------------------------
struct MpuSample {
  int16_t accelX;
  int16_t accelY;
  int16_t accelZ;
  int16_t temperatureRaw;
  int16_t gyroX;
  int16_t gyroY;
  int16_t gyroZ;
};

// ----------------------------------------------------------------------------
// Button debouncing
// ----------------------------------------------------------------------------
struct DebouncedButton {
  uint8_t pin;
  bool stablePressed;
  bool lastRawPressed;
  uint32_t lastChangeMs;
};

DebouncedButton buttons[] = {
  {PinButton1, false, false, 0},
  {PinButton2, false, false, 0},
  {PinButton3, false, false, 0},
  {PinButton4, false, false, 0}
};

// ----------------------------------------------------------------------------
// Runtime state
// ----------------------------------------------------------------------------
uint8_t mpuAddress = Mpu6500AddressLow;
bool adsPresent = false;
bool mpuPresent = false;

int32_t joystickCenters[4] = {
  JoystickDefaultCenter,
  JoystickDefaultCenter,
  JoystickDefaultCenter,
  JoystickDefaultCenter
};

int32_t gyroCenters[3] = {0, 0, 0};

float filteredRollDegrees = 0.0f;
float filteredPitchDegrees = 0.0f;
float zeroRollDegrees = 0.0f;
float zeroPitchDegrees = 0.0f;
uint32_t lastOrientationUpdateUs = 0;

int16_t joystickValues[4] = {
  JoystickDefaultCenter,
  JoystickDefaultCenter,
  JoystickDefaultCenter,
  JoystickDefaultCenter
};

int16_t gyroValues[3] = {0, 0, 0};

uint32_t lastJoystickUpdateMs = 0;
uint32_t lastMpuUpdateMs = 0;

uint8_t consecutiveAdsErrors = 0;
uint8_t consecutiveMpuErrors = 0;

// ----------------------------------------------------------------------------
// Utility: keep all long-running code watchdog/USB friendly
// ----------------------------------------------------------------------------
void ServiceBackgroundTasks() {
  rp2040.wdt_reset();

#ifdef TINYUSB_NEED_POLLING_TASK
  TinyUSBDevice.task();
#endif
}

// ----------------------------------------------------------------------------
// I2C helpers
// ----------------------------------------------------------------------------
bool I2cWrite(uint8_t address, const uint8_t* data, uint8_t length, bool sendStop = true) {
  Wire.clearTimeoutFlag();
  Wire.beginTransmission(address);

  if (Wire.write(data, length) != length) {
    Wire.clearTimeoutFlag();
    return false;
  }

  uint8_t status = Wire.endTransmission(sendStop);
  bool timeout = Wire.getTimeoutFlag();
  Wire.clearTimeoutFlag();

  return status == 0 && !timeout;
}

bool I2cWriteRegister8(uint8_t address, uint8_t reg, uint8_t value) {
  uint8_t data[2] = {reg, value};
  return I2cWrite(address, data, sizeof(data));
}

bool I2cWriteRegister16(uint8_t address, uint8_t reg, uint16_t value) {
  uint8_t data[3] = {
    reg,
    static_cast<uint8_t>((value >> 8) & 0xFF),
    static_cast<uint8_t>(value & 0xFF)
  };
  return I2cWrite(address, data, sizeof(data));
}

bool I2cReadRegisters(uint8_t address, uint8_t reg, uint8_t* buffer, uint8_t length) {
  Wire.clearTimeoutFlag();

  Wire.beginTransmission(address);
  if (Wire.write(reg) != 1) {
    Wire.clearTimeoutFlag();
    return false;
  }

  uint8_t status = Wire.endTransmission(false);
  if (status != 0 || Wire.getTimeoutFlag()) {
    Wire.clearTimeoutFlag();
    return false;
  }

  size_t received = Wire.requestFrom(address, length, true);
  bool timeout = Wire.getTimeoutFlag();

  if (timeout || received != length) {
    while (Wire.available()) {
      Wire.read();
    }
    Wire.clearTimeoutFlag();
    return false;
  }

  for (uint8_t i = 0; i < length; ++i) {
    buffer[i] = Wire.read();
  }

  Wire.clearTimeoutFlag();
  return true;
}

bool IsI2cDevicePresent(uint8_t address) {
  Wire.clearTimeoutFlag();
  Wire.beginTransmission(address);
  uint8_t status = Wire.endTransmission(true);
  bool timeout = Wire.getTimeoutFlag();
  Wire.clearTimeoutFlag();
  return status == 0 && !timeout;
}

// ----------------------------------------------------------------------------
// ADS1115
// ----------------------------------------------------------------------------
bool ReadAds1115Register(uint8_t reg, uint16_t& value) {
  uint8_t buffer[2] = {0, 0};

  if (!I2cReadRegisters(Ads1115Address, reg, buffer, sizeof(buffer))) {
    return false;
  }

  value = (static_cast<uint16_t>(buffer[0]) << 8) | buffer[1];
  return true;
}

bool ReadAds1115Channel(uint8_t channel, int16_t& value) {
  if (channel > 3 || !adsPresent) {
    return false;
  }

  uint16_t config = AdsBaseConfig | AdsMuxSingleEnded[channel];

  if (!I2cWriteRegister16(Ads1115Address, AdsRegConfig, config)) {
    return false;
  }

  // 860 SPS -> one conversion is about 1.16 ms.
  // 1.5 ms gives a small safety margin.
  delayMicroseconds(AdsConversionDelayUs);

  uint16_t raw = 0;
  if (!ReadAds1115Register(AdsRegConversion, raw)) {
    return false;
  }

  value = static_cast<int16_t>(raw);
  return true;
}

// ----------------------------------------------------------------------------
// MPU-6500
// ----------------------------------------------------------------------------
uint8_t DetectMpuAddress() {
  if (IsI2cDevicePresent(Mpu6500AddressHigh)) {
    return Mpu6500AddressHigh;
  }

  if (IsI2cDevicePresent(Mpu6500AddressLow)) {
    return Mpu6500AddressLow;
  }

  return 0;
}

bool ReadMpuRegister8(uint8_t reg, uint8_t& value) {
  return I2cReadRegisters(mpuAddress, reg, &value, 1);
}

bool WriteMpuRegister8(uint8_t reg, uint8_t value) {
  return I2cWriteRegister8(mpuAddress, reg, value);
}

bool InitMpu6500() {
  if (!mpuPresent) {
    return false;
  }

  uint8_t whoAmI = 0;
  if (!ReadMpuRegister8(MpuRegWhoAmI, whoAmI)) {
    return false;
  }

  if (whoAmI != Mpu6500WhoAmI) {
    return false;
  }

  // Hardware reset of the MPU, then wake it up.
  if (!WriteMpuRegister8(MpuRegPwrMgmt1, 0x80)) {
    return false;
  }

  delay(50);
  ServiceBackgroundTasks();

  if (!WriteMpuRegister8(MpuRegPwrMgmt1, 0x00)) {
    return false;
  }

  // DLPF configuration.
  if (!WriteMpuRegister8(MpuRegConfig, 0x03)) {
    return false;
  }

  // Gyro +/-250 dps.
  if (!WriteMpuRegister8(MpuRegGyroConfig, 0x00)) {
    return false;
  }

  // Accelerometer +/-2g.
  if (!WriteMpuRegister8(MpuRegAccelConfig, 0x00)) {
    return false;
  }

  // 1 kHz base sample rate with DLPF enabled.
  if (!WriteMpuRegister8(MpuRegSmplrtDiv, 0x00)) {
    return false;
  }

  return true;
}

int16_t ReadSigned16(const uint8_t highByte, const uint8_t lowByte) {
  return static_cast<int16_t>(
    (static_cast<uint16_t>(highByte) << 8) | lowByte
  );
}

bool ReadMpuSample(MpuSample& sample) {
  uint8_t buffer[14] = {0};

  if (!mpuPresent) {
    return false;
  }

  if (!I2cReadRegisters(
        mpuAddress,
        MpuRegAccelXoutH,
        buffer,
        sizeof(buffer))) {
    return false;
  }

  sample.accelX = ReadSigned16(buffer[0], buffer[1]);
  sample.accelY = ReadSigned16(buffer[2], buffer[3]);
  sample.accelZ = ReadSigned16(buffer[4], buffer[5]);
  sample.temperatureRaw = ReadSigned16(buffer[6], buffer[7]);
  sample.gyroX = ReadSigned16(buffer[8], buffer[9]);
  sample.gyroY = ReadSigned16(buffer[10], buffer[11]);
  sample.gyroZ = ReadSigned16(buffer[12], buffer[13]);

  return true;
}

// ----------------------------------------------------------------------------
// Calibration
// ----------------------------------------------------------------------------
void CalibrateJoystickCenters() {
  if (!adsPresent) {
    return;
  }

  int64_t sums[4] = {0, 0, 0, 0};
  uint16_t counts[4] = {0, 0, 0, 0};

  for (uint8_t sampleIndex = 0; sampleIndex < JoystickCalibrationSamples; ++sampleIndex) {
    for (uint8_t channel = 0; channel < 4; ++channel) {
      int16_t value = 0;

      if (ReadAds1115Channel(channel, value)) {
        sums[channel] += value;
        ++counts[channel];
      }

      ServiceBackgroundTasks();
    }
  }

  for (uint8_t channel = 0; channel < 4; ++channel) {
    if (counts[channel] >= 8) {
      joystickCenters[channel] = static_cast<int32_t>(sums[channel] / counts[channel]);
    } else {
      joystickCenters[channel] = JoystickDefaultCenter;
    }
  }
}

float CalculateRollDegrees(const MpuSample& sample) {
  return atan2f((float)sample.accelY, (float)sample.accelZ) * 180.0f / PI;
}

float CalculatePitchDegrees(const MpuSample& sample) {
  float accelYZ = sqrtf((float)sample.accelY * sample.accelY +
                        (float)sample.accelZ * sample.accelZ);
  return atan2f(-(float)sample.accelX, accelYZ) * 180.0f / PI;
}

void CalibrateGyro() {
  if (!mpuPresent) {
    return;
  }

  // Keep the controller still and in the desired neutral position for 20 s.
  int64_t gyroSums[3] = {0, 0, 0};
  double rollSum = 0.0;
  double pitchSum = 0.0;
  uint32_t validSamples = 0;
  uint32_t calibrationStartedMs = millis();
  uint32_t lastSampleMs = 0;

  while ((uint32_t)(millis() - calibrationStartedMs) <
         OrientationCalibrationDurationMs) {
    uint32_t nowMs = millis();

    if ((uint32_t)(nowMs - lastSampleMs) >=
        OrientationCalibrationSampleIntervalMs) {
      lastSampleMs = nowMs;
      MpuSample sample{};

      if (ReadMpuSample(sample)) {
        gyroSums[0] += sample.gyroX;
        gyroSums[1] += sample.gyroY;
        gyroSums[2] += sample.gyroZ;
        rollSum += CalculateRollDegrees(sample);
        pitchSum += CalculatePitchDegrees(sample);
        ++validSamples;
      }
    }

    ServiceBackgroundTasks();
    delay(1);
  }

  if (validSamples > 0) {
    for (uint8_t axis = 0; axis < 3; ++axis) {
      gyroCenters[axis] = (int32_t)(gyroSums[axis] / (int64_t)validSamples);
    }
    zeroRollDegrees = (float)(rollSum / validSamples);
    zeroPitchDegrees = (float)(pitchSum / validSamples);
  } else {
    gyroCenters[0] = gyroCenters[1] = gyroCenters[2] = 0;
    zeroRollDegrees = 0.0f;
    zeroPitchDegrees = 0.0f;
  }

  filteredRollDegrees = 0.0f;
  filteredPitchDegrees = 0.0f;
  lastOrientationUpdateUs = micros();
}

void UpdateOrientation(const MpuSample& sample) {
  uint32_t nowUs = micros();
  float deltaTime = (uint32_t)(nowUs - lastOrientationUpdateUs) / 1000000.0f;
  lastOrientationUpdateUs = nowUs;

  if (deltaTime <= 0.0f || deltaTime > 0.1f) {
    deltaTime = 0.005f;
  }

  float gyroRollRate = ((float)sample.gyroX - gyroCenters[0]) /
                       GyroSensitivityLsbPerDps;
  float gyroPitchRate = ((float)sample.gyroY - gyroCenters[1]) /
                        GyroSensitivityLsbPerDps;

  float gyroRoll = filteredRollDegrees + gyroRollRate * deltaTime;
  float gyroPitch = filteredPitchDegrees + gyroPitchRate * deltaTime;

  float accelRoll = CalculateRollDegrees(sample) - zeroRollDegrees;
  float accelPitch = CalculatePitchDegrees(sample) - zeroPitchDegrees;

  filteredRollDegrees = ComplementaryFilterAlpha * gyroRoll +
                        (1.0f - ComplementaryFilterAlpha) * accelRoll;
  filteredPitchDegrees = ComplementaryFilterAlpha * gyroPitch +
                         (1.0f - ComplementaryFilterAlpha) * accelPitch;
}

uint16_t MapTiltToHid(float angleDegrees) {
  if (fabsf(angleDegrees) <= TiltDeadzoneDegrees) {
    return HidCenter;
  }

  float usableRange = TiltHalfRangeDegrees - TiltDeadzoneDegrees;
  float magnitude = fabsf(angleDegrees) - TiltDeadzoneDegrees;
  magnitude = constrain(magnitude, 0.0f, usableRange);

  int32_t mappedOffset = (int32_t)((magnitude / usableRange) *
                                   (HidMax - HidCenter));

  if (angleDegrees < 0.0f) {
    return (uint16_t)constrain((int32_t)HidCenter - mappedOffset,
                               (int32_t)HidMin, (int32_t)HidCenter);
  }

  return (uint16_t)constrain((int32_t)HidCenter + mappedOffset,
                             (int32_t)HidCenter, (int32_t)HidMax);
}

// Mapping helpers
// ----------------------------------------------------------------------------
uint16_t MapJoystickToHid(int32_t rawValue, int32_t center) {
  int32_t minimum = center - JoystickHalfSpan;
  int32_t maximum = center + JoystickHalfSpan;

  rawValue = constrain(rawValue, minimum, maximum);

  int32_t delta = rawValue - center;

  if (abs(delta) <= JoystickDeadzone) {
    return HidCenter;
  }

  int32_t usableRange = JoystickHalfSpan - JoystickDeadzone;
  if (usableRange <= 0) {
    return HidCenter;
  }

  if (delta > 0) {
    int32_t distance = delta - JoystickDeadzone;
    int32_t mapped = HidCenter + (distance * (HidMax - HidCenter)) / usableRange;
    return static_cast<uint16_t>(constrain(mapped, (int32_t)HidCenter, (int32_t)HidMax));
  }

  int32_t distance = (-delta) - JoystickDeadzone;
  int32_t mapped = HidCenter - (distance * (HidCenter - HidMin)) / usableRange;
  return static_cast<uint16_t>(constrain(mapped, (int32_t)HidMin, (int32_t)HidCenter));
}

uint16_t InvertHidAxis(uint16_t value) {
  return static_cast<uint16_t>(HidMax - value);
}

// ----------------------------------------------------------------------------
// Buttons
// ----------------------------------------------------------------------------
void InitializeButtons() {
  uint32_t now = millis();

  for (auto& button : buttons) {
    pinMode(button.pin, INPUT_PULLUP);

    bool pressed = digitalRead(button.pin) == LOW;
    button.stablePressed = pressed;
    button.lastRawPressed = pressed;
    button.lastChangeMs = now;
  }
}

void UpdateButtons() {
  uint32_t now = millis();

  for (auto& button : buttons) {
    bool rawPressed = digitalRead(button.pin) == LOW;

    if (rawPressed != button.lastRawPressed) {
      button.lastRawPressed = rawPressed;
      button.lastChangeMs = now;
    }

    if (rawPressed != button.stablePressed &&
        (now - button.lastChangeMs) >= ButtonDebounceMs) {
      button.stablePressed = rawPressed;
    }
  }
}

uint8_t GetButtonBits() {
  uint8_t result = 0;

  if (buttons[0].stablePressed) result |= 0x01;
  if (buttons[1].stablePressed) result |= 0x02;
  if (buttons[2].stablePressed) result |= 0x04;
  if (buttons[3].stablePressed) result |= 0x08;

  return result;
}

// ----------------------------------------------------------------------------
// Sensor initialization
// ----------------------------------------------------------------------------
void InitializeSensors() {
  Wire.setSDA(PinI2cSda);
  Wire.setSCL(PinI2cScl);
  Wire.begin();
  Wire.setClock(400000);
  Wire.setTimeout(I2cTimeoutMs);

  // ADS1115: only probe it. Its conversion config is written per channel.
  adsPresent = IsI2cDevicePresent(Ads1115Address);

  // MPU-6500: probe both standard addresses.
  mpuAddress = DetectMpuAddress();
  mpuPresent = (mpuAddress != 0);

  if (mpuPresent) {
    mpuPresent = InitMpu6500();
  }

  ServiceBackgroundTasks();
}

// ----------------------------------------------------------------------------
// Read current joystick state
// ----------------------------------------------------------------------------
void UpdateJoysticks() {
  if (!adsPresent) {
    return;
  }

  for (uint8_t channel = 0; channel < 4; ++channel) {
    int16_t value = 0;

    if (ReadAds1115Channel(channel, value)) {
      joystickValues[channel] = value;
      consecutiveAdsErrors = 0;
    } else {
      if (consecutiveAdsErrors < 255) {
        ++consecutiveAdsErrors;
      }
    }

    ServiceBackgroundTasks();
  }

  // After repeated failures, stop depending on the device.
  // The gamepad remains alive and simply holds the previous joystick values.
  if (consecutiveAdsErrors >= 5) {
    adsPresent = false;
  }
}

// ----------------------------------------------------------------------------
// Read current gyro state
// ----------------------------------------------------------------------------
void UpdateGyro() {
  if (!mpuPresent) {
    return;
  }

  MpuSample sample{};

  if (ReadMpuSample(sample)) {
    UpdateOrientation(sample);
    consecutiveMpuErrors = 0;
    return;
  }

  if (consecutiveMpuErrors < 255) {
    ++consecutiveMpuErrors;
  }

  if (consecutiveMpuErrors >= 5) {
    mpuPresent = false;
  }
}

// ----------------------------------------------------------------------------
// HID report creation
// ----------------------------------------------------------------------------
HidGamepadReport BuildGamepadReport() {
  uint16_t leftY = MapJoystickToHid(joystickValues[0], joystickCenters[0]); // A0
  uint16_t leftX = MapJoystickToHid(joystickValues[1], joystickCenters[1]); // A1
  uint16_t rightY = MapJoystickToHid(joystickValues[2], joystickCenters[2]); // A2
  uint16_t rightX = MapJoystickToHid(joystickValues[3], joystickCenters[3]); // A3

  // Same orientation as the previous design:
  // Left:  X <- LeftY, Y <- LeftX
  // Right: Z <- inverted RightY, Rx <- inverted RightX
  uint16_t leftRotatedX = leftY;
  uint16_t leftRotatedY = leftX;

  uint16_t rightRotatedX = InvertHidAxis(rightY);
  uint16_t rightRotatedY = InvertHidAxis(rightX);

  HidGamepadReport report{};

  report.buttons = GetButtonBits();
  report.x = leftRotatedX;
  report.y = leftRotatedY;
  report.z = rightRotatedX;
  report.rx = rightRotatedY;

  // Send tilt relative to the startup zero, not angular velocity.
  // Yaw remains centered because no magnetometer is available.
  report.ry = mpuPresent ? MapTiltToHid(filteredRollDegrees) : HidCenter;
  report.rz = mpuPresent ? MapTiltToHid(filteredPitchDegrees) : HidCenter;
  report.slider = HidCenter;

  return report;
}

// ----------------------------------------------------------------------------
// USB HID initialization
// ----------------------------------------------------------------------------
void InitializeUsbHid() {
  if (!TinyUSBDevice.isInitialized()) {
    TinyUSBDevice.begin(0);
  }

  UsbHid.setPollInterval(2);
  UsbHid.setReportDescriptor(HidReportDescriptor, sizeof(HidReportDescriptor));
  UsbHid.setStringDescriptor("MoveUp Gamepad");
  UsbHid.begin();

  ServiceBackgroundTasks();
}

// ----------------------------------------------------------------------------
// Setup
// ----------------------------------------------------------------------------
void setup() {
  // Start the watchdog immediately so setup itself cannot hang forever.
  rp2040.wdt_begin(WatchdogTimeoutMs);
  rp2040.wdt_reset();

  // Initialize USB HID first. A bad sensor must never prevent the Pico from
  // exposing itself as a programmable USB device.
  InitializeUsbHid();
  rp2040.wdt_reset();

  InitializeButtons();
  rp2040.wdt_reset();

  InitializeSensors();
  rp2040.wdt_reset();

  // Calibration happens only when the corresponding device is present.
  // Keep the physical controls still and centered during this stage.
  CalibrateJoystickCenters();
  rp2040.wdt_reset();

  CalibrateGyro();
  rp2040.wdt_reset();

  uint32_t now = millis();
  lastJoystickUpdateMs = now;
  lastMpuUpdateMs = now;

  // Initialize output state around the calibrated center.
  for (uint8_t channel = 0; channel < 4; ++channel) {
    joystickValues[channel] = static_cast<int16_t>(joystickCenters[channel]);
  }

  for (uint8_t axis = 0; axis < 3; ++axis) {
    gyroValues[axis] = static_cast<int16_t>(gyroCenters[axis]);
  }

  rp2040.wdt_reset();
}

// ----------------------------------------------------------------------------
// Main loop
// ----------------------------------------------------------------------------
void loop() {
  ServiceBackgroundTasks();
  UpdateButtons();

  if (millis() - lastJoystickUpdateMs >= JoystickUpdateIntervalMs) {
    lastJoystickUpdateMs = millis();
    UpdateJoysticks();
  }

  if (millis() - lastMpuUpdateMs >= MpuUpdateIntervalMs) {
    lastMpuUpdateMs = millis();
    UpdateGyro();
  }

  if (TinyUSBDevice.mounted() && UsbHid.ready()) {
    HidGamepadReport report = BuildGamepadReport();
    UsbHid.sendReport(0, &report, sizeof(report));
  }

  ServiceBackgroundTasks();
  delay(1);
}