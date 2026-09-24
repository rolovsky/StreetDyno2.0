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
constexpr uint8_t PIN_EGT_CS = 5;    // MAX6675 #1 SPI Chip Select (EGT Sensor)
constexpr uint8_t PIN_EGT_SCK = 6;   // MAX6675 SPI Clock
// constexpr uint8_t PIN_CHT_CS = 7;    // MAX6675 #2 SPI Chip Select (CHT Sensor - disconnected)

// --- Calibration Constants ---
constexpr float PULSES_PER_REV = 3.0f;           // 3 pulses per revolution (Vespa Ducati CDI)
constexpr uint32_t DEBOUNCE_MICROS = 1000;       // EMI lockout threshold (up to 20,000 RPM)
constexpr uint32_t RPM_TIMEOUT_MICROS = 500000;  // 0.5s stall detection
constexpr float USB_VCC_VOLTAGE = 4.71f;         // Measured USB reference voltage

MAX6675 thermocoupleEGT(PIN_EGT_SCK, PIN_EGT_CS, PIN_EGT_SO);
// MAX6675 thermocoupleCHT(PIN_EGT_SCK, PIN_CHT_CS, PIN_EGT_SO); // Disconnected

// --- Atomic Interrupt Variables ---
volatile uint32_t v_firstPulseTime = 0;
volatile uint32_t v_lastPulseTime = 0;
volatile uint16_t v_pulseCount = 0;
volatile uint32_t v_lastSingleInterval = 0;

// --- Runtime State ---
float lastValidRPM = 0.0f;
float lastValidEgt = -1.0f;
float lastValidCht = -1.0f;
uint32_t lastTempMeasurementTime = 0;
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

    pinMode(PIN_EGT_CS, OUTPUT);
    digitalWrite(PIN_EGT_CS, HIGH);
    // pinMode(PIN_CHT_CS, OUTPUT);
    // digitalWrite(PIN_CHT_CS, HIGH);
}

long readVccMillivolts() {
    #if defined(__AVR_ATmega328P__) || defined(__AVR_ATmega168__)
        ADMUX = _BV(REFS0) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
    #else
        return 4710;
    #endif
    delayMicroseconds(1200);  // E-04: ATmega328P datasheet Table 23-2: bandgap settling ≥1.1ms
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

    // 1. Temperature Measurement every 500ms (Sequential EGT -> CHT)
    if (now - lastTempMeasurementTime >= 500) {
        lastTempMeasurementTime = now;

        // 1a. EGT Measurement (Exhaust Gas Temp, 50.0f delta filter)
        const float rawEgt = thermocoupleEGT.readCelsius();
        if (!isnan(rawEgt) && rawEgt > 0.0f) {
            if (lastValidEgt < 0.0f) {
                lastValidEgt = rawEgt;
            } else if (fabsf(rawEgt - lastValidEgt) < 50.0f) {
                lastValidEgt = rawEgt;
            }
        }

        // CHT measurement commented out (sensor disconnected)
        /*
        delayMicroseconds(100);
        const float rawCht = thermocoupleCHT.readCelsius();
        if (!isnan(rawCht) && rawCht > 0.0f) {
            if (lastValidCht < 0.0f) {
                lastValidCht = rawCht;
            } else if (fabsf(rawCht - lastValidCht) < 25.0f) {
                lastValidCht = rawCht;
            }
        }
        */
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
            noInterrupts();
            v_lastSingleInterval = 0;
            v_lastPulseTime = 0;
            interrupts();
        } else if (count >= 2) {
            // E-01: Overflow-safe unsigned delta — uint32_t subtraction wraps correctly
            // at the micros() rollover (~71 min), so pulse_span is always the true
            // elapsed time even when t_last < t_first numerically after wraparound.
            const uint32_t pulse_span = t_last - t_first;
            if (pulse_span > 0 && pulse_span < RPM_TIMEOUT_MICROS) {
                const float dt_sec = static_cast<float>(pulse_span) / 1000000.0f;
                const float pulseFreq = static_cast<float>(count - 1) / dt_sec;
                calculatedRPM = (pulseFreq / PULSES_PER_REV) * 60.0f;
            }
        } else if (singleInterval > 0 && singleInterval < RPM_TIMEOUT_MICROS) {
            // Low RPM (<600 RPM) fallback when only 1 pulse arrived in this frame
            calculatedRPM = (60000000.0f / static_cast<float>(singleInterval)) / PULSES_PER_REV;
        }

        // Asymmetric glitch rejection filter (reject positive EMI noise spikes >3500 RPM/100ms)
        if (lastValidRPM > 1000.0f && calculatedRPM > lastValidRPM + 3500.0f) {
            calculatedRPM = lastValidRPM;  // Positiver Spike (EMI): halten
        } else if (lastValidRPM > 2000.0f && calculatedRPM < lastValidRPM - 5000.0f) {
            // E-03: Negativer Cliff (Kupplung / Schaltvorgang): gedämpft auf 70%
            // statt hartem Sprung — verhindert dRPM/dt → -70.000 RPM/s → P = -82 PS.
            // Schwelle -5000 RPM/Frame trennt Motorbremse (<-1500) von Kupplungsriss (>-7000).
            calculatedRPM = lastValidRPM * 0.7f;
            lastValidRPM = calculatedRPM;
        } else {
            lastValidRPM = calculatedRPM;
        }

        // 3. 2-Point Calibrated Inverted AFR (Synchronized with SIP-Tacho: 19.5 cold, 13.5 idle)
        // With dynamic 1.1V Bandgap VCC compensation to eliminate supply voltage drift
        const float vcc = static_cast<float>(readVccMillivolts()) / 1000.0f;
        analogRead(PIN_AFR); // Dummy Read zum Umladen von C_S/H nach Bandgap-Messung
        const float afrV = static_cast<float>(analogRead(PIN_AFR)) * (vcc / 1023.0f);
        float afrValue = 22.62f - (afrV * 5.72f);
        if (afrValue < 9.0f) afrValue = 9.0f;
        else if (afrValue > 19.5f) afrValue = 19.5f;

        // 4. Send robust NMEA-style telemetry stream ($MICROS;RPM;AFR;EGT;CHT*CHECKSUM)
        char afrBuf[10];
        char egtBuf[10];
        char chtBuf[10];
        dtostrf(afrValue, 1, 2, afrBuf);

        // E-05: lastValidEgt/Cht are initialised to -1.0f (sentinel = no valid reading).
        // Send "0.0" instead — clean_egt_data() filters val <= 0 either way,
        // keeping the raw CSV log strictly non-negative.
        if (lastValidEgt >= 0.0f) {
            dtostrf(lastValidEgt, 1, 1, egtBuf);
        } else {
            strncpy(egtBuf, "0.0", sizeof(egtBuf));
        }

        if (lastValidCht >= 0.0f) {
            dtostrf(lastValidCht, 1, 1, chtBuf);
        } else {
            strncpy(chtBuf, "0.0", sizeof(chtBuf));
        }

        char payload[64];
        snprintf(payload, sizeof(payload), "%lu;%lu;%s;%s;%s",
                 static_cast<unsigned long>(micros()),
                 static_cast<unsigned long>(calculatedRPM + 0.5f),
                 afrBuf,
                 egtBuf,
                 chtBuf);

        uint8_t checksum = 0;
        for (const char* p = payload; *p != '\0'; ++p) {
            checksum ^= static_cast<uint8_t>(*p);
        }

        Serial.print('$');
        Serial.print(payload);
        Serial.print('*');
        if (checksum < 0x10) {
            Serial.print('0');
        }
        Serial.println(checksum, HEX);
    }
}
