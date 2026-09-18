"""
StreetDyno 2.0 - Hardware Service Module
Thread-safe background service for Arduino serial telemetry, GPSD polling,
OLED display updates, and automated CSV logging.
"""

from __future__ import annotations
import os
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Deque

try:
    import serial
except ImportError:
    serial = None

from config import (
    SERIAL_PORT,
    SERIAL_BAUD,
    LOG_DIR,
    TRIP_LOG_DIR,
    ALPHA_RPM,
    ALPHA_AFR,
    TIRE_CIRCUMFERENCE_M,
    GEAR_RATIOS,
    PRIMARY_RATIO
)
from hw.gps_l76k import GPS_L76K, GPSData
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger, TripLogger


@dataclass
class TelemetryState:
    """Thread-safe telemetry data snapshot."""
    arduino_micros: int = 0
    rpm: float = 0.0
    rpm_filtered: float = 0.0
    afr: float = 0.0
    afr_filtered: float = 0.0
    egt: float = 0.0
    cht: float = 0.0
    speed_kmh: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    gps_fix: bool = False
    is_logging: bool = False
    trip_active: bool = False
    status: str = "IDLE"
    last_update: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arduino_micros": self.arduino_micros,
            "rpm": round(self.rpm_filtered, 0),
            "speed": round(self.speed_kmh, 1),
            "afr": round(self.afr_filtered, 2),
            "egt": round(self.egt, 0),
            "cht": round(self.cht, 0),
            "lat": self.lat,
            "lon": self.lon,
            "alt": self.alt,
            "fix": self.gps_fix,
            "is_logging": self.is_logging,
            "trip_active": self.trip_active,
            "status": self.status
        }


class HardwareService:
    """
    Manages background serial communication with Arduino Nano,
    GPS daemon polling, hardware OLED display updates,
    and intelligent automatic WOT pull detection.
    """

    def __init__(self, log_dir: str = LOG_DIR, trip_log_dir: str = TRIP_LOG_DIR) -> None:
        self.log_dir = log_dir
        self.trip_log_dir = trip_log_dir
        self.logger = CSVLogger(log_dir=self.log_dir)
        self.trip_logger = TripLogger(log_dir=self.trip_log_dir)
        self.gps = GPS_L76K()
        self.display = OLEDDisplay()
        
        self.state = TelemetryState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Pre-trigger rolling buffer (stores the last 1.0s / 10 samples)
        self._pre_buffer: Deque[Dict[str, Any]] = deque(maxlen=10)
        self.auto_trigger_enabled: bool = True
        self.trip_logging_enabled: bool = True
        self._serial_buffer: str = ""
        self.current_micros: int = 0
        self.current_rpm: float = 0.0
        self.current_afr: float = 0.0
        self.current_egt: float = 0.0
        self.current_cht: float = 0.0
        self.last_serial_time: float = time.time()
        self._arduino_sync_time: float = 0.0
        self._arduino_sync_micros: int = 0

    def _parse_telemetry_line(self, line: str) -> bool:
        """Parses $MICROS;RPM;AFR;EGT;CHT*CHECKSUM line with XOR checksum validation."""
        if not (line.startswith('$') and '*' in line):
            return False
        payload, checksum_str = line[1:].split('*', 1)

        # XOR checksum validation over payload characters (without '$' and '*')
        calc_cs = 0
        for ch in payload:
            calc_cs ^= ord(ch)

        try:
            expected_cs = int(checksum_str.strip(), 16)
        except ValueError:
            return False

        if calc_cs != expected_cs:
            return False

        parts = payload.split(';')
        if len(parts) >= 4:
            try:
                self.current_micros = int(parts[0])
                self.current_rpm = float(parts[1])
                self.current_afr = float(parts[2])
                self.current_egt = float(parts[3])
                self.current_cht = float(parts[4]) if len(parts) >= 5 else 0.0
                self.last_serial_time = time.time()
                return True
            except (ValueError, TypeError):
                return False
        return False

    def start(self) -> None:
        """Starts the background hardware polling loop."""
        if self._running:
            return
        self._running = True
        self.gps.start()
        self._thread = threading.Thread(target=self._hardware_loop, daemon=True, name="HardwareServiceThread")
        self._thread.start()
        print("🚀 [HardwareService] Background hardware daemon started.")

    def stop(self) -> None:
        """Stops the background hardware polling loop."""
        self._running = False
        self.gps.stop()
        if self.logger.is_logging:
            self.logger.stop()
        if self.trip_logger.is_logging:
            self.trip_logger.stop(min_samples=50)
        print("🛑 [HardwareService] Background hardware daemon stopped.")

    def toggle_logging(self) -> bool:
        """Manually toggles CSV pull logging on/off."""
        with self._lock:
            if self.logger.is_logging:
                self.logger.stop()
            else:
                self.logger.start(trigger="MANUAL")
            self.state.is_logging = self.logger.is_logging
            self.state.status = "REC" if self.logger.is_logging else "IDLE"
            return self.logger.is_logging

    def get_telemetry(self) -> TelemetryState:
        """Returns a snapshot of the current telemetry state."""
        with self._lock:
            return TelemetryState(
                arduino_micros=self.state.arduino_micros,
                rpm=self.state.rpm,
                rpm_filtered=self.state.rpm_filtered,
                afr=self.state.afr,
                afr_filtered=self.state.afr_filtered,
                egt=self.state.egt,
                speed_kmh=self.state.speed_kmh,
                lat=self.state.lat,
                lon=self.state.lon,
                alt=self.state.alt,
                gps_fix=self.state.gps_fix,
                is_logging=self.logger.is_logging,
                trip_active=self.trip_logger.is_logging,
                status=self.state.status,
                last_update=self.state.last_update
            )

    def toggle_display_mode(self) -> str:
        """Cycles through OLED display modes (RPM -> SPEED -> AFR -> EGT -> CHT)."""
        modes = ["RPM", "SPEED", "AFR", "EGT", "CHT"]
        curr = self.display.mode
        next_idx = (modes.index(curr) + 1) % len(modes) if curr in modes else 0
        new_mode = modes[next_idx]
        self.display.set_mode(new_mode)
        return new_mode

    def _hardware_loop(self) -> None:
        """Core background thread reading Arduino Serial, GPS, and logging."""
        ser: Optional[serial.Serial] = None
        last_display_update = 0.0
        last_loop_time = time.time()

        # High-Precision 3-Point Derivative History [(timestamp, rpm), ...]
        rpm_history: Deque[tuple[float, float]] = deque(maxlen=4)

        # WOT Auto-Trigger Tracking State
        accel_streak = 0
        auto_pull_active = False
        pull_start_rpm = 0.0
        pull_peak_rpm = 0.0
        pull_start_time = 0.0
        last_pull_stop_time = 0.0

        # Background Trip Logger Tracking State
        engine_start_streak = 0
        last_engine_active_time = time.time()

        # S-02: EMA filter state — ALPHA_RPM / ALPHA_AFR from config.py now active.
        # Previously filtered_rpm / filtered_afr were raw pass-throughs (dead code).
        _ema_rpm: float = 0.0
        _ema_afr: float = 0.0

        # Expected 3rd gear RPM/Speed ratio: ~80.69 (tolerance 65.0 - 98.0)
        i_gear3 = PRIMARY_RATIO * GEAR_RATIOS.get(3, 38.0 / 17.0)
        gear3_ratio_nominal = (60.0 * i_gear3) / (TIRE_CIRCUMFERENCE_M * 3.6)

        while self._running:
            loop_now = time.time()
            dt = max(0.01, loop_now - last_loop_time)
            last_loop_time = loop_now

            # 1. Connect to Arduino Serial if not connected
            if ser is None and serial is not None:
                try:
                    if os.path.exists(SERIAL_PORT):
                        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
                        ser.reset_input_buffer()
                        print(f"🔌 [HardwareService] Connected to Arduino on {SERIAL_PORT}")
                    else:
                        time.sleep(1.0)
                except Exception:
                    ser = None
                    time.sleep(1.0)

            # 2. Read all available Serial packets via non-blocking buffer
            if ser is not None:
                try:
                    if ser.in_waiting > 0:
                        chunk = ser.read(ser.in_waiting).decode('ascii', errors='ignore')
                        self._serial_buffer += chunk
                        while '\n' in self._serial_buffer:
                             line, self._serial_buffer = self._serial_buffer.split('\n', 1)
                             line = line.strip()
                             if line.startswith('$') and '*' in line:
                                 self._parse_telemetry_line(line)
                except Exception:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None

            # Disconnect / Stall Timeout: If no packet received for > 1.0s, reset values
            if loop_now - self.last_serial_time > 1.0:
                self.current_rpm = 0.0
                self.current_afr = 0.0
                self.current_egt = 0.0
                self.current_cht = 0.0

            # S-02: Apply EMA smoothing — ALPHA_* from config.py now active (was dead code).
            # On stall (rpm=0): decay at ×0.8 per loop instead of hard zero to avoid
            # transient WOT-trigger resets from isolated zero-packets.
            _raw_rpm = self.current_rpm
            if _raw_rpm > 0.0:
                _ema_rpm = ALPHA_RPM * _raw_rpm + (1.0 - ALPHA_RPM) * _ema_rpm
            else:
                _ema_rpm = max(0.0, _ema_rpm * 0.8)

            _raw_afr = self.current_afr
            if _raw_afr > 0.0:
                _ema_afr = ALPHA_AFR * _raw_afr + (1.0 - ALPHA_AFR) * _ema_afr

            filtered_rpm = _ema_rpm
            filtered_afr = _ema_afr if _ema_afr > 0.0 else _raw_afr
            raw_egt = self.current_egt
            raw_cht = self.current_cht

            # 4. Robust 3-Point Rolling Central Derivative (dRPM/dt)
            rpm_history.append((loop_now, filtered_rpm))
            if len(rpm_history) >= 3:
                t_prev2, rpm_prev2 = rpm_history[-3]
                t_curr, rpm_curr = rpm_history[-1]
                dt_span = t_curr - t_prev2
                drpm_dt = (rpm_curr - rpm_prev2) / dt_span if dt_span > 0.02 else 0.0
            elif len(rpm_history) >= 2:
                t_prev1, rpm_prev1 = rpm_history[-2]
                t_curr, rpm_curr = rpm_history[-1]
                dt_span = t_curr - t_prev1
                drpm_dt = (rpm_curr - rpm_prev1) / dt_span if dt_span > 0.01 else 0.0
            else:
                drpm_dt = 0.0

            # 4. GPS Telemetry Polling
            gps_data: GPSData = self.gps.get_data()
            # S-01: Guard against frozen GPS values after signal dropout.
            # Using monotonic packet reception age (immune to NTP/system clock jumps/timezones).
            # If gpsd TPV report is older than GPS_STALENESS_LIMIT_S or mode < 2 (no fix),
            # speed is zeroed out and fix is marked False.
            GPS_STALENESS_LIMIT_S = 2.5
            _now_mono = time.monotonic()
            _gps_age = (_now_mono - gps_data.last_seen) if (gps_data and gps_data.last_seen > 0) else 999.0
            _gps_fresh = (gps_data is not None) and (_gps_age < GPS_STALENESS_LIMIT_S)

            fix = bool(gps_data and gps_data.fix and _gps_fresh)
            spd = gps_data.speed_kmh if fix else 0.0
            lat = gps_data.lat if (gps_data and gps_data.lat is not None) else 0.0
            lon = gps_data.lon if (gps_data and gps_data.lon is not None) else 0.0
            alt = gps_data.alt if (gps_data and gps_data.alt is not None) else 0.0

            # S-03: Microsecond-precise hardware timestamp derived from Arduino crystal
            if self.current_micros > 0:
                if self._arduino_sync_micros == 0 or (loop_now - self._arduino_sync_time) >= 60.0:
                    self._arduino_sync_time = loop_now
                    self._arduino_sync_micros = self.current_micros
                    hw_timestamp = loop_now
                else:
                    delta_us = (self.current_micros - self._arduino_sync_micros) & 0xFFFFFFFF
                    hw_timestamp = self._arduino_sync_time + (delta_us / 1_000_000.0)
            else:
                hw_timestamp = loop_now

            # Update rolling pre-trigger buffer
            sample_entry = {
                "time": f"{hw_timestamp:.4f}",
                "rpm": filtered_rpm,
                "afr": filtered_afr,
                "egt": raw_egt,
                "cht": raw_cht,
                "speed": spd,
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "fix": fix
            }
            self._pre_buffer.append(sample_entry)

            # 5a. Continuous Background Trip Lifecycle (Blackbox)
            is_engine_active = (filtered_rpm >= 400.0) or (spd >= 5.0)
            if is_engine_active:
                last_engine_active_time = loop_now
                if not self.trip_logger.is_logging and self.trip_logging_enabled:
                    engine_start_streak += 1
                    if engine_start_streak >= 5:  # ~100ms continuous running
                        with self._lock:
                            self.trip_logger.start()
                        engine_start_streak = 0
                else:
                    engine_start_streak = 0
            else:
                engine_start_streak = 0
                if self.trip_logger.is_logging:
                    if (loop_now - last_engine_active_time) >= 30.0:
                        with self._lock:
                            self.trip_logger.stop(min_samples=100)

            # 5b. Intelligent WOT Dyno Pull Auto-Detection (3. Gang)
            if self.auto_trigger_enabled:
                if not self.logger.is_logging:
                    # Strict 3rd gear validation: must be moving > 15 km/h and ratio between 65 and 98 RPM/(km/h)
                    speed_ok = (spd > 15.0)
                    in_gear3 = False
                    if speed_ok:
                        ratio = filtered_rpm / spd
                        in_gear3 = (65.0 <= ratio <= 98.0)

                    # WOT Acceleration Trigger Condition
                    cooldown_ok = (loop_now - last_pull_stop_time) >= 2.5
                    if speed_ok and in_gear3 and cooldown_ok and filtered_rpm >= 2800.0 and drpm_dt >= 200.0:
                        accel_streak += 1
                        if accel_streak >= 3:  # ~300ms continuous acceleration
                            auto_pull_active = True
                            pull_start_rpm = filtered_rpm
                            pull_peak_rpm = filtered_rpm
                            pull_start_time = loop_now
                            accel_streak = 0

                            dyno_pre_buffer = []
                            for entry in self._pre_buffer:
                                e = dict(entry)
                                e_rpm = float(e.get("rpm", 0.0))
                                if e_rpm > 1000.0:
                                    e["speed"] = round((e_rpm / 60.0 / i_gear3) * TIRE_CIRCUMFERENCE_M * 3.6, 1)
                                dyno_pre_buffer.append(e)

                            with self._lock:
                                self.logger.start(trigger="AUTO", pre_buffer=dyno_pre_buffer)
                                self.state.is_logging = True
                                self.state.status = "REC (AUTO)"
                            print(f"\n⚡ [AUTO-DYNO] 🎯 WOT-Pull im 3. Gang erkannt ({pull_start_rpm:.0f} RPM, {spd:.1f} km/h, Ratio {filtered_rpm/spd:.1f})! Aufzeichnung aktiv.")
                    else:
                        accel_streak = max(0, accel_streak - 1)

                elif auto_pull_active:
                    # Ongoing Auto-Pull Tracking
                    pull_peak_rpm = max(pull_peak_rpm, filtered_rpm)
                    pull_duration = loop_now - pull_start_time
                    rpm_gain = pull_peak_rpm - pull_start_rpm

                    # Abrupt Drop-Filter (e.g. clutch pulled or shift before real pull)
                    abrupt_drop = (drpm_dt <= -500.0 and rpm_gain < 1000.0 and pull_duration >= 0.3)
                    
                    # Hard lean cutoff (throttle closed / deceleration / coasting)
                    lean_cutoff = (filtered_afr >= 14.8 and pull_duration >= 0.5)

                    # Auto-Stop Conditions (Throttle closed / shift / rev limiter)
                    rpm_drop = pull_peak_rpm - filtered_rpm
                    should_stop = (
                        abrupt_drop or
                        lean_cutoff or
                        (pull_duration >= 0.8 and rpm_drop >= 350.0) or
                        (pull_duration >= 1.2 and drpm_dt <= -250.0) or
                        (filtered_rpm < 2600.0) or
                        (pull_duration >= 15.0)
                    )

                    if should_stop:
                        auto_pull_active = False
                        last_pull_stop_time = loop_now

                        with self._lock:
                            if not abrupt_drop and pull_duration >= 1.5 and rpm_gain >= 1600.0:
                                saved_file = self.logger.stop()
                                print(f"🏁 [AUTO-DYNO] ✅ Prüflauf erfolgreich abgeschlossen (+{rpm_gain:.0f} RPM in {pull_duration:.1f}s): {saved_file}")
                            else:
                                self.logger.discard_current()
                                reason = "Abrupter Einbruch" if abrupt_drop else ("Magerlauf/Schiebebetrieb" if lean_cutoff else f"nur +{rpm_gain:.0f} RPM in {pull_duration:.1f}s")
                                print(f"⚠️ [AUTO-DYNO] Verworfener Fehltrigger ({reason}).")

                            self.state.is_logging = False
                            self.state.status = "IDLE"

            # Dyno Pull Kinematic Speed: during an active dyno pull (always 3rd gear),
            # compute speed directly from RPM and transmission ratio to eliminate GPS latency/distortion
            if self.logger.is_logging or auto_pull_active:
                dyno_spd = round((filtered_rpm / 60.0 / i_gear3) * TIRE_CIRCUMFERENCE_M * 3.6, 1) if filtered_rpm > 1000.0 else spd
            else:
                dyno_spd = spd

            # 6. Thread-safe state update
            with self._lock:
                self.state.arduino_micros = self.current_micros
                self.state.rpm = self.current_rpm
                self.state.rpm_filtered = filtered_rpm
                self.state.afr = self.current_afr
                self.state.afr_filtered = filtered_afr
                self.state.egt = raw_egt
                self.state.cht = raw_cht
                self.state.speed_kmh = dyno_spd if self.logger.is_logging else spd
                self.state.lat = lat
                self.state.lon = lon
                self.state.alt = alt
                self.state.gps_fix = fix
                self.state.is_logging = self.logger.is_logging
                self.state.trip_active = self.trip_logger.is_logging
                if not self.logger.is_logging:
                    self.state.status = "IDLE"
                self.state.last_update = loop_now

            # 7. Periodic CSV Logging
            if self.logger.is_logging:
                self.logger.log(
                    rpm=round(filtered_rpm, 1),
                    afr=filtered_afr,
                    egt=raw_egt,
                    cht=raw_cht,
                    speed=dyno_spd,
                    lat=lat,
                    lon=lon,
                    alt=alt,
                    fix=fix,
                    timestamp=hw_timestamp
                )

            # Continuous Background Trip Logging
            if self.trip_logger.is_logging:
                self.trip_logger.log(
                    rpm=round(filtered_rpm, 1),
                    afr=filtered_afr,
                    egt=raw_egt,
                    cht=raw_cht,
                    speed=spd,
                    lat=lat,
                    lon=lon,
                    alt=alt,
                    fix=fix,
                    timestamp=hw_timestamp
                )

            # 8. Update Hardware OLED (max 10Hz)
            if loop_now - last_display_update >= 0.1:
                last_display_update = loop_now
                try:
                    self.display.show_status(
                        rpm=filtered_rpm,
                        speed=spd,
                        afr=filtered_afr,
                        egt=raw_egt,
                        cht=raw_cht,
                        info="VMC 177",
                        gps_fix=fix,
                        is_logging=self.logger.is_logging
                    )
                except Exception:
                    pass

            time.sleep(0.02)

