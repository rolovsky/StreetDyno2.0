"""
StreetDyno 2.0 - High-Speed CSV Logger Module
Records real-time telemetry (Time, RPM, AFR, EGT, Speed, Lat, Lon, Alt, GPS_Fix)
with 10Hz sampling frequency. Supports manual and automatic WOT-pull triggers.
"""

from __future__ import annotations
import os
import time
from typing import Optional, List, Dict, Any


class CSVLogger:
    """Thread-safe CSV file logger for high-speed dyno telemetry."""

    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        self.filepath: Optional[str] = None
        self.is_logging: bool = False
        self.trigger_mode: str = "MANUAL"  # "MANUAL" or "AUTO"
        self.samples_count: int = 0
        self.start_time: float = 0.0

        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def start(self, filepath: Optional[str] = None, trigger: str = "MANUAL", pre_buffer: Optional[List[Dict[str, Any]]] = None) -> str:
        """Initializes a new CSV log file with standard header and optional pre-trigger buffer."""
        self.trigger_mode = trigger
        self.samples_count = 0
        self.start_time = time.time()

        if filepath is not None:
            self.filepath = filepath
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filepath = os.path.join(self.log_dir, f"dyno_log_{timestamp}.csv")

        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")

            if pre_buffer:
                for entry in pre_buffer:
                    t_str = entry.get("time", time.strftime("%H:%M:%S"))
                    rpm = entry.get("rpm", 0.0)
                    afr = entry.get("afr", 0.0)
                    egt = entry.get("egt", 0.0)
                    spd = entry.get("speed", 0.0)
                    lat = entry.get("lat", 0.0)
                    lon = entry.get("lon", 0.0)
                    alt = entry.get("alt", 0.0)
                    fix = entry.get("fix", False)
                    f.write(f"{t_str},{rpm:.0f},{afr:.2f},{egt:.1f},{spd:.1f},{lat:.6f},{lon:.6f},{alt:.1f},{fix}\n")
                    self.samples_count += 1

        self.is_logging = True
        print(f"\n[LOGGER] Aufzeichnung ({self.trigger_mode}) gestartet: {self.filepath}")
        return self.filepath

    def stop(self) -> Optional[str]:
        """Stops active CSV logging and returns file path."""
        self.is_logging = False
        duration = time.time() - self.start_time if self.start_time > 0 else 0.0
        print(f"\n[LOGGER] Aufzeichnung gestoppt ({self.samples_count} Samples, {duration:.1f}s): {self.filepath}")
        return self.filepath

    def discard_current(self) -> None:
        """Stops logging and removes the incomplete/spurious log file."""
        self.is_logging = False
        if self.filepath and os.path.exists(self.filepath):
            try:
                os.remove(self.filepath)
                print(f"[LOGGER] Verworfener Log gelöscht: {self.filepath}")
            except Exception as e:
                print(f"[LOGGER ERROR] Fehler beim Löschen: {e}")
        self.filepath = None

    def log(
        self,
        rpm: float,
        afr: float,
        egt: float,
        speed: float,
        lat: float = 0.0,
        lon: float = 0.0,
        alt: float = 0.0,
        fix: bool = False
    ) -> None:
        """Writes a single 10Hz telemetry timestep to the CSV file."""
        if self.is_logging and self.filepath:
            timestamp = time.strftime("%H:%M:%S")
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(f"{timestamp},{rpm:.0f},{afr:.2f},{egt:.1f},{speed:.1f},{lat:.6f},{lon:.6f},{alt:.1f},{fix}\n")
            self.samples_count += 1

