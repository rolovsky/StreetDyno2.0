#include <Arduino.h>
#include "max6675.h"

// =========================================================
// --- STREETDYNO FIRMWARE V5.0 (GSF-LOCKOUT EDITION) ---
// =========================================================
// Setup: VMC 177 / 60mm / SI 24 (Lemarxon) / Polini Box
// =========================================================

const float PULSES_PER_REV = 3.0;   
const unsigned long DEBOUNCE_MICROS = 1500; // Sperrzeit gegen EMV-Ringen

const int rpmPin = 2;       
const int afrPin = A0;      

const float INTERNAL_BANDGAP = 1.1; // Default 1.1V Bandgap (für Kalibrierung anpassbar)

MAX6675 thermocouple(6, 5, 4); 

// Volatile Variablen für den Datenaustausch mit dem Interrupt
volatile unsigned long v_lastPulseTime = 0;
volatile unsigned long v_interval = 0;
volatile bool v_newPulse = false;

// Hilfsvariablen für die Hauptschleife
float lastValidRPM = 0;

void rpmInterrupt() {
    unsigned long now = micros();
    unsigned long interval = now - v_lastPulseTime;
    
    // GSF-Lockout: Alles unter 1500µs wird gnadenlos ignoriert (EMV-Schutz)
    if (interval > DEBOUNCE_MICROS) {
        v_interval = interval;
        v_lastPulseTime = now;
        v_newPulse = true;
    }
}

unsigned long smartRound(unsigned long value) {
    int roundTo;
    if (value > 4000)      roundTo = 100;
    else if (value > 2000) roundTo = 50;
    else if (value > 1000) roundTo = 25;
    else if (value > 500)  roundTo = 10;
    else return value;
    return ((value + (roundTo / 2)) / roundTo) * roundTo;
}

long readVcc() {
    // 1.1V Referenzspannung gegen AVcc messen
    #if defined(__AVR_ATmega328P__) || defined(__AVR_ATmega168__)
        ADMUX = _BV(REFS0) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
    #elif defined(__AVR_ATmega32U4__) || defined(__AVR_ATmega32U4) || defined(__AVR_ATmega1280__) || defined(__AVR_ATmega2560__)
        ADMUX = _BV(REFS0) | _BV(MUX4) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
        ADCSRB &= ~_BV(MUX5);   // MUX5 Bit löschen
    #endif  

    delay(2); // Warten bis Referenzspannung stabil ist
    ADCSRA |= _BV(ADSC); // Messung starten
    while (bit_is_set(ADCSRA, ADSC)); // Warten bis fertig

    uint8_t low  = ADCL; // ADCL zuerst lesen (sperrt das Register)
    uint8_t high = ADCH; // ADCH lesen (entsperrt beide)

    long result = (high << 8) | low;

    // Vcc in Millivolt berechnen: INTERNAL_BANDGAP * 1023 * 1000
    result = (INTERNAL_BANDGAP * 1023.0 * 1000.0) / result; 
    return result; 
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

    // EGT-Messung alle 500ms (Max6675 ist träge)
    if (now % 500 == 0) {
        float egt = thermocouple.readCelsius();
        if (!isnan(egt) && egt > 0) {
            lastEgt = egt;
        }
    }

    // 10Hz Datenausgabe an den Raspberry Pi
    if (now - lastUpdate >= 100) {
        lastUpdate = now;
        
        unsigned long currentInterval;
        unsigned long timeSinceLast;
        bool hasNewPulse;

        // Kritische Sektion: Daten aus dem Interrupt sicher kopieren
        noInterrupts();
        currentInterval = v_interval;
        timeSinceLast = micros() - v_lastPulseTime;
        hasNewPulse = v_newPulse;
        v_newPulse = false; 
        interrupts();

        float calculatedRPM = 0;

        // Stillstand-Erkennung (Timeout nach 0,5 Sek)
        if (timeSinceLast > 500000) {
            calculatedRPM = 0;
        } 
        else if (currentInterval > 0) {
            calculatedRPM = (60000000.0 / (float)currentInterval) / PULSES_PER_REV;
            
            // GSF-Zusatzschutz: Plötzliche Sprünge > 3000 RPM pro 100ms 
            // sind physikalisch unmöglich und deuten auf EMV hin.
            if (lastValidRPM > 1000 && abs(calculatedRPM - lastValidRPM) > 3000) {
                calculatedRPM = lastValidRPM; // Wert halten statt abstürzen
            }
        }

        lastValidRPM = calculatedRPM;
        unsigned long roundedRPM = smartRound((unsigned long)calculatedRPM);

        // Dynamische VCC Messung für präzise AFR-Spannungsreferenz
        static float filteredVcc = -1.0;
        float currentVcc = readVcc();
        
        // Dummy-Read und kurze Wartezeit, damit sich der ADC-Multiplexer wieder auf AVcc einschwingen kann
        analogRead(afrPin);
        delay(2);

        if (filteredVcc < 0) {
            filteredVcc = currentVcc;
        } else {
            filteredVcc = (filteredVcc * 0.9) + (currentVcc * 0.1);
        }

        float vccVolts = filteredVcc / 1000.0;
        float afrV = analogRead(afrPin) * (vccVolts / 1023.0);
        float afrValue = 23.14 - (afrV * 6.15); 

        // Das $ Protokoll für den Pi
        Serial.print("$");
        Serial.print(roundedRPM);
        Serial.print(";");
        Serial.print(afrValue, 2);
        Serial.print(";");
        Serial.println(lastEgt, 1);
    }
}
