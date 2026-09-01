"""
StreetDyno 2.0 - Hardware Service Module
Thread-safe background service for Arduino serial telemetry, GPSD polling,
OLED display updates, and automated CSV logging.
"""

from __future__ import annotations
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

try:
    import serial
except ImportError:
    serial = None

from config import (
    SERIAL_PORT,
    SERIAL_BAUD,
    LOG_DIR,
    ALPHA_RPM,
    ALPHA_AFR
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
            "status": "REC" if self.is_logging else "IDLE"
        }


class HardwareService:
    """
    Manages background serial communication with Arduino Nano,
    GPS daemon polling, and hardware OLED display updates.
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
        """Toggles CSV pull logging on/off."""
        with self._lock:
            if self.logger.is_logging:
                self.logger.stop()
            else:
                self.logger.start()
            self.state.is_logging = self.logger.is_logging
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
                status="REC" if self.logger.is_logging else "IDLE",
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

        filtered_rpm = 0.0
        filtered_afr = 0.0

        while self._running:
            # 1. Connect to Arduino Serial if not connected
            if ser is None and serial is not None:
                try:
                    if os.path.exists(SERIAL_PORT):
                        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
                        ser.reset_input_buffer()
                        print(f"🔌 [HardwareService] Connected to Arduino on {SERIAL_PORT}")
                    else:
                        time.sleep(1.0)
                except Exception as e:
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

            # 4. GPS Telemetry Polling
            gps_data: GPSData = self.gps.get_data()
            spd = gps_data.speed_kmh if gps_data else 0.0
            lat = gps_data.lat if gps_data and gps_data.lat is not None else 0.0
            lon = gps_data.lon if gps_data and gps_data.lon is not None else 0.0
            alt = gps_data.alt if gps_data and gps_data.alt is not None else 0.0
            fix = gps_data.fix if gps_data else False

            # 5. Thread-safe state update
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
                self.state.last_update = time.time()

            # 6. Periodic CSV Logging
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

            # 7. Update Hardware OLED (max 10Hz)
            now = time.time()
            if now - last_display_update >= 0.1:
                last_display_update = now
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
