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
constexpr uint32_t DEBOUNCE_MICROS = 1000;       // EMI lockout threshold (up to 20,000 RPM)
constexpr uint32_t RPM_TIMEOUT_MICROS = 500000;  // 0.5s stall detection
constexpr float USB_VCC_VOLTAGE = 4.71f;         // Measured USB reference voltage

MAX6675 thermocouple(PIN_EGT_SCK, PIN_EGT_CS, PIN_EGT_SO);

// --- Atomic Interrupt Variables ---
volatile uint32_t v_firstPulseTime = 0;
volatile uint32_t v_lastPulseTime = 0;
volatile uint16_t v_pulseCount = 0;
volatile uint32_t v_lastSingleInterval = 0;

// --- Runtime State ---
float lastValidRPM = 0.0f;
float lastValidEgt = -1.0f;
uint32_t lastEgtMeasurementTime = 0;
uint32_t lastTelemetryOutputTime = 0;

void rpmInterrupt() {
    const uint32_t now = micros();
    const uint32_t interval = now - v_lastPulseTime;

    if (interval > DEBOUNCE_MICROS) {
        if (v_pulseCount == 0) {
            v_firstPulseTime = now;
        }
        v_pulseCount++;
        v_lastSingleInterval = interval;
        v_lastPulseTime = now;
    }
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_RPM, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_RPM), rpmInterrupt, FALLING);
}

long readVccMillivolts() {
    #if defined(__AVR_ATmega328P__) || defined(__AVR_ATmega168__)
        ADMUX = _BV(REFS0) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
    #else
        return 4710;
    #endif
    delayMicroseconds(350);
    ADCSRA |= _BV(ADSC);
    while (bit_is_set(ADCSRA, ADSC));
    uint8_t low = ADCL;
    uint8_t high = ADCH;
    long result = (high << 8) | low;
    result = 1125300L / result; // 1.1 * 1023 * 1000
    if (result < 4000 || result > 5500) return 4710;
    return result;
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

        noInterrupts();
        const uint16_t count = v_pulseCount;
        const uint32_t t_first = v_firstPulseTime;
        const uint32_t t_last = v_lastPulseTime;
        const uint32_t singleInterval = v_lastSingleInterval;
        v_pulseCount = 0;
        v_firstPulseTime = 0;
        interrupts();

        float calculatedRPM = 0.0f;
        const uint32_t timeSinceLast = (t_last > 0) ? (micros() - t_last) : 999999;

        if (timeSinceLast > RPM_TIMEOUT_MICROS || t_last == 0) {
            calculatedRPM = 0.0f;
        } else if (count >= 2 && t_last > t_first) {
            // High-precision multi-pulse frequency over all pulses in the 100ms window
            const float dt_sec = static_cast<float>(t_last - t_first) / 1000000.0f;
            const float pulseFreq = static_cast<float>(count - 1) / dt_sec;
            calculatedRPM = (pulseFreq / PULSES_PER_REV) * 60.0f;
        } else if (singleInterval > 0 && singleInterval < RPM_TIMEOUT_MICROS) {
            // Low RPM (<600 RPM) fallback when only 1 pulse arrived in this frame
            calculatedRPM = (60000000.0f / static_cast<float>(singleInterval)) / PULSES_PER_REV;
        }

        // Glitch rejection filter (>3500 RPM jump per 100ms indicates EMI noise)
        if (lastValidRPM > 1000.0f && fabsf(calculatedRPM - lastValidRPM) > 3500.0f) {
            calculatedRPM = lastValidRPM;
        } else {
            lastValidRPM = calculatedRPM;
        }

        // 3. 2-Point Calibrated Inverted AFR (Synchronized with SIP-Tacho: 19.5 cold, 13.5 idle)
        // With dynamic 1.1V Bandgap VCC compensation to eliminate supply voltage drift
        const float vcc = static_cast<float>(readVccMillivolts()) / 1000.0f;
        const float afrV = static_cast<float>(analogRead(PIN_AFR)) * (vcc / 1023.0f);
        float afrValue = 22.62f - (afrV * 5.72f);
        if (afrValue < 9.0f) afrValue = 9.0f;
        else if (afrValue > 19.5f) afrValue = 19.5f;

        // 4. Send continuous unrounded stream to Raspberry Pi
        Serial.print('$');
        Serial.print(static_cast<uint32_t>(calculatedRPM + 0.5f));
        Serial.print(';');
        Serial.print(afrValue, 2);
        Serial.print(';');
        Serial.println(lastValidEgt, 1);
    }
}
