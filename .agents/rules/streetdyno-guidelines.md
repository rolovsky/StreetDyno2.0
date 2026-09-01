---
trigger: always_on
description: Development, hardware deployment, and architecture rules for StreetDyno 2.0
---

# StreetDyno 2.0 Development & Hardware Deployment Guidelines

## 1. Background Task Execution & Asynchronous Commands
- When executing long-running background tasks (e.g. system upgrades, initramfs generation, kernel builds), **never run tight polling loops** using `manage_task status` or `view_file` in rapid succession.
- Stop calling tools and let the system's reactive message dispatch notify you when the task completes.

## 2. Arduino Nano (AVR ATmega328P) Firmware Rules
- **Platform**: ATmega328P @ 16MHz (AVR architecture).
- **Float Math**: Always use `<math.h>` and `fabsf()` for floating-point absolute values (avoid `abs()` which may cast to int).
- **No Non-AVR Macros**: Never use ESP32/ESP8266 macros like `IRAM_ATTR` on AVR targets.
- **Interrupts**: Ensure shared ISR variables are `volatile` and accessed inside `noInterrupts() ... interrupts()` atomic blocks.
- **Timing & Filtering**: Use 1500µs debounce lockout for Vespa Ducati CDI ignition (up to 13,333 RPM) and 10Hz stream (`$RPM;AFR;EGT\n`).

## 3. Arduino Flashing via Raspberry Pi (`/dev/ttyUSB0`)
When flashing the Arduino Nano from the Raspberry Pi:
1. Stop the background service: `sudo systemctl stop streetdyno.service`
2. Ensure the serial port is free: `sudo fuser -k /dev/ttyUSB0 2>/dev/null || true`
3. Flash using the Optiboot bootloader:
   `/usr/local/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328 /tmp/sketch_build`
4. Restart the service: `sudo systemctl start streetdyno.service`

## 4. Architecture & Single Source of Truth (DRY)
- **Mathematical Logic**: All physics formulas, Savitzky-Golay filtering, slope compensation ($F_{\text{slope}} = m \cdot g \cdot \sin\theta$), DIN 70020 / SAE J1349 weather normalization, and 4-zone SI 24 carburetor jetting rules must reside exclusively in `src/data/analyzer_logic.py` and `src/data/jetting_advisor.py`.
- **Desktop & Web Synchronization**: `desktop_analyzer.py` and Flask routes (`src/web/routes.py`) must import from `src.data` to guarantee 100% identical evaluation results.
- **Web Templates**: Web pages must use dedicated Jinja2 templates in `src/templates/` (`hud.html`, `logs.html`, `analyze.html`, `compare.html`, `tuning.html`, `dyno_sheet.html`).

## 5. Raspberry Pi Zero 2 W Operations
- **Memory & ZRAM**: Maintain 416 MB ZRAM swap (`ALGO=lz4`, `PERCENT=50`) to prevent Out-Of-Memory errors during telemetry processing and chart rendering.
- **Sandbox**: Remote SSH/SCP commands to the Pi require `BypassSandbox: true` due to local sandbox network isolation.
