"""
StreetDyno 2.0 - High-Speed CSV Logger Module
Records real-time telemetry (Time, RPM, AFR, EGT, Speed, Lat, Lon, Alt, GPS_Fix)
with 10Hz sampling frequency. Supports manual and automatic WOT-pull triggers.
"""

from __future__ import annotations
import os
import time
import json
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple, Union
import pandas as pd

from config import get_full_setup_metadata, load_carb_setup


def get_log_creation_datetime(filepath: str) -> datetime:
    """
    Extracts the immutable creation timestamp of a log file.
    Prefers the embedded filename timestamp (e.g. 20260911-140700 or 20260121_200500),
    then # RECORDED_AT header, then file creation/modification time.
    """
    fname = os.path.basename(filepath)
    match = re.search(r'(\d{4})(\d{2})(\d{2})[-_](\d{2})(\d{2})(\d{2})', fname)
    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6))
            )
        except Exception:
            pass

    # Check # RECORDED_AT header
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(8):
                line = f.readline()
                if not line:
                    break
                if line.startswith('# RECORDED_AT:'):
                    date_str = line.split('# RECORDED_AT:')[1].strip()
                    return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except Exception:
        pass

    try:
        return datetime.fromtimestamp(os.path.getctime(filepath))
    except Exception:
        return datetime.fromtimestamp(os.path.getmtime(filepath))


def read_log_metadata(filepath: str) -> Dict[str, Any]:
    """
    Fast, lightweight reader that extracts embedded setup metadata from the top comment lines
    of a CSV log file without loading the whole telemetry table into memory.
    Falls back gracefully to current carb setup if legacy log without metadata.
    """
    if not os.path.exists(filepath):
        return {"carb": load_carb_setup(), "is_embedded": False}

    meta_dict = None
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(15):  # Read first 15 lines max
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("# SETUP_META:"):
                    json_part = line[len("# SETUP_META:"):].strip()
                    meta_dict = json.loads(json_part)
                    meta_dict["is_embedded"] = True
                    break
                elif line.startswith("# METADATA:"):
                    json_part = line[len("# METADATA:"):].strip()
                    meta_dict = json.loads(json_part)
                    meta_dict["is_embedded"] = True
                    break
                elif not line.startswith("#") and "Time" in line:
                    break
    except Exception as e:
        print(f"[LOGGER] Error reading metadata from {filepath}: {e}")

    if meta_dict is None:
        # Fallback for legacy logs
        carb = load_carb_setup()
        meta_dict = {
            "displacement_cc": 187.0,
            "ignition_deg": 18.0,
            "carb": carb,
            "is_embedded": False,
            "notes": carb.get("notes", "")
        }

    return meta_dict


def get_setup_badge_string(meta: Dict[str, Any]) -> str:
    """Formats a compact human-readable setup badge string for UI display."""
    carb = meta.get("carb", {}) if "carb" in meta else meta
    hd = carb.get("main_jet_hd", 125)
    nd = carb.get("idle_jet_nd", "60/160")
    tube = carb.get("emulsion_tube", "x234")
    # Simplify tube display
    if "Lemarxon" in str(tube):
        tube_str = str(tube).replace("Lemarxon ", "")
    else:
        tube_str = str(tube)
    ign = meta.get("ignition_deg", carb.get("ignition_deg", 18))
    return f"HD {hd} | ND {nd} | {tube_str} | {ign:.0f}° Zdg"


def load_telemetry_csv(filepath: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Loads telemetry CSV with comment header support and extracts embedded metadata."""
    meta = read_log_metadata(filepath)
    df = pd.read_csv(filepath, comment="#")
    return df, meta


def write_log_metadata(filepath: str, updated_setup: Dict[str, Any], notes: str = "") -> Tuple[bool, Dict[str, Any]]:
    """
    Updates or retroactively injects # SETUP_META header into an existing CSV log file
    while keeping all telemetry data rows (Time,RPM,AFR,...) 100% intact.
    """
    if not os.path.exists(filepath):
        return False, {}

    try:
        data_lines = []
        recorded_at = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("# RECORDED_AT:"):
                    recorded_at = line[len("# RECORDED_AT:"):].strip()
                elif line.startswith("#"):
                    continue
                else:
                    data_lines.append(line)

        # Build clean updated metadata
        current_full = get_full_setup_metadata()
        carb = current_full["carb"].copy()
        carb.update(updated_setup)

        meta = {
            "displacement_cc": float(updated_setup.get("displacement_cc", current_full.get("displacement_cc", 187.0))),
            "stroke_mm": float(updated_setup.get("stroke_mm", current_full.get("stroke_mm", 60.0))),
            "bore_mm": float(updated_setup.get("bore_mm", current_full.get("bore_mm", 63.0))),
            "squish_mm": float(updated_setup.get("squish_mm", current_full.get("squish_mm", 1.4))),
            "ignition_deg": float(updated_setup.get("ignition_deg", current_full.get("ignition_deg", 18.0))),
            "timing": current_full.get("timing", {}),
            "carb": carb,
            "vehicle": current_full.get("vehicle", {}),
            "notes": notes or updated_setup.get("notes", carb.get("notes", "")),
            "is_embedded": True
        }

        meta_json = json.dumps(meta, ensure_ascii=False)

        # Atomic write
        tmp_filepath = filepath + ".tmp"
        with open(tmp_filepath, "w", encoding="utf-8") as f:
            f.write("# STREETDYNO_LOG_VERSION: 2.0\n")
            f.write(f"# RECORDED_AT: {recorded_at}\n")
            f.write(f"# SETUP_META: {meta_json}\n")
            for line in data_lines:
                f.write(line)

        os.replace(tmp_filepath, filepath)
        return True, meta
    except Exception as e:
        print(f"[LOGGER ERROR] Failed to update metadata for {filepath}: {e}")
        return False, {}


class CSVLogger:
    """Thread-safe CSV file logger for high-speed dyno telemetry with embedded metadata."""

    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        self.filepath: Optional[str] = None
        self.is_logging: bool = False
        self.trigger_mode: str = "MANUAL"  # "MANUAL" or "AUTO"
        self.samples_count: int = 0
        self.start_time: float = 0.0

        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def start(
        self,
        filepath: Optional[str] = None,
        trigger: str = "MANUAL",
        pre_buffer: Optional[List[Dict[str, Any]]] = None,
        setup_meta: Optional[Dict[str, Any]] = None
    ) -> str:
        """Initializes a new CSV log file with embedded setup metadata header and optional pre-trigger buffer."""
        self.trigger_mode = trigger
        self.samples_count = 0
        self.start_time = time.time()

        if filepath is not None:
            self.filepath = filepath
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filepath = os.path.join(self.log_dir, f"dyno_log_{timestamp}.csv")

        meta = setup_meta or get_full_setup_metadata()
        meta_json = json.dumps(meta, ensure_ascii=False)

        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("# STREETDYNO_LOG_VERSION: 2.0\n")
            f.write(f"# RECORDED_AT: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# SETUP_META: {meta_json}\n")
            f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")

            if pre_buffer:
                for entry in pre_buffer:
                    t_str = entry.get("time", f"{time.time():.4f}")
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
        print(f"\n[LOGGER] Aufzeichnung ({self.trigger_mode}) gestartet mit Setup-Header: {self.filepath}")
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
        fix: bool = False,
        timestamp: Optional[Union[float, str]] = None
    ) -> None:
        """Writes a single 10Hz telemetry timestep to the CSV file."""
        if self.is_logging and self.filepath:
            if timestamp is not None:
                t_str = f"{timestamp:.4f}" if isinstance(timestamp, (int, float)) else str(timestamp)
            else:
                t_str = f"{time.time():.4f}"
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(f"{t_str},{rpm:.0f},{afr:.2f},{egt:.1f},{speed:.1f},{lat:.6f},{lon:.6f},{alt:.1f},{fix}\n")
            self.samples_count += 1


class TripLogger:
    """
    Thread-safe continuous background trip logger (Blackbox).
    Automatically records 10Hz telemetry for all engine operation regimes
    into the dedicated logs/trips/ folder.
    """

    def __init__(self, log_dir: str = "logs/trips") -> None:
        self.log_dir = log_dir
        self.filepath: Optional[str] = None
        self.is_logging: bool = False
        self.samples_count: int = 0
        self.start_time: float = 0.0
        self._file_handle = None

        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def start(self, filepath: Optional[str] = None, setup_meta: Optional[Dict[str, Any]] = None) -> str:
        """Initializes a new trip CSV file with setup metadata header."""
        if self.is_logging:
            return self.filepath or ""

        self.samples_count = 0
        self.start_time = time.time()

        if filepath is not None:
            self.filepath = filepath
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filepath = os.path.join(self.log_dir, f"trip_{timestamp}.csv")

        meta = setup_meta or get_full_setup_metadata()
        meta_json = json.dumps(meta, ensure_ascii=False)

        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("# STREETDYNO_LOG_VERSION: 2.0\n")
            f.write(f"# RECORDED_AT: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# SETUP_META: {meta_json}\n")
            f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")

        self.is_logging = True
        print(f"\n🛵 [TRIP-LOGGER] Kontinuierliche Blackbox-Fahrt gestartet mit Setup-Header: {self.filepath}")
        return self.filepath

    def stop(self, min_samples: int = 100) -> Optional[str]:
        """
        Stops active trip logging.
        Discards spurious recordings shorter than min_samples (default 100 = 10s @ 10Hz).
        """
        if not self.is_logging:
            return None

        self.is_logging = False
        duration = time.time() - self.start_time if self.start_time > 0 else 0.0
        target_path = self.filepath

        if self.samples_count < min_samples:
            if target_path and os.path.exists(target_path):
                try:
                    os.remove(target_path)
                    print(f"🧹 [TRIP-LOGGER] Minifahrt verworfen (<{min_samples} Samples, {duration:.1f}s): {target_path}")
                except Exception as e:
                    print(f"[TRIP-LOGGER ERROR] Fehler beim Löschen: {e}")
            self.filepath = None
            return None

        print(f"🏁 [TRIP-LOGGER] Fahrt erfolgreich gespeichert ({self.samples_count} Samples, {duration:.1f}s): {target_path}")
        return target_path

    def log(
        self,
        rpm: float,
        afr: float,
        egt: float,
        speed: float,
        lat: float = 0.0,
        lon: float = 0.0,
        alt: float = 0.0,
        fix: bool = False,
        timestamp: Optional[Union[float, str]] = None
    ) -> None:
        """Appends a 10Hz telemetry sample to the trip file."""
        if self.is_logging and self.filepath:
            if timestamp is not None:
                t_str = f"{timestamp:.4f}" if isinstance(timestamp, (int, float)) else str(timestamp)
            else:
                t_str = f"{time.time():.4f}"
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(f"{t_str},{rpm:.0f},{afr:.2f},{egt:.1f},{speed:.1f},{lat:.6f},{lon:.6f},{alt:.1f},{fix}\n")
            self.samples_count += 1


