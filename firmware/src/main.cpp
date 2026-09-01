#include <Arduino.h>
#include <math.h>
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
volatile uint32_t v_lastValidInterval = 0;
volatile uint32_t v_intervalSum = 0;
volatile uint16_t v_pulseCount = 0;

// --- Runtime State ---
float lastValidRPM = 0.0f;
float lastValidEgt = -1.0f;
uint32_t lastEgtMeasurementTime = 0;
uint32_t lastTelemetryOutputTime = 0;

void rpmInterrupt() {
    const uint32_t now = micros();
    const uint32_t interval = now - v_lastPulseTime;

    if (interval > DEBOUNCE_MICROS) {
        v_intervalSum += interval;
        v_pulseCount++;
        v_lastValidInterval = interval;
        v_lastPulseTime = now;
    }
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
            } else if (fabsf(rawEgt - lastValidEgt) < 50.0f) {
                lastValidEgt = rawEgt;
            }
        }
    }

    // 2. 10Hz Telemetry Stream to Raspberry Pi ($RPM;AFR;EGT)
    if (now - lastTelemetryOutputTime >= 100) {
        lastTelemetryOutputTime = now;

        // Atomically snapshot and reset pulse accumulator
        noInterrupts();
        const uint16_t pulseCount = v_pulseCount;
        const uint32_t intervalSum = v_intervalSum;
        const uint32_t lastPulse = v_lastPulseTime;
        const uint32_t lastValidInterval = v_lastValidInterval;
        v_pulseCount = 0;
        v_intervalSum = 0;
        const uint32_t timeSinceLast = micros() - lastPulse;
        interrupts();

        float calculatedRPM = 0.0f;

        if (timeSinceLast > RPM_TIMEOUT_MICROS || lastPulse == 0) {
            calculatedRPM = 0.0f;
        } else if (pulseCount > 0 && intervalSum > 0) {
            // High-precision average interval across all pulses in the 100ms window
            const float avgInterval = static_cast<float>(intervalSum) / static_cast<float>(pulseCount);
            calculatedRPM = (60000000.0f / avgInterval) / PULSES_PER_REV;
        } else if (lastValidInterval > 0) {
            // Low RPM (<600 RPM) fallback when no new pulse occurred in this exact 100ms frame
            calculatedRPM = (60000000.0f / static_cast<float>(lastValidInterval)) / PULSES_PER_REV;
        }

        // Glitch rejection filter (>3000 RPM jump per 100ms indicates EMI noise)
        if (lastValidRPM > 1000.0f && fabsf(calculatedRPM - lastValidRPM) > 3000.0f) {
            calculatedRPM = lastValidRPM;
        } else {
            lastValidRPM = calculatedRPM;
        }

        // 3. Calibrated Wideband AFR (0-5V Linear Scale: 0V -> 23.14 AFR, 5V -> 7.35 AFR)
        const float afrV = static_cast<float>(analogRead(PIN_AFR)) * (USB_VCC_VOLTAGE / 1023.0f);
        const float afrValue = 23.14f - (afrV * 6.15f);

        // 4. Send continuous unrounded stream to Raspberry Pi
        Serial.print('$');
        Serial.print(static_cast<uint32_t>(calculatedRPM + 0.5f));
        Serial.print(';');
        Serial.print(afrValue, 2);
        Serial.print(';');
        Serial.println(lastValidEgt, 1);
    }
}
