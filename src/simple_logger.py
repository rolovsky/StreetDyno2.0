#!/usr/bin/env python3
"""
StreetDyno 2.0 – Phase 2.5 Minimal Bootstrap Logger
=====================================================
Single responsibility: collect raw telemetry, write it to disk.

Architecture
------------
  Thread 1  (GPSReader, daemon)
            Polls gpsd JSON socket every GPS_POLL_INTERVAL seconds.
            Updates GPSSnapshot under a Lock. Never blocks the main loop.

  Main loop
            Reads /dev/ttyUSB0, parses $MICROS;RPM;AFR;EGT;CHT*XX frames
            (XOR checksum, identical to V5.1 hardware_service.py §111-145).
            Merges serial + GPS snapshot → writes raw CSV rows.
            Publishes atomic JSON state snapshot for Flask HUD.

CSV outputs (V5.1-compatible format)
  • Continuous trip CSV  – trips/trip_YYYYMMDD-HHMMSS.csv
                           opened when RPM ≥ 400 || speed ≥ 5 km/h
                           for ≥ ENGINE_START_STREAK consecutive samples,
                           closed after ENGINE_STOP_DELAY seconds of silence.
  • Manual dyno CSV      – logs/dyno_log_YYYYMMDD-HHMMSS.csv
                           toggled via SIGUSR1 (sent by flask_web.py using PID file).

Signal handlers (processed safely in the main loop via threading.Event)
  SIGHUP   – reload lambda_ground_offset_mv from user_setup.json
  SIGUSR1  – toggle manual dyno recording

Does NOT import: pandas, matplotlib, luma, RPi.GPIO, flask.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, IO, Optional, Tuple

# ── pyserial is optional: missing on dev machines ──────────────────────────
try:
    import serial  # type: ignore
except ImportError:
    serial = None  # type: ignore

# ── Ensure src/ is on sys.path so config.py resolves correctly ─────────────
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from config import (
    LOG_DIR,
    SERIAL_BAUD,
    SERIAL_PORT,
    TRIP_LOG_DIR,
    get_full_setup_metadata,
    load_carb_setup,
)

# ══════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════

BOOTSTRAP_VERSION    = "3.0-bootstrap"

# IPC paths
STATE_FILE           = "/tmp/streetdyno_state.json"
STATE_FILE_TMP       = "/tmp/streetdyno_state.json.tmp"
PID_FILE             = "/tmp/streetdyno_logger.pid"

# gpsd
GPS_HOST             = "127.0.0.1"
GPS_PORT             = 2947
GPS_SOCK_TIMEOUT     = 0.1   # s – non-blocking recv
GPS_POLL_INTERVAL    = 0.2   # s – sleep between GPS thread iterations
GPS_STALENESS_LIMIT  = 2.5   # s – beyond this, fix is considered stale

# CSV writing
# fsync every N rows to balance power-safety vs. SD-card wear.
# At 10 Hz: FSYNC_EVERY=10 → ≤ 1 s data loss window, ≈ 1 fsync/s.
FSYNC_EVERY_N        = 10

# Trip lifecycle
ENGINE_START_STREAK  = 5     # consecutive samples before trip opens (~500 ms)
ENGINE_STOP_DELAY    = 30.0  # s of engine silence before trip closes

# State snapshot
STATE_WRITE_INTERVAL = 0.2   # s

# EMA alphas — ONLY for state.json HUD display; raw values go into CSV.
_ALPHA_RPM           = 0.20
_ALPHA_AFR           = 0.15

# Main loop target period
LOG_LOOP_SLEEP       = 0.02  # s → theoretical max 50 Hz, Arduino sends 10 Hz


# ══════════════════════════════════════════════════════════════════════════
# Shared data structures
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class GPSSnapshot:
    lat:       float = 0.0
    lon:       float = 0.0
    alt:       float = 0.0
    speed_kmh: float = 0.0
    fix:       bool  = False
    last_seen: float = 0.0   # time.monotonic() of last valid TPV packet


@dataclass
class SerialState:
    micros:    int   = 0
    rpm:       float = 0.0
    afr:       float = 0.0
    egt:       float = 0.0
    cht:       float = 0.0
    last_seen: float = 0.0   # time.time() of last valid parsed frame


# ══════════════════════════════════════════════════════════════════════════
# GPS reader thread
# ══════════════════════════════════════════════════════════════════════════

class GPSReader(threading.Thread):
    """
    Async gpsd reader daemon thread.

    Updates the shared GPSSnapshot under a Lock. The main loop reads it with
    Lock.acquire(timeout=0) so GPS activity never blocks telemetry processing.
    If the lock is busy the main loop reuses the previously cached snapshot —
    acceptable since GPS updates at ≤ 1 Hz.
    """

    def __init__(self, snapshot: GPSSnapshot, lock: threading.Lock) -> None:
        super().__init__(daemon=True, name="GPSReader")
        self._snapshot = snapshot
        self._lock     = lock
        self._sock: Optional[socket.socket] = None
        self._buf      = b""
        self._running  = True

    def stop(self) -> None:
        self._running = False

    def _connect(self) -> bool:
        try:
            s = socket.create_connection((GPS_HOST, GPS_PORT), timeout=2.0)
            s.settimeout(GPS_SOCK_TIMEOUT)
            s.sendall(b'?WATCH={"enable":true,"json":true}\n')
            self._sock = s
            print("[GPS] Connected to gpsd.", flush=True)
            return True
        except OSError as e:
            print(f"[GPS] gpsd connect failed: {e}", flush=True)
            self._sock = None
            return False

    def _read_once(self) -> None:
        if self._sock is None:
            if not self._connect():
                time.sleep(2.0)
                return
        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                self._sock = None
                return
            self._buf += chunk
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("ascii", "ignore"))
                except json.JSONDecodeError:
                    continue

                if msg.get("class") == "TPV":
                    mode      = msg.get("mode") or 0
                    fix       = mode >= 2
                    speed_ms  = float(msg.get("speed") or 0.0)
                    lat       = float(msg.get("lat") or 0.0)
                    lon       = float(msg.get("lon") or 0.0)
                    alt       = float(
                        msg.get("alt") or msg.get("altHAE") or msg.get("altMSL") or 0.0
                    )
                    snap = GPSSnapshot(
                        lat=lat, lon=lon, alt=alt,
                        speed_kmh=speed_ms * 3.6,
                        fix=fix,
                        last_seen=time.monotonic(),
                    )
                    with self._lock:
                        self._snapshot.__dict__.update(snap.__dict__)

        except socket.timeout:
            pass
        except OSError:
            try:
                if self._sock:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None

    def run(self) -> None:
        while self._running:
            self._read_once()
            time.sleep(GPS_POLL_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════
# CSV helpers
# ══════════════════════════════════════════════════════════════════════════

CSV_COLUMNS = ["Time", "RPM", "AFR", "EGT", "CHT", "Speed", "Lat", "Lon", "Alt", "GPS_Fix"]


def _write_csv_header(f: IO[str], trigger: str) -> None:
    """Write V5.1-compatible comment header then column row."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    setup   = get_full_setup_metadata()
    f.write(f"# STREETDYNO_VERSION: {BOOTSTRAP_VERSION}\n")
    f.write(f"# RECORDED_AT: {now_str}\n")
    f.write(f"# TRIGGER: {trigger}\n")
    f.write(f"# SETUP_JSON: {json.dumps(setup, ensure_ascii=False)}\n")
    f.write(",".join(CSV_COLUMNS) + "\n")
    f.flush()


def _open_csv(directory: str, prefix: str, trigger: str) -> Tuple[str, IO[str]]:
    os.makedirs(directory, exist_ok=True)
    fname = f"{prefix}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    fpath = os.path.join(directory, fname)
    f = open(fpath, "w", encoding="utf-8", newline="")
    _write_csv_header(f, trigger)
    return fpath, f


def _format_csv_row(row: Dict[str, Any]) -> str:
    """Format one telemetry sample as a CSV line (no trailing newline)."""
    return (
        f"{row['ts']:.4f},{row['rpm']:.1f},{row['afr']:.3f},"
        f"{row['egt']:.1f},{row['cht']:.1f},{row['speed']:.2f},"
        f"{row['lat']:.6f},{row['lon']:.6f},{row['alt']:.1f},"
        f"{1 if row['fix'] else 0}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Main logger
# ══════════════════════════════════════════════════════════════════════════

class SimpleLogger:
    """
    Ultra-lightweight blackbox telemetry daemon.
    All state lives here; the main loop is a single-threaded sequential pipeline.
    """

    def __init__(self) -> None:
        # ── GPS shared state ───────────────────────────────────────────────
        self._gps        = GPSSnapshot()     # written by GPSReader thread
        self._gps_cache  = GPSSnapshot()     # last-known-good, read by main loop
        self._gps_lock   = threading.Lock()
        self._gps_reader = GPSReader(self._gps, self._gps_lock)

        # ── Serial state (main-thread only) ───────────────────────────────
        self._serial     = SerialState()
        self._serial_buf = ""

        # ── Arduino hardware timestamp sync (S-03) ────────────────────────
        # Verbatim logic from V5.1 hardware_service.py §341–350.
        self._arduino_sync_time:   float = 0.0
        self._arduino_sync_micros: int   = 0

        # ── Lambda ground offset (reloaded on SIGHUP) ─────────────────────
        self._lambda_offset_mv: float = 0.0
        self._reload_config()

        # ── EMA state — for state.json HUD display ONLY ───────────────────
        self._ema_rpm: float = 0.0
        self._ema_afr: float = 0.0

        # ── Trip CSV ───────────────────────────────────────────────────────
        self._trip_path:         Optional[str]     = None
        self._trip_f:            Optional[IO[str]] = None
        self._trip_row_counter:  int               = 0
        self._engine_streak:     int               = 0
        self._last_engine_active: float            = time.time()

        # ── Dyno CSV (manual toggle) ───────────────────────────────────────
        self._dyno_path:         Optional[str]     = None
        self._dyno_f:            Optional[IO[str]] = None
        self._dyno_active:       bool              = False
        self._dyno_row_counter:  int               = 0

        # ── Inter-loop signal flags (set in signal handlers, consumed in loop)
        self._reload_requested = threading.Event()
        self._dyno_toggle_requested = threading.Event()

        # ── State snapshot throttle ────────────────────────────────────────
        self._last_state_write: float = 0.0

        # ── Register signal handlers ───────────────────────────────────────
        signal.signal(signal.SIGHUP,  self._on_sighup)
        signal.signal(signal.SIGUSR1, self._on_sigusr1)

    # ── Signal handlers ────────────────────────────────────────────────────

    def _on_sighup(self, signum: int, frame: Any) -> None:
        """Schedule config reload. Processed in main loop (signal-safe)."""
        self._reload_requested.set()

    def _on_sigusr1(self, signum: int, frame: Any) -> None:
        """Schedule dyno toggle. Processed in main loop (signal-safe)."""
        self._dyno_toggle_requested.set()

    # ── Config reload ──────────────────────────────────────────────────────

    def _reload_config(self) -> None:
        try:
            setup = load_carb_setup()
            self._lambda_offset_mv = float(setup.get("lambda_ground_offset_mv", 0.0))
        except Exception:
            self._lambda_offset_mv = 0.0

    # ── Serial parsing (verbatim XOR checksum from V5.1 §111-145) ─────────

    def _parse_line(self, line: str) -> bool:
        """
        Parse $MICROS;RPM;AFR;EGT;CHT*XX with XOR checksum validation.
        Identical checksum algorithm to V5.1 hardware_service.py §111-145.
        Returns True only on a valid, complete frame.
        """
        if not (line.startswith("$") and "*" in line):
            return False
        payload, cs_str = line[1:].split("*", 1)

        calc_cs = 0
        for ch in payload:
            calc_cs ^= ord(ch)
        try:
            expected_cs = int(cs_str.strip(), 16)
        except ValueError:
            return False
        if calc_cs != expected_cs:
            return False

        parts = payload.split(";")
        if len(parts) < 4:
            return False
        try:
            self._serial.micros = int(parts[0])
            self._serial.rpm    = float(parts[1])
            raw_afr             = float(parts[2])
            # Lambda ground offset compensation — verbatim from V5.1 §136-138
            if self._lambda_offset_mv != 0.0 and raw_afr > 0.0:
                raw_afr = max(
                    9.0, min(19.5, raw_afr + (self._lambda_offset_mv / 1000.0) * 5.72)
                )
            self._serial.afr    = raw_afr
            self._serial.egt    = float(parts[3])
            self._serial.cht    = float(parts[4]) if len(parts) >= 5 else 0.0
            self._serial.last_seen = time.time()
            return True
        except (ValueError, TypeError):
            return False

    # ── Hardware timestamp (S-03, verbatim from V5.1 §341-350) ───────────

    def _hw_timestamp(self, wall_now: float) -> float:
        """
        Derive sub-millisecond timestamp from Arduino 16 MHz crystal.
        Re-syncs to system clock every 60 s to prevent crystal drift.
        32-bit µs rollover handled by masking with 0xFFFFFFFF.
        """
        if self._serial.micros > 0:
            if (
                self._arduino_sync_micros == 0
                or (wall_now - self._arduino_sync_time) >= 60.0
            ):
                self._arduino_sync_time   = wall_now
                self._arduino_sync_micros = self._serial.micros
                return wall_now
            delta_us = (self._serial.micros - self._arduino_sync_micros) & 0xFFFFFFFF
            return self._arduino_sync_time + (delta_us / 1_000_000.0)
        return wall_now

    # ── GPS safe read (non-blocking) ──────────────────────────────────────

    def _read_gps(self) -> GPSSnapshot:
        """
        Try to refresh the local GPS cache from the shared snapshot.
        Uses Lock.acquire(timeout=0): if the GPS thread holds the lock,
        the stale cache is returned rather than blocking the main loop.
        """
        if self._gps_lock.acquire(timeout=0):
            try:
                self._gps_cache.__dict__.update(self._gps.__dict__)
            finally:
                self._gps_lock.release()
        return self._gps_cache  # always last-known-good

    # ── Staleness guard (mirrors V5.1 §329-334) ───────────────────────────

    @staticmethod
    def _gps_is_fresh(snap: GPSSnapshot) -> bool:
        age = (time.monotonic() - snap.last_seen) if snap.last_seen > 0 else 999.0
        return snap.fix and (age < GPS_STALENESS_LIMIT)

    # ── Trip CSV lifecycle ─────────────────────────────────────────────────

    def _trip_start(self) -> None:
        if self._trip_f is not None:
            return
        self._trip_path, self._trip_f = _open_csv(TRIP_LOG_DIR, "trip", "TRIP")
        self._trip_row_counter = 0
        print(f"[TRIP] Started: {self._trip_path}", flush=True)

    def _trip_stop(self) -> None:
        if self._trip_f is None:
            return
        try:
            os.fsync(self._trip_f.fileno())   # final flush to disk
            self._trip_f.close()
        except Exception:
            pass
        print(f"[TRIP] Closed:  {self._trip_path}", flush=True)
        self._trip_f    = None
        self._trip_path = None

    # ── Dyno CSV lifecycle ────────────────────────────────────────────────

    def _dyno_open(self) -> None:
        self._dyno_path, self._dyno_f = _open_csv(LOG_DIR, "dyno_log", "MANUAL")
        self._dyno_row_counter = 0
        self._dyno_active      = True
        print(f"[DYNO] Manual recording started: {self._dyno_path}", flush=True)

    def _dyno_close(self) -> None:
        if self._dyno_f is None:
            return
        try:
            os.fsync(self._dyno_f.fileno())
            self._dyno_f.close()
        except Exception:
            pass
        print(f"[DYNO] Manual recording stopped: {self._dyno_path}", flush=True)
        self._dyno_f      = None
        self._dyno_path   = None
        self._dyno_active = False

    def _dyno_toggle(self) -> None:
        if not self._dyno_active:
            self._dyno_open()
        else:
            self._dyno_close()

    # ── Power-safe CSV write ───────────────────────────────────────────────

    def _write_to(
        self, f: IO[str], row: Dict[str, Any], counter_attr: str
    ) -> bool:
        """
        Write one row to an open CSV file.
        flush() on every write; fsync() every FSYNC_EVERY_N rows.
        Returns False on I/O error (caller should close the file).
        """
        try:
            f.write(_format_csv_row(row) + "\n")
            f.flush()
            count = getattr(self, counter_attr) + 1
            setattr(self, counter_attr, count)
            if count >= FSYNC_EVERY_N:
                os.fsync(f.fileno())
                setattr(self, counter_attr, 0)
            return True
        except OSError as e:
            print(f"[CSV] Write error on {f.name}: {e}", flush=True)
            return False

    # ── Atomic state.json snapshot ─────────────────────────────────────────

    def _write_state(self, row: Dict[str, Any]) -> None:
        """
        Update EMA accumulators and write atomic JSON state for Flask HUD.
        EMA is computed here exclusively; raw values go into CSV.
        """
        raw_rpm = row["rpm"]
        raw_afr = row["afr"]

        if raw_rpm > 0.0:
            self._ema_rpm = _ALPHA_RPM * raw_rpm + (1.0 - _ALPHA_RPM) * self._ema_rpm
        else:
            self._ema_rpm = max(0.0, self._ema_rpm * 0.8)

        if raw_afr > 0.0:
            self._ema_afr = _ALPHA_AFR * raw_afr + (1.0 - _ALPHA_AFR) * self._ema_afr

        state: Dict[str, Any] = {
            "ts":          row["ts"],
            "rpm":         round(self._ema_rpm, 0),
            "rpm_raw":     round(raw_rpm, 1),
            "afr":         round(self._ema_afr, 2) if self._ema_afr > 0.0 else round(raw_afr, 2),
            "egt":         round(row["egt"], 1),
            "cht":         round(row["cht"], 1),
            "speed":       round(row["speed"], 1),
            "lat":         row["lat"],
            "lon":         row["lon"],
            "alt":         round(row["alt"], 1),
            "fix":         row["fix"],
            "trip_active": self._trip_f is not None,
            "dyno_active": self._dyno_active,
            "trip_file":   os.path.basename(self._trip_path) if self._trip_path else None,
            "dyno_file":   os.path.basename(self._dyno_path) if self._dyno_path else None,
            "status": (
                "DYNO" if self._dyno_active
                else ("TRIP" if self._trip_f else "IDLE")
            ),
        }
        try:
            with open(STATE_FILE_TMP, "w", encoding="utf-8") as sf:
                json.dump(state, sf)
            os.replace(STATE_FILE_TMP, STATE_FILE)  # atomic rename
        except OSError:
            pass

    # ── Main run loop ──────────────────────────────────────────────────────

    def run(self) -> None:
        # Write PID file so flask_web.py can send SIGUSR1 for dyno toggle
        try:
            with open(PID_FILE, "w") as pf:
                pf.write(str(os.getpid()))
        except OSError as e:
            print(f"[LOGGER] Warning: could not write PID file: {e}", flush=True)

        self._gps_reader.start()

        print("=" * 60, flush=True)
        print(f"🚀 StreetDyno 2.5 – simple_logger  PID={os.getpid()}", flush=True)
        print(f"   Serial:  {SERIAL_PORT} @ {SERIAL_BAUD} baud", flush=True)
        print(f"   Trips:   {TRIP_LOG_DIR}", flush=True)
        print(f"   Dynos:   {LOG_DIR}", flush=True)
        print(f"   State:   {STATE_FILE}", flush=True)
        print(f"   Signals: SIGHUP=reload-config  SIGUSR1=toggle-dyno", flush=True)
        print("=" * 60, flush=True)

        ser: Optional[Any] = None  # serial.Serial instance

        try:
            while True:
                wall_now = time.time()

                # ── Consume pending signals (safe deferred handling) ───────
                if self._reload_requested.is_set():
                    self._reload_requested.clear()
                    self._reload_config()
                    print("[CFG] SIGHUP: user_setup.json reloaded.", flush=True)

                if self._dyno_toggle_requested.is_set():
                    self._dyno_toggle_requested.clear()
                    self._dyno_toggle()

                # ── Serial: connect if needed ──────────────────────────────
                if ser is None and serial is not None:
                    if os.path.exists(SERIAL_PORT):
                        try:
                            ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
                            ser.reset_input_buffer()
                            print(f"[SERIAL] Connected on {SERIAL_PORT}", flush=True)
                        except Exception as e:
                            print(f"[SERIAL] Connect failed: {e}", flush=True)
                            ser = None
                            time.sleep(1.0)
                            continue
                    else:
                        time.sleep(1.0)
                        continue

                # ── Serial: drain available bytes (non-blocking) ───────────
                if ser is not None:
                    try:
                        waiting = ser.in_waiting
                        if waiting > 0:
                            chunk = ser.read(waiting).decode("ascii", errors="ignore")
                            self._serial_buf += chunk
                            while "\n" in self._serial_buf:
                                line, self._serial_buf = self._serial_buf.split("\n", 1)
                                line = line.strip()
                                if line.startswith("$") and "*" in line:
                                    self._parse_line(line)
                    except Exception as e:
                        print(f"[SERIAL] Read error: {e}", flush=True)
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = None

                # ── Stale serial timeout: zero sensors after >1 s silence ──
                if wall_now - self._serial.last_seen > 1.0:
                    self._serial.rpm = 0.0
                    self._serial.afr = 0.0
                    self._serial.egt = 0.0
                    self._serial.cht = 0.0

                # ── Hardware timestamp (S-03) ──────────────────────────────
                ts = self._hw_timestamp(wall_now)

                # ── GPS snapshot (non-blocking) ────────────────────────────
                gps   = self._read_gps()
                fresh = self._gps_is_fresh(gps)
                fix   = fresh
                speed = gps.speed_kmh if fresh else 0.0

                # ── Compose sample row ─────────────────────────────────────
                row: Dict[str, Any] = {
                    "ts":    ts,
                    "rpm":   self._serial.rpm,
                    "afr":   self._serial.afr,
                    "egt":   self._serial.egt,
                    "cht":   self._serial.cht,
                    "speed": speed,
                    "lat":   gps.lat,
                    "lon":   gps.lon,
                    "alt":   gps.alt,
                    "fix":   fix,
                }

                # ── Trip lifecycle ─────────────────────────────────────────
                is_engine_active = (row["rpm"] >= 400.0) or (row["speed"] >= 5.0)
                if is_engine_active:
                    self._last_engine_active = wall_now
                    if self._trip_f is None:
                        self._engine_streak += 1
                        if self._engine_streak >= ENGINE_START_STREAK:
                            self._trip_start()
                            self._engine_streak = 0
                    else:
                        self._engine_streak = 0
                else:
                    self._engine_streak = 0
                    if self._trip_f is not None:
                        if (wall_now - self._last_engine_active) >= ENGINE_STOP_DELAY:
                            self._trip_stop()

                # ── Write to trip CSV ──────────────────────────────────────
                if self._trip_f is not None:
                    ok = self._write_to(self._trip_f, row, "_trip_row_counter")
                    if not ok:
                        self._trip_stop()

                # ── Write to dyno CSV ──────────────────────────────────────
                if self._dyno_active and self._dyno_f is not None:
                    ok = self._write_to(self._dyno_f, row, "_dyno_row_counter")
                    if not ok:
                        self._dyno_close()

                # ── Atomic state.json snapshot (every STATE_WRITE_INTERVAL) ─
                if wall_now - self._last_state_write >= STATE_WRITE_INTERVAL:
                    self._last_state_write = wall_now
                    self._write_state(row)

                time.sleep(LOG_LOOP_SLEEP)

        except KeyboardInterrupt:
            print("\n[LOGGER] KeyboardInterrupt – shutting down.", flush=True)
        finally:
            self._shutdown(ser)

    def _shutdown(self, ser: Any) -> None:
        """Graceful shutdown: flush all open files, remove PID file."""
        self._gps_reader.stop()
        self._trip_stop()
        if self._dyno_active:
            self._dyno_close()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        try:
            os.unlink(PID_FILE)
        except Exception:
            pass
        print("[LOGGER] Shutdown complete.", flush=True)


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(TRIP_LOG_DIR, exist_ok=True)
    SimpleLogger().run()
