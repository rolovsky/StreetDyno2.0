#include <Arduino.h>
#include "max6675.h"

// ==========================================
// --- KONFIGURATION: V3.9.1 (SIP-Box Sync) ---
// ==========================================
const int RPM_PIN = 2;              
const int IMPULSES_PER_REV = 3;     // Deine SIP Blackbox Einstellung
const float ALPHA = 0.12;           // Dein Glättungsfaktor
const unsigned long INTERVAL = 100; // Messintervall für Output

const int afrPin = A0;
float afrOffset = -2.5;             // Dein originaler Offset (aktuell inaktiv)

// MAX6675 Pins
int thermoDO = 4, thermoCS = 5, thermoCLK = 6;
MAX6675 thermocouple(thermoCLK, thermoCS, thermoDO);

// ==========================================
// --- VARIABLEN ---
// ==========================================
volatile unsigned long pulseTicks = 0;
float currentRPM = 0;
unsigned long prevMillis = 0;
unsigned long lastEgtUpdate = 0;
float currentEgtValue = -1.0;

// Interrupt Service Routine (Schlank aus deinem Snippet)
void countPulse() {
  pulseTicks++;
}

void setup() {
  Serial.begin(115200);
  
  pinMode(RPM_PIN, INPUT); 
  attachInterrupt(digitalPinToInterrupt(RPM_PIN), countPulse, RISING);
  
  // Kurze Info für den Serial Monitor beim Booten
  Serial.println("System Ready: EMA-Filter + Multimeter Mode Active");
}

void loop() {
  unsigned long now = millis();

  // 1. EGT Abfrage (Alle 500ms für stabile Werte)
  if (now - lastEgtUpdate >= 500) {
    lastEgtUpdate = now;
    float rawEgt = thermocouple.readCelsius();
    if (!isnan(rawEgt) && rawEgt > 1.0) {
      currentEgtValue = rawEgt;
    } else {
      currentEgtValue = -1.0; 
    }
  }

  // 2. Haupt-Berechnung & Output (Alle 100ms)
  if (now - prevMillis >= INTERVAL) {
    // Impulse atomar auslesen
    noInterrupts();
    unsigned long capturedPulses = pulseTicks;
    pulseTicks = 0;
    interrupts();

    // Berechnung der RPM (Deine neue Logik)
    float rawRPM = (capturedPulses * 600.0) / IMPULSES_PER_REV;

    // Glättung mit EMA (Trägheit)
    currentRPM = (rawRPM * ALPHA) + (currentRPM * (1.0 - ALPHA));

    // Nullpunkt-Unterdrückung
    if (currentRPM < 50) currentRPM = 0;

    // --- AFR / VOLT LOGIK ---
    float afrV = analogRead(afrPin) * (5.0 / 1023.0);
    
    // ORIGINAL-BERECHNUNG (AUSKOMMENTIERT):
    // float afrValue = 20.0 - (afrV * 2.0) + afrOffset; 
    
    // MULTIMETER-MODUS: Spannung direkt senden
    float afrValue = afrV; 

    // --- DASHBOARD-KOMPATIBLER OUTPUT ---
    // Format: $RPM;VOLT;EGT
    Serial.print("$");
    Serial.print((int)currentRPM);
    Serial.print(";");
    Serial.print(afrValue, 3); // 3 Nachkommastellen für die Kalibrierung
    Serial.print(";");
    Serial.println(currentEgtValue, 1);

    prevMillis = now;
  }
}
