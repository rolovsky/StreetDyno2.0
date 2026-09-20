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

from config import (
    LOG_DIR,
    TRIP_LOG_DIR,
    AUDIT_DIR,
    FUEL_STOICHIOMETRY,
    SLIDE_TYPES,
    INTAKE_TYPES,
    AIRBOX_TYPES,
    EMULSION_TUBES,
    STANDARD_HLKD_VALUES,
    load_carb_setup,
    save_carb_setup,
)

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


# ── /tuning ────────────────────────────────────────────────────────────────

def _send_sighup() -> bool:
    """Send SIGHUP to simple_logger.py to reload lambda_ground_offset_mv."""
    pid = _read_logger_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _build_weather_comp(main_jet_hd: int, temp_c: float, pressure_hpa: float) -> Dict[str, Any]:
    """
    Lightweight inline weather correction for the tuning page.
    Mirrors calculate_weather_corrected_main_jet() from jetting_advisor.py
    without importing pandas/matplotlib. Uses Relative Air Density (RAD) physics:
    HD_corr = round(HD_base * sqrt(RAD)).
    """
    import math
    base = float(main_jet_hd)
    t_ref = 293.15
    t_kelvin = max(233.15, float(temp_c) + 273.15)
    p_ref = 1013.25
    p_actual = max(700.0, min(1100.0, float(pressure_hpa)))
    rad = (p_actual / p_ref) * (t_ref / t_kelvin)
    sqrt_rad = math.sqrt(rad)
    recommended_hd = int(round(base * sqrt_rad))
    delta_hd = recommended_hd - int(round(base))
    return {
        "base_hd": int(round(base)),
        "recommended_hd": recommended_hd,
        "delta_hd": delta_hd,
        "rad": round(rad, 4),
        "rad_pct": round(rad * 100.0, 1),
        "temp_c": round(float(temp_c), 1),
        "pressure_hpa": round(float(pressure_hpa), 1),
        "sqrt_rad": round(sqrt_rad, 4),
        "factor": round(sqrt_rad, 4),
    }


@bootstrap_bp.route("/tuning", methods=["GET", "POST"])
def tuning_dashboard() -> Any:
    """
    GET  – render tuning.html with current carburetor setup + weather correction.
    POST – persist changed setup to user_setup.json, then SIGHUP the logger
           so it reloads lambda_ground_offset_mv without a restart.

    The heavy jetting zone analysis (requires pandas + a dyno CSV) is skipped
    in Bootstrap mode; zone_cards_html is an informational placeholder.
    """
    if request.method == "POST":
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or request.form.to_dict()
        if data:
            # Coerce integer fields — same logic as V5.1 api_update_carb_setup
            cleaned: Dict[str, Any] = {}
            for k, v in data.items():
                if k in ("main_jet_hd", "air_corrector_hlkd"):
                    try:
                        cleaned[k] = int(float(v))
                    except (ValueError, TypeError):
                        cleaned[k] = v
                else:
                    cleaned[k] = str(v)
            ok = save_carb_setup(cleaned)
            if ok:
                _send_sighup()   # logger reloads lambda_ground_offset_mv immediately
                return jsonify({"status": "success", "setup": load_carb_setup()})
            return jsonify({"status": "error", "message": "Fehler beim Speichern"}), 500
        return jsonify({"status": "error", "message": "Keine Daten empfangen"}), 400

    # GET ──────────────────────────────────────────────────────────────────
    carb = load_carb_setup()

    try:
        temp_c = float(request.args.get("temp", 20.0))
    except (ValueError, TypeError):
        temp_c = 20.0
    try:
        pressure_hpa = float(request.args.get("pressure", 1013.25))
    except (ValueError, TypeError):
        pressure_hpa = 1013.25

    weather_comp = _build_weather_comp(
        int(carb.get("main_jet_hd", 125)), temp_c, pressure_hpa
    )

    # Zone analysis requires pandas + a dyno CSV — deferred to Phase 3.
    zone_cards_html = (
        "<div style='color:#888; font-size:0.85rem; padding:8px 0;'>"
        "⚡ Bootstrap-Modus: Live-Zonenanalyse startet nach einem Dyno-Pull. "
        "Starte einen Pull auf dem HUD — die Auswertung erscheint hier automatisch."
        "</div>"
    )
    latest_file = None
    dyno_files = sorted(glob.glob(os.path.join(LOG_DIR, "dyno_log_*.csv")), reverse=True)
    if dyno_files:
        latest_file = os.path.basename(dyno_files[0])

    return render_template(
        "tuning.html",
        carb=carb,
        analysis=None,
        zone_cards_html=zone_cards_html,
        slide_types=SLIDE_TYPES,
        SLIDE_TYPES=SLIDE_TYPES,
        SLIDE_CUTAWAY_PROFILES=SLIDE_TYPES,
        intake_types=INTAKE_TYPES,
        INTAKE_TYPES=INTAKE_TYPES,
        INTAKE_VENTURI_PROFILES=INTAKE_TYPES,
        airbox_types=AIRBOX_TYPES,
        AIRBOX_TYPES=AIRBOX_TYPES,
        fuel_types=FUEL_STOICHIOMETRY,
        FUEL_STOICHIOMETRY=FUEL_STOICHIOMETRY,
        emulsion_tubes=EMULSION_TUBES,
        EMULSION_TUBES=EMULSION_TUBES,
        standard_hlkd_values=STANDARD_HLKD_VALUES,
        STANDARD_HLKD_VALUES=STANDARD_HLKD_VALUES,
        weather_comp=weather_comp,
        temp_param=temp_c,
        pressure_param=pressure_hpa,
        latest_file=latest_file,
    )


# ── /api/update_carb_setup (compat shim — tuning.html POSTs here via JS) ──

@bootstrap_bp.route("/api/update_carb_setup", methods=["GET", "POST"])
def api_update_carb_setup() -> Any:
    """
    V5.1-compatible endpoint. tuning.html's JS calls this via fetch().
    Saves the carb setup and sends SIGHUP to the logger.
    """
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
    else:
        data = request.args.to_dict()

    if not data:
        return jsonify({"status": "error", "message": "Keine Daten empfangen"}), 400

    cleaned: Dict[str, Any] = {}
    for k, v in data.items():
        if k in ("main_jet_hd", "air_corrector_hlkd"):
            try:
                cleaned[k] = int(float(v))
            except (ValueError, TypeError):
                cleaned[k] = v
        else:
            cleaned[k] = str(v)

    ok = save_carb_setup(cleaned)
    if ok:
        _send_sighup()
        return jsonify({"status": "success", "setup": load_carb_setup()})
    return jsonify({"status": "error", "message": "Fehler beim Speichern"}), 500


# ── /diagnostics ───────────────────────────────────────────────────────────

def _list_audit_reports() -> List[Dict[str, Any]]:
    """
    List audit JSON reports from AUDIT_DIR without importing audit_manager.py
    (which would drag in pandas). Returns same dict shape as list_audit_reports().
    """
    reports: List[Dict[str, Any]] = []
    pattern = os.path.join(AUDIT_DIR, "audit_*.json")
    for fpath in sorted(glob.glob(pattern), reverse=True):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                d = json.load(f)
            reports.append({
                "recorded_at":  d.get("recorded_at", os.path.basename(fpath)),
                "delta_u_mv":   d.get("delta_u_mv", 0.0),
                "afr_impact":   d.get("afr_impact", 0.0),
                "status_label": d.get("status_label", "—"),
                "filename":     os.path.basename(fpath),
            })
        except Exception:
            continue
    return reports


@bootstrap_bp.route("/diagnostics")
def diagnostics() -> str:
    """
    Render the hardware diagnostics + Sternmasse-Audit cockpit.
    Passes carb setup, current lambda offset, and past audit reports.
    Live telemetry is polled client-side via /api/diagnostics/live.
    """
    carb = load_carb_setup()
    offset_mv = float(carb.get("lambda_ground_offset_mv", 0.0))
    past_reports = _list_audit_reports()
    return render_template(
        "diagnostics.html",
        carb=carb,
        offset_mv=offset_mv,
        past_reports=past_reports,
    )


# ── /api/diagnostics/live (called by diagnostics.html JS every 500 ms) ────

@bootstrap_bp.route("/api/diagnostics/live")
def api_diagnostics_live() -> Response:
    """
    Return live telemetry from state.json, enriched with reconstructed A0
    voltage (inline formula — no pandas dependency).
    """
    import math
    state = _read_state()
    afr   = float(state.get("afr", 0.0))
    rpm   = float(state.get("rpm", 0.0))
    egt   = float(state.get("egt", 0.0))
    cht   = float(state.get("cht", 0.0))
    speed = float(state.get("speed", 0.0))
    status = state.get("status", "IDLE")

    # Reconstruct A0 voltage from AFR: mirrors reconstruct_afr_voltage() in audit_manager.py.
    # Koso AFR sensor: V_A0 = (AFR / 20.0) * 5.0 V  (linear 0–20 AFR → 0–5 V)
    v_a0 = round((afr / 20.0) * 5.0, 3) if afr > 0.0 else 0.0

    carb = load_carb_setup()
    offset_mv = float(carb.get("lambda_ground_offset_mv", 0.0))

    import time as _time
    resp = jsonify({
        "rpm":        rpm,
        "afr":        afr,
        "voltage_a0": v_a0,
        "egt":        egt,
        "cht":        cht,
        "speed":      speed,
        "offset_mv":  offset_mv,
        "status":     status,
        "timestamp":  _time.time(),
        "fix":        state.get("fix", False),
        "lat":        state.get("lat", 0.0),
        "lon":        state.get("lon", 0.0),
        "alt":        state.get("alt", 0.0),
    })
    for k, v in _NO_CACHE.items():
        resp.headers[k] = v
    return resp


# ── /api/diagnostics/save_audit ────────────────────────────────────────────

@bootstrap_bp.route("/api/diagnostics/save_audit", methods=["POST"])
def api_save_audit() -> Response:
    """
    Persist a completed Sternmasse-Audit report to AUDIT_DIR and optionally
    apply the measured ground offset to user_setup.json + SIGHUP the logger.
    """
    import time as _time
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        if not data:
            return jsonify({"status": "error", "message": "Keine Audit-Daten übermittelt"}), 400

        delta_u_mv   = float(data.get("delta_u_mv", 0.0))
        apply_comp   = str(data.get("apply_compensation", "false")).lower() in ("true", "1", "yes")

        # Evaluate severity — mirrors evaluate_ground_offset() in audit_manager.py
        abs_mv = abs(delta_u_mv)
        if abs_mv < 5:
            status_label = "OK"
        elif abs_mv < 15:
            status_label = "LEICHT VERSETZT"
        elif abs_mv < 30:
            status_label = "MODERAT VERSETZT"
        else:
            status_label = "STARK VERSETZT"

        afr_impact = round((delta_u_mv / 1000.0) * 5.72, 3)

        report = {
            "recorded_at":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "delta_u_mv":         delta_u_mv,
            "afr_impact":         afr_impact,
            "status_label":       status_label,
            "apply_compensation": apply_comp,
            **{k: v for k, v in data.items() if k not in ("delta_u_mv", "apply_compensation")},
        }

        os.makedirs(AUDIT_DIR, exist_ok=True)
        ts_str  = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path = os.path.join(AUDIT_DIR, f"audit_{ts_str}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        if apply_comp and delta_u_mv != 0.0:
            save_carb_setup({"lambda_ground_offset_mv": delta_u_mv})
            _send_sighup()   # logger picks up new offset immediately

        return jsonify({
            "status":     "success",
            "message":    f"Audit gespeichert ({status_label})",
            "evaluation": {"status_label": status_label, "afr_impact": afr_impact},
            "json_file":  os.path.basename(json_path),
            "md_file":    "",   # Markdown export deferred to Phase 3
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── /api/diagnostics/history ───────────────────────────────────────────────

@bootstrap_bp.route("/api/diagnostics/history")
def api_diagnostics_history() -> Response:
    """Return list of past audit reports (same shape as V5.1)."""
    return jsonify({"status": "success", "reports": _list_audit_reports()})


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
    os.makedirs(AUDIT_DIR, exist_ok=True)

    app = create_app()

    print("=" * 60, flush=True)
    print("🌐 StreetDyno 2.5 – flask_web.py", flush=True)
    print("   http://0.0.0.0:8080", flush=True)
    print(f"   State file: {STATE_FILE}", flush=True)
    print(f"   PID file:   {PID_FILE}", flush=True)
    print("   Routes: /hud /tuning /diagnostics /logs /trips", flush=True)
    print("   APIs:   /api/telemetry /api/toggle_dyno /api/update_carb_setup", flush=True)
    print("           /api/diagnostics/live /api/diagnostics/save_audit", flush=True)
    print("=" * 60, flush=True)

    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
