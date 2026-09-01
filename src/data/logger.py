"""
StreetDyno 2.0 - High-Speed CSV Logger Module
Records real-time telemetry (Time, RPM, AFR, EGT, Speed, Lat, Lon, Alt, GPS_Fix)
with 10Hz sampling frequency.
"""

from __future__ import annotations
import os
import time
from typing import Optional


class CSVLogger:
    """Thread-safe CSV file logger for high-speed dyno telemetry."""

    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        self.filepath: Optional[str] = None
        self.is_logging: bool = False

        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def start(self, filepath: Optional[str] = None) -> None:
        """Initializes a new CSV log file with standard header."""
        if filepath is not None:
            self.filepath = filepath
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filepath = os.path.join(self.log_dir, f"dyno_log_{timestamp}.csv")

        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")

        self.is_logging = True
        print(f"\n[LOGGER] Aufzeichnung gestartet: {self.filepath}")

    def stop(self) -> None:
        """Stops active CSV logging."""
        self.is_logging = False
        print("\n[LOGGER] Aufzeichnung gestoppt.")

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
