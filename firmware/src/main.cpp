#include <Arduino.h>
#include "max6675.h"

// --- KONFIGURATION V3.0 [cite: 2026-03-23] ---
const float PULSES_PER_REV = 4.0;   
const int   MIN_INTERVAL   = 2200; 

const int rpmPin = 2;       
const int afrPin = A0;      

// MAX6675 Pins
int thermoDO = 4, thermoCS = 5, thermoCLK = 6;
MAX6675 thermocouple(thermoCLK, thermoCS, thermoDO);

volatile unsigned long lastPulseTime = 0;
volatile unsigned long currentInterval = 0;

const int AVG_SAMPLES = 10;
unsigned long rpmBuffer[AVG_SAMPLES];
int rpmIdx = 0;

unsigned long lastUpdate = 0;
unsigned long lastEgtUpdate = 0;
float currentEgtValue = -1.0;

void rpmInterrupt() {
    unsigned long now = micros();
    unsigned long interval = now - lastPulseTime;
    if (interval > MIN_INTERVAL) {
        currentInterval = interval;
        lastPulseTime = now;
    }
}

void setup() {
    Serial.begin(115200); // Master Baudrate [cite: 2026-03-23]
    pinMode(rpmPin, INPUT); 
    attachInterrupt(digitalPinToInterrupt(rpmPin), rpmInterrupt, RISING);
    for(int i=0; i<AVG_SAMPLES; i++) rpmBuffer[i] = 0;
}

void loop() {
    unsigned long nowMs = millis();

    if (nowMs - lastEgtUpdate >= 500) {
        lastEgtUpdate = nowMs;
        float rawEgt = thermocouple.readCelsius();
        currentEgtValue = (isnan(rawEgt) || rawEgt < 1.0) ? -1.0 : rawEgt;
    }

    if (nowMs - lastUpdate >= 100) {
        lastUpdate = nowMs;
        float rpm = 0;
        unsigned long timeSinceLast = micros() - lastPulseTime;

        if (timeSinceLast > 500000) {
            rpm = 0; 
            for(int i=0; i<AVG_SAMPLES; i++) rpmBuffer[i] = 0;
        } else {
            rpmBuffer[rpmIdx] = currentInterval;
            rpmIdx = (rpmIdx + 1) % AVG_SAMPLES;
            unsigned long sumInterval = 0;
            for(int i=0; i<AVG_SAMPLES; i++) sumInterval += rpmBuffer[i];
            rpm = (60000000.0 / ((float)sumInterval / AVG_SAMPLES)) / PULSES_PER_REV;
        }

        float afrV = analogRead(afrPin) * (5.0 / 1023.0);
        float afrValue = 20.0 - (afrV * 2.0) - 2.5; 

        Serial.print("$");
        Serial.print(rpm, 0);
        Serial.print(";");
        Serial.print(afrValue, 2);
        Serial.print(";");
        Serial.println(currentEgtValue, 1);
    }
}
