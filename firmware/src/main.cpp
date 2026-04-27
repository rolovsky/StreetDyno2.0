#include <Arduino.h>
#include "max6675.h"

// ==========================================
// --- STREETDYNO FIRMWARE V4.0 (3-PULSE) ---
// ==========================================
const float PULSES_PER_REV = 3.0;   
const int   DEBOUNCE_MICROS = 1000; 

const int rpmPin = 2;       
const int afrPin = A0;      

MAX6675 thermocouple(6, 5, 4); // CLK, CS, DO

volatile unsigned long v_lastPulseTime = 0;
volatile unsigned long v_currentInterval = 0;
volatile bool v_newPulseData = false;

void rpmInterrupt() {
    unsigned long now = micros();
    unsigned long interval = now - v_lastPulseTime;
    if (interval > DEBOUNCE_MICROS) {
        v_currentInterval = interval;
        v_lastPulseTime = now;
        v_newPulseData = true; 
    }
}

void setup() {
    Serial.begin(115200); 
    pinMode(rpmPin, INPUT_PULLUP); // Zwingend für Optokoppler
    attachInterrupt(digitalPinToInterrupt(rpmPin), rpmInterrupt, FALLING);
    delay(500);
}

void loop() {
    static unsigned long lastUpdate = 0;
    static unsigned long lastEgtUpdate = 0;
    static float lastEgt = -1.0;
    unsigned long now = millis();

    if (now - lastEgtUpdate >= 500) {
        lastEgtUpdate = now;
        float egt = thermocouple.readCelsius();
        lastEgt = (!isnan(egt) && egt > 0) ? egt : -1.0;
    }

    if (now - lastUpdate >= 100) {
        lastUpdate = now;
        unsigned long localInterval = 0;
        bool hasNewPulse = false;

        noInterrupts();
        if (v_newPulseData) {
            localInterval = v_currentInterval;
            v_newPulseData = false;
            hasNewPulse = true;
        }
        unsigned long timeSinceLast = micros() - v_lastPulseTime;
        interrupts();

        float rpm = 0;
        if (timeSinceLast < 500000 && hasNewPulse) {
            rpm = (60000000.0 / (float)localInterval) / PULSES_PER_REV;
        } else if (timeSinceLast > 500000) {
            rpm = 0; 
        }

        float afrV = analogRead(afrPin) * (5.0 / 1023.0);
        float afrValue = 23.14 - (afrV * 6.15); 

        Serial.print("$");
        Serial.print(rpm, 0);
        Serial.print(";");
        Serial.print(afrValue, 2);
        Serial.print(";");
        Serial.println(lastEgt, 1);
    }
}
