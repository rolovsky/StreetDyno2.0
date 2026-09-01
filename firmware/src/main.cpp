#include <Arduino.h>
#include "max6675.h"

// =========================================================================
// --- STREETDYNO FIRMWARE V5.1 (PROD-CALIBRATED & ISOLATED) ---
// =========================================================================
// Target Hardware: Arduino Nano (ATmega328P) @ 16MHz
// Setup: Vespa Largeframe PX / VMC 177 / BGM SI 24 Fastflow
// Calibration: 4.71V USB-VCC Reference for Precision Wideband ADC
// =========================================================================

// --- Pin Assignments ---
constexpr uint8_t PIN_RPM = 2;       // Hardware Interrupt INT0 (SIP Tacho Box)
constexpr uint8_t PIN_AFR = A0;      // 0-5V Wideband Lambda Controller
constexpr uint8_t PIN_EGT_SO = 4;    // MAX6675 SPI Serial Data Out
constexpr uint8_t PIN_EGT_CS = 5;    // MAX6675 SPI Chip Select
constexpr uint8_t PIN_EGT_SCK = 6;   // MAX6675 SPI Clock

// --- Calibration Constants ---
constexpr float PULSES_PER_REV = 3.0f;           // 3 pulses per revolution (Vespa Ducati CDI)
constexpr uint32_t DEBOUNCE_MICROS = 1500;       // EMI lockout threshold (GSF filter)
constexpr uint32_t RPM_TIMEOUT_MICROS = 500000;  // 0.5s stall detection
constexpr float USB_VCC_VOLTAGE = 4.71f;         // Measured USB reference voltage

MAX6675 thermocouple(PIN_EGT_SCK, PIN_EGT_CS, PIN_EGT_SO);

// --- Atomic Interrupt Variables ---
volatile uint32_t v_lastPulseTime = 0;
volatile uint32_t v_interval = 0;
volatile bool v_newPulse = false;

// --- Runtime State ---
float lastValidRPM = 0.0f;
float lastValidEgt = -1.0f;
uint32_t lastEgtMeasurementTime = 0;
uint32_t lastTelemetryOutputTime = 0;

void IRAM_ATTR rpmInterrupt() {
    const uint32_t now = micros();
    const uint32_t interval = now - v_lastPulseTime;

    if (interval > DEBOUNCE_MICROS) {
        v_interval = interval;
        v_lastPulseTime = now;
        v_newPulse = true;
    }
}

uint32_t smartRound(uint32_t value) {
    uint32_t roundTo = 1;
    if (value > 4000)      roundTo = 100;
    else if (value > 2000) roundTo = 50;
    else if (value > 1000) roundTo = 25;
    else if (value > 500)  roundTo = 10;
    else return value;

    return ((value + (roundTo / 2)) / roundTo) * roundTo;
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_RPM, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_RPM), rpmInterrupt, FALLING);
}

void loop() {
    const uint32_t now = millis();

    // 1. EGT Measurement every 500ms
    if (now - lastEgtMeasurementTime >= 500) {
        lastEgtMeasurementTime = now;
        const float rawEgt = thermocouple.readCelsius();

        if (!isnan(rawEgt) && rawEgt > 0.0f) {
            if (lastValidEgt < 0.0f) {
                lastValidEgt = rawEgt;
            } else if (abs(rawEgt - lastValidEgt) < 50.0f) {
                lastValidEgt = rawEgt;
            }
        }
    }

    // 2. 10Hz Telemetry Stream to Raspberry Pi ($RPM;AFR;EGT)
    if (now - lastTelemetryOutputTime >= 100) {
        lastTelemetryOutputTime = now;

        uint32_t currentInterval;
        uint32_t timeSinceLast;

        noInterrupts();
        currentInterval = v_interval;
        timeSinceLast = micros() - v_lastPulseTime;
        v_newPulse = false;
        interrupts();

        float calculatedRPM = 0.0f;

        if (timeSinceLast > RPM_TIMEOUT_MICROS) {
            calculatedRPM = 0.0f;
        } else if (currentInterval > 0) {
            calculatedRPM = (60000000.0f / static_cast<float>(currentInterval)) / PULSES_PER_REV;

            // Reject physical impossibilities (>3000 RPM jump per 100ms indicates EMI)
            if (lastValidRPM > 1000.0f && abs(calculatedRPM - lastValidRPM) > 3000.0f) {
                calculatedRPM = lastValidRPM;
            }
        }

        lastValidRPM = calculatedRPM;
        const uint32_t roundedRPM = smartRound(static_cast<uint32_t>(calculatedRPM));

        // 3. Calibrated Wideband AFR (0-5V Linear Scale: 0V -> 23.14 AFR, 5V -> 7.35 AFR)
        const float afrV = static_cast<float>(analogRead(PIN_AFR)) * (USB_VCC_VOLTAGE / 1023.0f);
        const float afrValue = 23.14f - (afrV * 6.15f);

        // 4. Send structured $ packet
        Serial.print('$');
        Serial.print(roundedRPM);
        Serial.print(';');
        Serial.print(afrValue, 2);
        Serial.print(';');
        Serial.println(lastValidEgt, 1);
    }
}
