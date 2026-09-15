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

## Firmware Modification & Scope Constraints (STRICT)
1. **Scope Lock für Core & Firmware (`main.cpp`, `analyzer_logic.py`, `hardware_service.py`):**
   - Es dürfen KEINE architektonischen Umbauten, Algorithmen-Wechsel oder Refactorings der Interrupt-/Berechnungslogik autonom durchgeführt werden.
   - Bei Signal- oder Messproblemen dürfen eigenständig AUSSCHLIESSLICH Schwellenwerte, Filter-Parameter (Debounce-Zeiten, Multiplikatoren) oder Konfigurationswerte in `user_setup.json` / `config.py` justiert werden.
2. **Diff & Approval Gate:**
   - Vor jeder Änderung an C++-Dateien in `firmware/` oder vor dem Ausführen von `arduino-cli upload` MUSS dem Nutzer ein präziser Plan samt Begründung vorgelegt und dessen explizite Freigabe eingeholt werden.
   - Eigenmächtiges Flashen des Microcontrollers ohne vorangehende Bestätigung ist untersagt.
3. **Rollback-Fähigkeit:**
   - Vor Eingriffen in die Signalverarbeitung ist der aktuelle Git-Stand stets durch einen Branch oder Stash abzusichern.

## 4. Architecture & Single Source of Truth (DRY)
- **Mathematical Logic**: All physics formulas, Savitzky-Golay filtering, slope compensation ($F_{\text{slope}} = m \cdot g \cdot \sin\theta$), DIN 70020 / SAE J1349 weather normalization, and 4-zone SI 24 carburetor jetting rules must reside exclusively in `src/data/analyzer_logic.py` and `src/data/jetting_advisor.py`.
- **Desktop & Web Synchronization**: `desktop_analyzer.py` and Flask routes (`src/web/routes.py`) must import from `src.data` to guarantee 100% identical evaluation results.
- **Web Templates**: Web pages must use dedicated Jinja2 templates in `src/templates/` (`hud.html`, `logs.html`, `analyze.html`, `compare.html`, `tuning.html`, `dyno_sheet.html`).

## 5. Raspberry Pi Zero 2 W Operations
- **Memory & ZRAM**: Maintain 416 MB ZRAM swap (`ALGO=lz4`, `PERCENT=50`) to prevent Out-Of-Memory errors during telemetry processing and chart rendering.
- **Sandbox**: Remote SSH/SCP commands to the Pi require `BypassSandbox: true` due to local sandbox network isolation.

## 6. NumPy / SciPy Array Safety Rules (analyzer_logic.py)
Derived from V5.3 Numerical Hardening — these patterns must be enforced in all future changes to `analyzer_logic.py` and any new physics modules:

1. **SciPy `savgol_filter` window must always be odd:**
   - After any `min()` or arithmetic on `window_length`, enforce `if w % 2 == 0: w -= 1` before passing to `savgol_filter`. A caller passing an even `window_length` causes `ValueError: window_length must be odd` at runtime — not caught by static analysis.
   - Identical `polyorder` must be used in all SG calls within the same computation chain. Mixing `polyorder=1` (linear) and `polyorder=2` (quadratic) in the sanitization path vs. the main filter path causes systematic peak damping after artifact correction.

2. **`np.where()` on a Python scalar returns a 0-dim `ndarray`, not a scalar:**
   - `np.isscalar(np.where(True, 0.1, 0.1))` evaluates to `False`. Boolean indexing on a 0-dim array (`arr[arr > 0]`) produces undefined results.
   - **Rule**: Any time-step array (`dt_arr`) or similar signal array must be built directly as 1-D via `.values` on a Pandas Series, or `np.full(n, val, dtype=float)` for fallbacks — never via `np.where(scalar, ...)` followed by an `np.isscalar` guard.

3. **`n_points = len(df)` must precede any block that passes `n_points` to `np.full()`:**
   - Place `n_points` definition at the top of the function scope or before the first block that uses it as a shape argument. A definition after its use site is a latent `NameError` that only surfaces in exception paths.

## 7. Timestamp & Sampling Rate Integrity (S-03)
Derived from S-03 Subsecond Timestamps & Jitter-Resilient dt Refactoring:

1. **Integer Second Truncation Trap (`%H:%M:%S`):**
   - High-frequency (>1Hz) logs must never rely on string timestamps with 1-second resolution without subseconds. At 10Hz, truncated seconds produce 9x 0.0s and 1x 1.0s deltas.
   - When parsing timestamps, any non-numeric time format must be tested with `(dt_raw <= 0.001).mean() > 0.3`. If detected, calculate the global mean `span / (n - 1)` rather than clamping individual zero-steps, which leaves the 1.0s spikes intact.

2. **Microcontroller Hardware Timestamps ($MICROS):**
   - To eliminate Linux userspace scheduling jitter (±15ms on Pi Zero 2 W), timestamping should synchronize against the ATmega328P 16MHz crystal (`micros()`) via 32-bit rollover masking `(current - sync) & 0xFFFFFFFF`.
   - CSV `Time` column must be written with 4 decimal places (`f"{ts:.4f}"`), providing 0.1ms resolution while remaining standard numeric float.

## 8. Telemetry Logging & Pre-Trigger Buffer Pipeline (S-03 / Dual-Layer)
- **Subsecond Resolution**: Telemetry entries in both `dyno_log_*.csv` and `trip_*.csv` must write timestamps with 4 decimal places (`f"{ts:.4f}"`) anchored to the ATmega328P 16MHz crystal via `current_micros`.
- **Pre-Trigger Buffer Continuity**: The 10-sample rolling pre-trigger buffer in `HardwareService` must preserve subsecond timestamps so that transitioning from pre-buffer to live logging produces no $\Delta t$ discontinuity or artificial acceleration spikes.
- **Legacy Zero-Ratio Guard**: The analysis pipeline must automatically detect truncated-second logs via `(dt_raw <= 0.001).mean() > 0.3` and apply total-span mean $\Delta t$ to avoid 1.0s periodic power collapse.
