#!/usr/bin/env python3
"""
StreetDyno 2.0 – Phase 2.5 Bootstrap Web Server
================================================
Single responsibility: serve the web UI and JSON API.
Reads telemetry exclusively from /tmp/streetdyno_state.json (written
by simple_logger.py). Never touches /dev/ttyUSB0 or any hardware lock.

Endpoints
---------
  /  &  /hud            Live HUD (reuses src/templates/hud.html)
  /api/telemetry        Current telemetry snapshot (JSON)
  /api/toggle_dyno      Send SIGUSR1 to simple_logger.py (PID file IPC)
  /logs                 Dyno CSV file listing + download
  /logs/download/<f>    Direct CSV download
  /trips                Trip CSV file listing + download
  /trips/download/<f>   Direct CSV download

Heavy analysis routes (/analyze, /dyno_sheet, etc.) are intentionally
absent and will be added modularly in Phase 3.

Independence contract
---------------------
  • Flask crash / restart  →  simple_logger.py continues uninterrupted
  • simple_logger.py not running  →  Flask returns {"status":"LOGGER_OFFLINE"}
  • No shared locks, no shared state beyond the /tmp JSON file
"""

from __future__ import annotations

import glob
import json
import os
import signal
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint,
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

# ── Path resolution ────────────────────────────────────────────────────────
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from config import LOG_DIR, TRIP_LOG_DIR

# ══════════════════════════════════════════════════════════════════════════
# IPC paths (must match simple_logger.py exactly)
# ══════════════════════════════════════════════════════════════════════════

STATE_FILE = "/tmp/streetdyno_state.json"
PID_FILE   = "/tmp/streetdyno_logger.pid"

# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _read_state() -> Dict[str, Any]:
    """
    Read the atomic JSON state written by simple_logger.py.
    Returns a safe default dict if the file is absent or unreadable
    (e.g., logger not yet started or just restarting).
    """
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {
            "status": "LOGGER_OFFLINE",
            "rpm": 0, "afr": 0.0, "egt": 0, "cht": 0,
            "speed": 0.0, "lat": 0.0, "lon": 0.0, "alt": 0.0,
            "fix": False, "trip_active": False, "dyno_active": False,
            "trip_file": None, "dyno_file": None,
        }


def _read_logger_pid() -> Optional[int]:
    """Read the PID written by simple_logger.py, or None if unavailable."""
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _send_sigusr1() -> bool:
    """
    Send SIGUSR1 to simple_logger.py to toggle dyno recording.
    Returns True on success, False if PID is unavailable or process is dead.
    """
    pid = _read_logger_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGUSR1)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _list_csv_files(directory: str, prefix: str = "") -> List[Dict[str, Any]]:
    """
    List CSV files in a directory, sorted newest-first by filename timestamp.
    Returns lightweight metadata dicts (no pandas, no file parsing).
    """
    pattern = os.path.join(directory, f"{prefix}*.csv")
    files = sorted(glob.glob(pattern), reverse=True)  # filename sort = time sort
    result = []
    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            size_kb = round(os.path.getsize(fpath) / 1024, 1)
            mtime   = datetime.fromtimestamp(os.path.getmtime(fpath))
            mtime_s = mtime.strftime("%d.%m.%Y %H:%M")
        except OSError:
            size_kb = 0.0
            mtime_s = "—"

        # Extract timestamp from filename (dyno_log_YYYYMMDD-HHMMSS.csv or trip_YYYYMMDD-HHMMSS.csv)
        display_dt = "—"
        try:
            parts = fname.replace(".csv", "").split("_")
            # last part is YYYYMMDD-HHMMSS
            ts_part = parts[-1]
            dt = datetime.strptime(ts_part, "%Y%m%d-%H%M%S")
            display_dt = dt.strftime("%d.%m.%Y %H:%M:%S")
        except (ValueError, IndexError):
            display_dt = mtime_s

        result.append({
            "filename":   fname,
            "size_kb":    size_kb,
            "created_at": display_dt,
            "mtime":      mtime_s,
        })
    return result


# ══════════════════════════════════════════════════════════════════════════
# Blueprint
# ══════════════════════════════════════════════════════════════════════════

bootstrap_bp = Blueprint(
    "bootstrap_bp",
    __name__,
    template_folder=os.path.join(_SRC_DIR, "templates"),
    static_folder=os.path.join(_SRC_DIR, "static"),
)

_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma":        "no-cache",
    "Expires":       "0",
}


# ── / and /hud ─────────────────────────────────────────────────────────────

@bootstrap_bp.route("/")
@bootstrap_bp.route("/hud")
def live_hud() -> Response:
    """
    Render the full-screen Live HUD.
    Reuses the existing src/templates/hud.html without modification.
    The template polls /api/telemetry and /api/toggle_logging — those
    names are shimmed below so the existing JS works without changes.
    """
    resp = Response(render_template("hud.html"))
    for k, v in _NO_CACHE.items():
        resp.headers[k] = v
    return resp


# ── /api/telemetry ─────────────────────────────────────────────────────────

@bootstrap_bp.route("/api/telemetry")
def api_telemetry() -> Response:
    """
    Return current telemetry from state.json.
    Callers should handle {"status":"LOGGER_OFFLINE"} gracefully.
    """
    state = _read_state()
    resp  = jsonify(state)
    for k, v in _NO_CACHE.items():
        resp.headers[k] = v
    return resp


# ── /api/data (HUD compat shim) ────────────────────────────────────────────

@bootstrap_bp.route("/api/data")
def api_data_shim() -> Response:
    """
    Shim: the existing hud.html polls /api/data.
    Normalise the bootstrap state dict to the field names the HUD JS expects.
    """
    state = _read_state()
    # HUD JS reads: rpm, speed, afr, egt, cht, fix, status, is_logging
    payload = {
        "rpm":        state.get("rpm", 0),
        "speed":      state.get("speed", 0.0),
        "afr":        state.get("afr", 0.0),
        "egt":        state.get("egt", 0),
        "cht":        state.get("cht", 0),
        "fix":        state.get("fix", False),
        "status":     state.get("status", "IDLE"),
        "is_logging": state.get("dyno_active", False),
        "trip_active":state.get("trip_active", False),
        # pass through extras
        "lat":        state.get("lat", 0.0),
        "lon":        state.get("lon", 0.0),
        "alt":        state.get("alt", 0.0),
        "dyno_file":  state.get("dyno_file"),
        "trip_file":  state.get("trip_file"),
    }
    resp = jsonify(payload)
    for k, v in _NO_CACHE.items():
        resp.headers[k] = v
    return resp


# ── /api/toggle_dyno ───────────────────────────────────────────────────────

@bootstrap_bp.route("/api/toggle_dyno", methods=["GET", "POST"])
def api_toggle_dyno() -> Response:
    """
    Toggle manual dyno recording in simple_logger.py via SIGUSR1.
    The logger updates state.json atomically; the caller polls /api/telemetry
    for the new dyno_active flag (appears within STATE_WRITE_INTERVAL = 200 ms).
    """
    ok = _send_sigusr1()
    if not ok:
        resp = jsonify({
            "success":    False,
            "error":      "Logger not running or PID file missing",
            "dyno_active": False,
        })
        resp.status_code = 503
        return resp

    # After sending the signal, optimistically read state (may still be stale)
    state = _read_state()
    return jsonify({
        "success":    True,
        "dyno_active": state.get("dyno_active", False),
        "is_logging":  state.get("dyno_active", False),  # HUD compat
    })


# ── /api/toggle_logging (HUD compat shim) ─────────────────────────────────

@bootstrap_bp.route("/api/toggle_logging")
def api_toggle_logging_shim() -> Response:
    """
    Shim: existing hud.html calls /api/toggle_logging.
    Delegates to the dyno toggle and normalises response for the HUD JS.
    """
    ok = _send_sigusr1()
    state = _read_state()
    dyno_active = state.get("dyno_active", False)
    return jsonify({
        "is_logging": dyno_active,
        "success":    ok,
        "status":     state.get("status", "IDLE"),
    })


# ── /logs ──────────────────────────────────────────────────────────────────

@bootstrap_bp.route("/logs")
def log_archive() -> str:
    """
    List dyno pull CSV files (logs/dyno_log_*.csv), newest first.
    Renders src/templates/logs.html with a lightweight metadata dict
    compatible with the existing template variables.
    """
    dyno_files = _list_csv_files(LOG_DIR, prefix="dyno_log_")
    # Wrap into the dict shape logs.html expects
    logs_data = [
        {
            "filename":   f["filename"],
            "size_kb":    f["size_kb"],
            "created_at": f["created_at"],
            "mtime":      f["mtime"],
            "setup_badge": "Bootstrap 2.5",   # no embedded metadata in minimal logger
            "setup_meta":  {},
        }
        for f in dyno_files
    ]
    return render_template("logs.html", logs=logs_data)


@bootstrap_bp.route("/logs/download/<path:filename>")
def download_log(filename: str) -> Response:
    """Serve a dyno CSV for direct download. Path traversal is prevented by send_from_directory."""
    return send_from_directory(
        LOG_DIR, filename,
        as_attachment=True,
        mimetype="text/csv",
    )


# ── /trips ─────────────────────────────────────────────────────────────────

@bootstrap_bp.route("/trips")
def trips_list() -> str:
    """
    List trip CSV files (logs/trips/trip_*.csv), newest first.
    Renders src/templates/trips.html with lightweight metadata.
    """
    trip_files = _list_csv_files(TRIP_LOG_DIR, prefix="trip_")
    trips_data = [
        {
            "filename":    f["filename"],
            "size_kb":     f["size_kb"],
            "created_at":  f["created_at"],
            "mtime":       f["mtime"],
            "duration_min": round((f["size_kb"] * 1024 / 80) * 0.1 / 60, 1),  # ~80 bytes/row @ 10Hz
            "distance_km": 0.0,   # requires CSV parsing; deferred to Phase 3
            "setup_badge": "Bootstrap 2.5",
            "setup_meta":  {},
        }
        for f in trip_files
    ]
    return render_template("trips.html", trips=trips_data)


@bootstrap_bp.route("/trips/download/<path:filename>")
def download_trip(filename: str) -> Response:
    """Serve a trip CSV for direct download."""
    return send_from_directory(
        TRIP_LOG_DIR, filename,
        as_attachment=True,
        mimetype="text/csv",
    )


# ══════════════════════════════════════════════════════════════════════════
# Flask application factory
# ══════════════════════════════════════════════════════════════════════════

def create_app() -> Flask:
    """
    Build a minimal Flask app that registers only the bootstrap blueprint.
    The existing src/templates/ and src/static/ directories are reused.
    """
    template_folder = os.path.join(_SRC_DIR, "templates")
    static_folder   = os.path.join(_SRC_DIR, "static")

    app = Flask(
        __name__,
        template_folder=template_folder,
        static_folder=static_folder,
    )
    app.register_blueprint(bootstrap_bp)
    return app


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(TRIP_LOG_DIR, exist_ok=True)

    app = create_app()

    print("=" * 60, flush=True)
    print("🌐 StreetDyno 2.5 – flask_web.py", flush=True)
    print("   http://0.0.0.0:8080", flush=True)
    print(f"   State file: {STATE_FILE}", flush=True)
    print(f"   PID file:   {PID_FILE}", flush=True)
    print("   Routes: /hud  /api/telemetry  /api/toggle_dyno  /logs  /trips", flush=True)
    print("=" * 60, flush=True)

    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
