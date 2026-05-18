#include <Arduino.h>
#include "max6675.h"

// ==========================================
// --- STREETDYNO FIRMWARE V4.8 (SMOOTH) ---
// ==========================================
const float PULSES_PER_REV = 3.0;   
const int   DEBOUNCE_MICROS = 1500; // Erlaubt bis zu 13.000 RPM bei 3 Pulsen

const int rpmPin = 2;       
const int afrPin = A0;      

MAX6675 thermocouple(6, 5, 4); 

volatile unsigned long v_lastPulseTime = 0;
volatile unsigned long v_avgInterval = 0; 
const float FILTER_ALPHA = 0.25; // Filter für Rohdaten (0.1 = träge, 0.5 = direkt)

void rpmInterrupt() {
    unsigned long now = micros();
    unsigned long interval = now - v_lastPulseTime;
    
    if (interval > DEBOUNCE_MICROS) {
        // Gleitende Mittelung der Zeitabstände (stabilisiert das Signal an der Quelle)
        if (v_avgInterval == 0) v_avgInterval = interval;
        else v_avgInterval = (v_avgInterval * (1.0 - FILTER_ALPHA)) + (interval * FILTER_ALPHA);
        
        v_lastPulseTime = now;
    }
}

// Die "gewichtete Rundung" aus deinem Fundstück, optimiert
unsigned long smartRound(unsigned long value) {
    int roundTo;
    if (value > 4000)      roundTo = 100;
    else if (value > 2000) roundTo = 50;
    else if (value > 1000) roundTo = 25;
    else if (value > 500)  roundTo = 10;
    else return value;

    return ((value + (roundTo / 2)) / roundTo) * roundTo;
}

void setup() {
    Serial.begin(115200); 
    pinMode(rpmPin, INPUT_PULLUP); 
    attachInterrupt(digitalPinToInterrupt(rpmPin), rpmInterrupt, FALLING);
}

void loop() {
    static unsigned long lastUpdate = 0;
    static float lastEgt = -1.0;
    unsigned long now = millis();

    // EGT alle 500ms
    if (now % 500 == 0) {
        float egt = thermocouple.readCelsius();
        lastEgt = (!isnan(egt) && egt > 0) ? egt : -1.0;
    }

    if (now - lastUpdate >= 100) {
        lastUpdate = now;
        
        float rpm = 0;
        unsigned long currentAvg;

        noInterrupts();
        currentAvg = v_avgInterval;
        unsigned long timeSinceLast = micros() - v_lastPulseTime;
        interrupts();

        // Stillstand-Erkennung (0,5 Sek kein Puls)
        if (timeSinceLast < 500000 && currentAvg > 0) {
            rpm = (60000000.0 / (float)currentAvg) / PULSES_PER_REV;
        }

        // Rundung anwenden für ein ruhiges Dashboard
        unsigned long roundedRPM = smartRound((unsigned long)rpm);

        float afrV = analogRead(afrPin) * (5.0 / 1023.0);
        float afrValue = 23.14 - (afrV * 6.15); 

        Serial.print("$");
        Serial.print(roundedRPM);
        Serial.print(";");
        Serial.print(afrValue, 2);
        Serial.print(";");
        Serial.println(lastEgt, 1);
    }
}
