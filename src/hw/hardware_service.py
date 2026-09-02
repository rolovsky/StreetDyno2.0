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
    ALPHA_RPM,
    ALPHA_AFR,
    TIRE_CIRCUMFERENCE_M,
    GEAR_RATIOS,
    PRIMARY_RATIO
)
from hw.gps_l76k import GPS_L76K, GPSData
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger


@dataclass
class TelemetryState:
    """Thread-safe telemetry data snapshot."""
    rpm: float = 0.0
    rpm_filtered: float = 0.0
    afr: float = 0.0
    afr_filtered: float = 0.0
    egt: float = 0.0
    speed_kmh: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    gps_fix: bool = False
    is_logging: bool = False
    status: str = "IDLE"
    last_update: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rpm": round(self.rpm_filtered, 0),
            "speed": round(self.speed_kmh, 1),
            "afr": round(self.afr_filtered, 2),
            "egt": round(self.egt, 0),
            "lat": self.lat,
            "lon": self.lon,
            "alt": self.alt,
            "fix": self.gps_fix,
            "is_logging": self.is_logging,
            "status": self.status
        }


class HardwareService:
    """
    Manages background serial communication with Arduino Nano,
    GPS daemon polling, hardware OLED display updates,
    and intelligent automatic WOT pull detection.
    """

    def __init__(self, log_dir: str = LOG_DIR) -> None:
        self.log_dir = log_dir
        self.logger = CSVLogger(log_dir=self.log_dir)
        self.gps = GPS_L76K()
        self.display = OLEDDisplay()
        
        self.state = TelemetryState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Pre-trigger rolling buffer (stores the last 1.0s / 10 samples)
        self._pre_buffer: Deque[Dict[str, Any]] = deque(maxlen=10)
        self.auto_trigger_enabled: bool = True

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
                status=self.state.status,
                last_update=self.state.last_update
            )

    def toggle_display_mode(self) -> str:
        """Cycles through OLED display modes (RPM -> SPEED -> AFR -> EGT)."""
        modes = ["RPM", "SPEED", "AFR", "EGT"]
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

        filtered_rpm = 0.0
        filtered_afr = 0.0

        # High-Precision 3-Point Derivative History [(timestamp, rpm), ...]
        rpm_history: Deque[tuple[float, float]] = deque(maxlen=4)

        # WOT Auto-Trigger Tracking State
        accel_streak = 0
        auto_pull_active = False
        pull_start_rpm = 0.0
        pull_peak_rpm = 0.0
        pull_start_time = 0.0
        last_pull_stop_time = 0.0

        # Expected 3rd gear RPM/Speed ratio: ~81.6 (tolerance 65.0 - 105.0)
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

            # 2. Read Serial packet: $RPM;AFR;EGT
            raw_rpm = 0.0
            raw_afr = 0.0
            raw_egt = 0.0

            if ser is not None:
                try:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('ascii', errors='ignore').strip()
                        if line.startswith('$'):
                            parts = line[1:].split(';')
                            if len(parts) >= 3:
                                raw_rpm = float(parts[0])
                                raw_afr = float(parts[1])
                                raw_egt = float(parts[2])
                except Exception:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None

            # 3. EMA Filtering for smooth visual display
            if raw_rpm > 0:
                filtered_rpm = (ALPHA_RPM * raw_rpm) + ((1.0 - ALPHA_RPM) * filtered_rpm)
            else:
                filtered_rpm = 0.0

            if raw_afr > 0:
                filtered_afr = (ALPHA_AFR * raw_afr) + ((1.0 - ALPHA_AFR) * filtered_afr) if filtered_afr > 0 else raw_afr
            else:
                filtered_afr = 0.0

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
            spd = gps_data.speed_kmh if gps_data else 0.0
            lat = gps_data.lat if gps_data and gps_data.lat is not None else 0.0
            lon = gps_data.lon if gps_data and gps_data.lon is not None else 0.0
            alt = gps_data.alt if gps_data and gps_data.alt is not None else 0.0
            fix = gps_data.fix if gps_data else False

            # Update rolling pre-trigger buffer
            sample_entry = {
                "time": time.strftime("%H:%M:%S"),
                "rpm": filtered_rpm,
                "afr": filtered_afr,
                "egt": raw_egt,
                "speed": spd,
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "fix": fix
            }
            self._pre_buffer.append(sample_entry)

            # 5. Intelligent WOT Dyno Pull Auto-Detection (3. Gang)
            if self.auto_trigger_enabled:
                if not self.logger.is_logging:
                    # Strict 3rd gear validation: must be moving > 15 km/h and ratio between 60 and 110 RPM/(km/h)
                    speed_ok = (spd > 15.0)
                    in_gear3 = False
                    if speed_ok:
                        ratio = filtered_rpm / spd
                        in_gear3 = (60.0 <= ratio <= 110.0)

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

                            with self._lock:
                                self.logger.start(trigger="AUTO", pre_buffer=list(self._pre_buffer))
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

                    # Auto-Stop Conditions (Throttle closed / shift / rev limiter)
                    rpm_drop = pull_peak_rpm - filtered_rpm
                    should_stop = (
                        abrupt_drop or
                        (pull_duration >= 0.8 and rpm_drop >= 350.0) or
                        (pull_duration >= 1.2 and drpm_dt <= -250.0) or
                        (filtered_rpm < 2600.0) or
                        (pull_duration >= 15.0)
                    )

                    if should_stop:
                        auto_pull_active = False
                        last_pull_stop_time = loop_now

                        with self._lock:
                            if not abrupt_drop and pull_duration >= 1.0 and rpm_gain >= 1200.0:
                                saved_file = self.logger.stop()
                                print(f"🏁 [AUTO-DYNO] ✅ Prüflauf erfolgreich abgeschlossen (+{rpm_gain:.0f} RPM in {pull_duration:.1f}s): {saved_file}")
                            else:
                                self.logger.discard_current()
                                reason = "Abrupter Einbruch" if abrupt_drop else f"nur +{rpm_gain:.0f} RPM in {pull_duration:.1f}s"
                                print(f"⚠️ [AUTO-DYNO] Verworfener Fehltrigger ({reason}).")

                            self.state.is_logging = False
                            self.state.status = "IDLE"

            # 6. Thread-safe state update
            with self._lock:
                self.state.rpm = raw_rpm
                self.state.rpm_filtered = filtered_rpm
                self.state.afr = raw_afr
                self.state.afr_filtered = filtered_afr
                self.state.egt = raw_egt
                self.state.speed_kmh = spd
                self.state.lat = lat
                self.state.lon = lon
                self.state.alt = alt
                self.state.gps_fix = fix
                self.state.is_logging = self.logger.is_logging
                if not self.logger.is_logging:
                    self.state.status = "IDLE"
                self.state.last_update = loop_now

            # 7. Periodic CSV Logging
            if self.logger.is_logging:
                self.logger.log(
                    rpm=round(filtered_rpm, 1),
                    afr=filtered_afr,
                    egt=raw_egt,
                    speed=spd,
                    lat=lat,
                    lon=lon,
                    alt=alt,
                    fix=fix
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
                        info="VMC 177",
                        gps_fix=fix,
                        is_logging=self.logger.is_logging
                    )
                except Exception:
                    pass

            time.sleep(0.02)

