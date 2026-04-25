#include <Arduino.h>
#include "max6675.h"

// ==========================================
// --- KONFIGURATION: MASTER-UNIT V3.0 ---
// ==========================================
const float PULSES_PER_REV = 4.0;   
const int   MIN_INTERVAL   = 2200;  // Schärferer Filter gegen "Brizzeln" (~6800 RPM max bei Faktor 4)

const int rpmPin = 2;       
const int afrPin = A0;      

float afrOffset = -2.5;     
float egtOffset = 0.0;      

// MAX6675 Pins
int thermoDO = 4, thermoCS = 5, thermoCLK = 6;
MAX6675 thermocouple(thermoCLK, thermoCS, thermoDO);

// ==========================================
// --- INTERNE LOGIK ---
// ==========================================
volatile unsigned long lastPulseTime = 0;
volatile unsigned long currentInterval = 0;
volatile bool newPulse = false;

// Puffer für gleitenden Durchschnitt (RPM Glättung)
const int AVG_SAMPLES = 10;
unsigned long rpmBuffer[AVG_SAMPLES];
int rpmIdx = 0;

unsigned long lastUpdate = 0;
unsigned long lastEgtUpdate = 0;
float currentEgtValue = -1.0;

void rpmInterrupt() {
    unsigned long now = micros();
    unsigned long interval = now - lastPulseTime;
    
    // Nur Impulse akzeptieren, die einen plausiblen Abstand haben
    if (interval > MIN_INTERVAL) {
        currentInterval = interval;
        lastPulseTime = now;
        newPulse = true;
    }
}

void setup() {
    Serial.begin(115200); 
    pinMode(rpmPin, INPUT); // Dein 10k Widerstand gegen GND macht hier die Arbeit
    attachInterrupt(digitalPinToInterrupt(rpmPin), rpmInterrupt, RISING);
    for(int i=0; i<AVG_SAMPLES; i++) rpmBuffer[i] = 0;
}

void loop() {
    unsigned long nowMs = millis();

    // 1. EGT Abfrage alle 500ms (Sicherheits-Intervall für MAX6675)
    if (nowMs - lastEgtUpdate >= 500) {
        lastEgtUpdate = nowMs;
        float rawEgt = thermocouple.readCelsius();
        if (!isnan(rawEgt) && rawEgt > 1.0) {
            currentEgtValue = rawEgt + egtOffset;
        } else {
            // Wenn der Sensor spinnt, versuchen wir einen Reset durch kurzes Warten
            currentEgtValue = -1.0; 
        }
    }

    // 2. Daten-Output an den Pi alle 100ms (10Hz)
    if (nowMs - lastUpdate >= 100) {
        lastUpdate = nowMs;

        float rpm = 0;
        unsigned long timeSinceLast = micros() - lastPulseTime;

        if (timeSinceLast > 500000) {
            rpm = 0; // Motor aus
            for(int i=0; i<AVG_SAMPLES; i++) rpmBuffer[i] = 0;
        } else {
            // Gleitender Durchschnitt der Intervalle für ruhige Werte
            rpmBuffer[rpmIdx] = currentInterval;
            rpmIdx = (rpmIdx + 1) % AVG_SAMPLES;
            
            unsigned long sumInterval = 0;
            int validCount = 0;
            for(int i=0; i<AVG_SAMPLES; i++) {
                if(rpmBuffer[i] > 0) {
                    sumInterval += rpmBuffer[i];
                    validCount++;
                }
            }
            
            if (validCount > 0) {
                float avgInt = (float)sumInterval / validCount;
                rpm = (60000000.0 / avgInt) / PULSES_PER_REV;
            }
        }

        // AFR lesen (Glättung im Pi)
        float afrV = analogRead(afrPin) * (5.0 / 1023.0);
        float afrValue = 20.0 - (afrV * 2.0) + afrOffset; 

        // Output im Format: $RPM;AFR;EGT
        Serial.print("$");
        Serial.print(rpm, 0);
        Serial.print(";");
        Serial.print(afrValue, 2);
        Serial.print(";");
        Serial.println(currentEgtValue, 1);
    }
}
