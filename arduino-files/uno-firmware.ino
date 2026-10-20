#include <Wire.h>

const int MPU_ADDR = 0x68; 

const int PIN_BTN1 = 2; 
const int PIN_BTN2 = 3; 
const int PIN_BTN3 = 4; 

const int PIN_X1 = A0;
const int PIN_Y1 = A1;
const int PIN_X2 = A2;
const int PIN_Y2 = A3;

void setup() {
  Serial.begin(115200);
  
  pinMode(PIN_BTN1, INPUT_PULLUP);
  pinMode(PIN_BTN2, INPUT_PULLUP);
  pinMode(PIN_BTN3, INPUT_PULLUP);

  Wire.begin();
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); 
  Wire.write(0);    
  Wire.endTransmission(true);
}

void loop() {
  int x1 = analogRead(PIN_X1);
  int y1 = analogRead(PIN_Y1);
  int x2 = analogRead(PIN_X2);
  int y2 = analogRead(PIN_Y2);

  int btn1 = digitalRead(PIN_BTN1) == LOW ? 1 : 0;
  int btn2 = digitalRead(PIN_BTN2) == LOW ? 1 : 0;
  int btn3 = digitalRead(PIN_BTN3) == LOW ? 1 : 0;

  int16_t raw_ax, raw_ay;
  
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); 
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 4, true);

  if (Wire.available() >= 4) {
    raw_ax = (Wire.read() << 8) | Wire.read();
    raw_ay = (Wire.read() << 8) | Wire.read();
  } else {
    raw_ax = 0;
    raw_ay = 0;
  }

  float ax = (float)raw_ax / 16384.0;
  float ay = (float)raw_ay / 16384.0;

  Serial.print(x1);      Serial.print(",");
  Serial.print(y1);      Serial.print(",");
  Serial.print(btn1);    Serial.print(",");
  Serial.print(x2);      Serial.print(",");
  Serial.print(y2);      Serial.print(",");
  Serial.print(btn2);    Serial.print(",");
  Serial.print(btn3);    Serial.print(","); 
  Serial.print(ax, 3);   Serial.print(","); 
  Serial.print(ay, 3);   
  Serial.println(); 

  delay(20); 
}
