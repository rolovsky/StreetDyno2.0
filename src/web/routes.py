"""
StreetDyno 2.0 - Web Routing & JSON API Blueprint
Contains all web endpoints, Jinja2 template rendering, and JSON telemetry endpoints.
"""

from __future__ import annotations
import os
import glob
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
from flask import (
    Blueprint,
    render_template,
    jsonify,
    request,
    send_from_directory,
    current_app,
    Response
)

from config import (
    LOG_DIR,
    PLOT_DIR,
    load_carb_setup,
    save_carb_setup,
    DEFAULT_CARB_SETUP,
    FUEL_STOICHIOMETRY,
    SLIDE_TYPES,
    INTAKE_TYPES,
    AIRBOX_TYPES
)
from data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    plot_telemetry,
    calculate_road_slope_percent,
    calculate_weather_correction_factor
)
from data.jetting_advisor import analyze_carb_jetting

dyno_bp = Blueprint('dyno_bp', __name__)


def get_hw_service():
    """Retrieves the active HardwareService from Flask app context."""
    return current_app.config.get('HW_SERVICE')


@dyno_bp.route('/')
def live_hud() -> str:
    """Renders the high-contrast OLED Live Cockpit HUD."""
    resp = Response(render_template('hud.html'))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@dyno_bp.route('/logs')
def log_archive() -> str:
    """Lists all recorded CSV log files with multi-select comparison."""
    files = sorted(glob.glob(os.path.join(LOG_DIR, '*.csv')), key=os.path.getmtime, reverse=True)
    logs_data = []
    for f in files:
        fname = os.path.basename(f)
        size_kb = round(os.path.getsize(f) / 1024, 1)
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d.%m.%Y %H:%M')
        logs_data.append({
            "filename": fname,
            "size_kb": size_kb,
            "mtime": mtime
        })
    return render_template('logs.html', logs=logs_data)


@dyno_bp.route('/analyze')
def analyze_run() -> str:
    """Analyzes a single dyno pull with slope and weather normalization."""
    fname = request.args.get('file')
    if not fname:
        return "Keine Datei ausgewählt."

    fpath = os.path.join(LOG_DIR, fname)
    if not os.path.exists(fpath):
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Datei nicht gefunden: {fname}</h3><br><a href='/logs'>Zurück</a></body>"

    slope_param = request.args.get('slope', 'auto')
    try:
        temp_param = float(request.args.get('temp', 20.0))
    except (ValueError, TypeError):
        temp_param = 20.0

    try:
        pressure_param = float(request.args.get('pressure', 1013.25))
    except (ValueError, TypeError):
        pressure_param = 1013.25

    norm_param = request.args.get('norm', 'DIN70020')

    try:
        df = pd.read_csv(fpath)
        df.columns = [c.strip() for c in df.columns]

        required_cols = ['Time', 'RPM', 'AFR', 'EGT', 'Speed_kmh']
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehlende Spalten in CSV: {missing_cols}</h3><br><a href='/logs'>Zurück</a></body>"

        df = clean_egt_data(df)
        df = calculate_telemetry_metrics(
            df,
            slope_percent=slope_param,
            temp_c=temp_param,
            pressure_hpa=pressure_param,
            norm_standard=norm_param
        )

        trimmed_df, detected = detect_dyno_pull(
            df,
            min_rpm=2800.0,
            min_duration_sec=0.8,
            drop_threshold=400.0,
            slope_percent=slope_param,
            temp_c=temp_param,
            pressure_hpa=pressure_param,
            norm_standard=norm_param
        )

        detected_gear = int(trimmed_df.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed_df.columns else 3
        title_suffix = f" (🎯 {detected_gear}. Gang)" if detected else " (Gesamtes Log)"

        # Generate P4 Plot
        pname = f"p_{os.path.splitext(fname)[0]}.png"
        plot_path = os.path.join(PLOT_DIR, pname)
        plot_telemetry(trimmed_df, title_suffix, plot_path)

        # Peak Performance Metrics
        max_ps = float(trimmed_df['PS'].max()) if 'PS' in trimmed_df.columns else 0.0
        ps_raw = float(trimmed_df['PS_Raw'].max()) if 'PS_Raw' in trimmed_df.columns else max_ps
        max_nm = float(trimmed_df['Nm'].max()) if 'Nm' in trimmed_df.columns else 0.0
        max_egt = float(trimmed_df['EGT_cleaned'].max()) if 'EGT_cleaned' in trimmed_df.columns else 0.0
        avg_afr = float(trimmed_df['AFR'].mean()) if 'AFR' in trimmed_df.columns else 0.0

        peak_ps_idx = trimmed_df['PS'].idxmax() if 'PS' in trimmed_df.columns else None
        peak_ps_rpm = float(trimmed_df.loc[peak_ps_idx, 'RPM_smoothed']) if (peak_ps_idx is not None and not pd.isna(peak_ps_idx)) else 0.0

        peak_nm_idx = trimmed_df['Nm'].idxmax() if 'Nm' in trimmed_df.columns else None
        peak_nm_rpm = float(trimmed_df.loc[peak_nm_idx, 'RPM_smoothed']) if (peak_nm_idx is not None and not pd.isna(peak_nm_idx)) else 0.0

        # Slope & Weather
        detected_slope = float(trimmed_df['Slope_Pct'].iloc[0]) if 'Slope_Pct' in trimmed_df.columns else 0.0
        avg_slope_ps = float(trimmed_df['Slope_Power_PS'].mean()) if 'Slope_Power_PS' in trimmed_df.columns else 0.0
        k_norm = float(trimmed_df['Weather_K_Norm'].iloc[0]) if 'Weather_K_Norm' in trimmed_df.columns else 1.0

        # GPS Coordinates for Open-Meteo
        gps_lat = 0.0
        gps_lon = 0.0
        if 'Lat' in df.columns and 'Lon' in df.columns:
            valid_coords = df[(df['Lat'] != 0.0) & (df['Lon'] != 0.0)]
            if len(valid_coords) > 0:
                gps_lat = float(valid_coords['Lat'].iloc[0])
                gps_lon = float(valid_coords['Lon'].iloc[0])

        # Carburetor Jetting Diagnosis
        carb_setup = load_carb_setup()
        carb_diag = analyze_carb_jetting(trimmed_df, carb_setup)

        diag_rows_html = ""
        if carb_diag.get("valid"):
            for z in carb_diag.get("zones", []):
                badge_bg = "#00e676" if "PERFEKT" in z["status_text"] else ("#ff1744" if "KRITISCH" in z["status_text"] else ("#ff9800" if "LEICHT MAGER" in z["status_text"] else "#29b6f6"))
                diag_rows_html += f"""
                <div style="background:#18181c; border:1px solid #27272e; border-radius:10px; padding:10px; margin-bottom:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <strong style="color:#fff; font-size:0.85rem;">{z['name']} ({z['rpm_range']})</strong>
                        <span style="background:{badge_bg}; color:#000; font-weight:800; font-size:0.7rem; padding:2px 6px; border-radius:4px;">{z['status_text']}</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; font-size:0.8rem; color:#bbb; margin-bottom:4px;">
                        <span>Bauteil: {z['component']}</span>
                        <span>Gemessen: <b style="color:#fff;">{z['mean_afr'] if z['mean_afr'] else '--'} AFR</b></span>
                    </div>
                    <div style="font-size:0.75rem; color:#ddd; background:#0c0c0e; padding:6px 8px; border-radius:4px; border-left:3px solid {badge_bg};">
                        💡 {z['advice']}
                    </div>
                </div>
                """
        else:
            diag_rows_html = "<div style='color:#888; font-size:0.8rem;'>Keine verwertbaren AFR-Punkte für die Vergaserdiagnose.</div>"

        return render_template(
            'analyze.html',
            fname=fname,
            pname=pname,
            max_ps=max_ps,
            ps_raw=ps_raw,
            max_nm=max_nm,
            avg_afr=avg_afr,
            max_egt=max_egt,
            peak_ps_rpm=peak_ps_rpm,
            peak_nm_rpm=peak_nm_rpm,
            detected_slope=detected_slope,
            avg_slope_ps=avg_slope_ps,
            k_norm=k_norm,
            gps_lat=gps_lat,
            gps_lon=gps_lon,
            slope_param=slope_param,
            temp_param=temp_param,
            pressure_param=pressure_param,
            norm_param=norm_param,
            carb_diag=carb_diag,
            diag_rows_html=diag_rows_html
        )
    except Exception as e:
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehler bei der Analyse:</h3><pre>{str(e)}</pre><br><a href='/logs'>Zurück</a></body>"


@dyno_bp.route('/compare')
def compare_runs() -> str:
    """Interactively compares two dyno runs side-by-side with Chart.js."""
    f1 = request.args.get('file1')
    f2 = request.args.get('file2')
    if not f1 or not f2:
        return "Bitte zwei Dateien zum Vergleichen angeben."

    p1 = os.path.join(LOG_DIR, f1)
    p2 = os.path.join(LOG_DIR, f2)
    if not os.path.exists(p1) or not os.path.exists(p2):
        return "Eine oder beide Log-Dateien wurden nicht gefunden."

    try:
        df1 = clean_egt_data(pd.read_csv(p1))
        df2 = clean_egt_data(pd.read_csv(p2))

        df1 = calculate_telemetry_metrics(df1)
        df2 = calculate_telemetry_metrics(df2)

        t1, _ = detect_dyno_pull(df1)
        t2, _ = detect_dyno_pull(df2)

        cdata1 = t1[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')
        cdata2 = t2[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')

        m1_ps = float(t1['PS'].max()) if 'PS' in t1.columns else 0.0
        m1_nm = float(t1['Nm'].max()) if 'Nm' in t1.columns else 0.0
        m1_afr = float(t1['AFR'].mean()) if 'AFR' in t1.columns else 0.0

        m2_ps = float(t2['PS'].max()) if 'PS' in t2.columns else 0.0
        m2_nm = float(t2['Nm'].max()) if 'Nm' in t2.columns else 0.0
        m2_afr = float(t2['AFR'].mean()) if 'AFR' in t2.columns else 0.0

        d_ps = m2_ps - m1_ps
        d_nm = m2_nm - m1_nm

        return render_template(
            'compare.html',
            f1_short=f1.replace('dyno_log_', '').replace('.csv', ''),
            f2_short=f2.replace('dyno_log_', '').replace('.csv', ''),
            m1_ps=m1_ps,
            m1_nm=m1_nm,
            m1_afr=m1_afr,
            m2_ps=m2_ps,
            m2_nm=m2_nm,
            m2_afr=m2_afr,
            d_ps=d_ps,
            d_nm=d_nm,
            cdata1=cdata1,
            cdata2=cdata2
        )
    except Exception as e:
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehler beim Vergleich: {e}</h3><br><a href='/logs'>Zurück</a></body>"


@dyno_bp.route('/tuning')
def tuning_dashboard() -> str:
    """Vergaser-Bedüsungs Dashboard & Live-Diagnose."""
    carb = load_carb_setup()
    files = sorted(glob.glob(os.path.join(LOG_DIR, '*.csv')), key=os.path.getmtime, reverse=True)

    analysis = None
    latest_file = os.path.basename(files[0]) if files else None

    if latest_file:
        try:
            p = os.path.join(LOG_DIR, latest_file)
            df = pd.read_csv(p)
            df.columns = [c.strip() for c in df.columns]
            df = clean_egt_data(df)
            df = calculate_telemetry_metrics(df)
            trimmed, _ = detect_dyno_pull(df)
            analysis = analyze_carb_jetting(trimmed, carb)
        except Exception as e:
            analysis = {"valid": False, "error": str(e), "overall_verdict": "Fehler bei der Analyse"}

    zone_cards_html = ""
    if analysis and analysis.get("valid"):
        for z in analysis.get("zones", []):
            badge_bg = "#00e676" if "PERFEKT" in z["status_text"] else ("#ff1744" if "KRITISCH" in z["status_text"] else ("#ff9800" if "LEICHT MAGER" in z["status_text"] else "#29b6f6"))
            zone_cards_html += f"""
            <div style="background:#16161a; border:1px solid #27272e; border-radius:12px; padding:12px; margin-bottom:10px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <div>
                        <strong style="color:#fff; font-size:0.9rem;">{z['name']}</strong>
                        <div style="font-size:0.75rem; color:#888;">{z['rpm_range']} &nbsp;|&nbsp; {z['component']}</div>
                    </div>
                    <div style="background:{badge_bg}; color:#000; font-weight:800; font-size:0.75rem; padding:3px 8px; border-radius:4px;">
                        {z['status_text']}
                    </div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin:6px 0;">
                    <span>Gemessen: <b style="color:#fff;">{z['mean_afr'] if z['mean_afr'] else '--'} AFR</b></span>
                    <span style="color:#aaa;">Ziel: {z['target']} AFR</span>
                </div>
                <div style="background:#09090c; padding:8px 10px; border-radius:6px; font-size:0.8rem; color:#ddd; border-left:3px solid {badge_bg};">
                    💡 {z['advice']}
                </div>
            </div>
            """
    else:
        zone_cards_html = "<div style='color:#888; font-size:0.85rem;'>Noch kein Dyno-Pull für Live-Diagnose vorhanden. Starte einen Pull!</div>"

    return render_template(
        'tuning.html',
        carb=carb,
        latest_file=latest_file,
        analysis=analysis,
        zone_cards_html=zone_cards_html
    )


@dyno_bp.route('/dyno_sheet')
def dyno_sheet_report() -> str:
    """Generates official A4 Printable Dyno Sheet."""
    fname = request.args.get('file')
    if not fname:
        return "Keine Datei ausgewählt."
    fpath = os.path.join(LOG_DIR, fname)
    if not os.path.exists(fpath):
        return "Datei nicht gefunden."

    slope_param = request.args.get('slope', 'auto')
    try:
        temp_param = float(request.args.get('temp', 20.0))
    except (ValueError, TypeError):
        temp_param = 20.0

    try:
        pressure_param = float(request.args.get('pressure', 1013.25))
    except (ValueError, TypeError):
        pressure_param = 1013.25

    norm_param = request.args.get('norm', 'DIN70020')

    try:
        df = pd.read_csv(fpath)
        df.columns = [c.strip() for c in df.columns]
        df = clean_egt_data(df)
        df = calculate_telemetry_metrics(
            df,
            slope_percent=slope_param,
            temp_c=temp_param,
            pressure_hpa=pressure_param,
            norm_standard=norm_param
        )
        trimmed, _ = detect_dyno_pull(
            df,
            slope_percent=slope_param,
            temp_c=temp_param,
            pressure_hpa=pressure_param,
            norm_standard=norm_param
        )

        carb = load_carb_setup()
        carb_diag = analyze_carb_jetting(trimmed, carb)

        slide_label = SLIDE_TYPES.get(carb.get("slide_type", ""), carb.get("slide_type", "Lemarxon Low Cutaway"))
        intake_label = INTAKE_TYPES.get(carb.get("intake_type", ""), carb.get("intake_type", "Polini Venturi Trichter"))
        airbox_label = AIRBOX_TYPES.get(carb.get("airbox_type", ""), carb.get("airbox_type", "Polini Airbox"))

        gear = int(trimmed.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed.columns else 3
        max_ps = float(trimmed['PS'].max()) if 'PS' in trimmed.columns else 0.0
        max_ps_raw = float(trimmed['PS_Raw'].max()) if 'PS_Raw' in trimmed.columns else max_ps
        max_nm = float(trimmed['Nm'].max()) if 'Nm' in trimmed.columns else 0.0
        avg_afr = float(trimmed['AFR'].mean()) if 'AFR' in trimmed.columns else 0.0
        max_egt = float(trimmed['EGT_cleaned'].max()) if 'EGT_cleaned' in trimmed.columns else 0.0

        peak_ps_idx = trimmed['PS'].idxmax() if 'PS' in trimmed.columns else None
        peak_ps_rpm = float(trimmed.loc[peak_ps_idx, 'RPM_smoothed']) if (peak_ps_idx is not None and not pd.isna(peak_ps_idx)) else 0.0

        peak_nm_idx = trimmed['Nm'].idxmax() if 'Nm' in trimmed.columns else None
        peak_nm_rpm = float(trimmed.loc[peak_nm_idx, 'RPM_smoothed']) if (peak_nm_idx is not None and not pd.isna(peak_nm_idx)) else 0.0

        k_norm = float(trimmed['Weather_K_Norm'].iloc[0]) if 'Weather_K_Norm' in trimmed.columns else 1.0
        detected_slope = float(trimmed['Slope_Pct'].iloc[0]) if 'Slope_Pct' in trimmed.columns else 0.0

        chart_data = trimmed[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%d.%m.%Y - %H:%M:%S')

        return render_template(
            'dyno_sheet.html',
            fname=fname,
            mtime=mtime,
            gear=gear,
            max_ps=max_ps,
            max_ps_raw=max_ps_raw,
            max_nm=max_nm,
            avg_afr=avg_afr,
            max_egt=max_egt,
            peak_ps_rpm=peak_ps_rpm,
            peak_nm_rpm=peak_nm_rpm,
            carb=carb,
            carb_diag=carb_diag,
            slide_label=slide_label,
            intake_label=intake_label,
            airbox_label=airbox_label,
            norm_param=norm_param,
            temp_param=temp_param,
            pressure_param=pressure_param,
            k_norm=k_norm,
            detected_slope=detected_slope,
            slope_param=slope_param,
            chart_data=chart_data
        )
    except Exception as e:
        return f"<h3>Fehler beim Erstellen des Dyno-Sheets: {str(e)}</h3><br><a href='/analyze?file={fname}'>Zurück</a>"


@dyno_bp.route('/download/<filename>')
def download(filename: str):
    """Downloads raw CSV log file."""
    return send_from_directory(LOG_DIR, filename, as_attachment=True)


@dyno_bp.route('/plots/<filename>')
def serve_plot(filename: str):
    """Serves generated P4 dyno curve images."""
    return send_from_directory(PLOT_DIR, filename)


# --- JSON API ROUTES ---

@dyno_bp.route('/api/data')
def api_data():
    """Returns 10Hz live telemetry JSON stream."""
    hw = get_hw_service()
    if hw:
        state = hw.get_telemetry()
        data = state.to_dict()
    else:
        data = {
            "rpm": 0, "speed": 0.0, "afr": 0.0, "egt": 0,
            "lat": 0.0, "lon": 0.0, "alt": 0.0, "fix": False,
            "is_logging": False, "status": "OFFLINE"
        }
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@dyno_bp.route('/api/toggle_logging')
def api_toggle_logging():
    """Toggles hardware CSV recording."""
    hw = get_hw_service()
    is_logging = hw.toggle_logging() if hw else False
    resp = jsonify({"is_logging": is_logging, "status": "REC" if is_logging else "IDLE"})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@dyno_bp.route('/api/update_carb_setup', methods=['GET', 'POST'])
def api_update_carb_setup():
    """Updates persistent carburetor setup."""
    try:
        if request.method == 'POST':
            data = request.get_json(force=True, silent=True) or request.form.to_dict()
        else:
            data = request.args.to_dict()

        if not data:
            return jsonify({"status": "error", "message": "Keine Daten empfangen"}), 400

        cleaned = {}
        for k, v in data.items():
            if k in ['main_jet_hd', 'air_corrector_hlkd']:
                try:
                    cleaned[k] = int(float(v))
                except Exception:
                    cleaned[k] = v
            else:
                cleaned[k] = v

        success = save_carb_setup(cleaned)
        if success:
            updated = load_carb_setup()
            return jsonify({"status": "success", "setup": updated})
        else:
            return jsonify({"status": "error", "message": "Fehler beim Speichern"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@dyno_bp.route('/api/toggle_display')
def api_toggle_display():
    """Toggles OLED display mode."""
    hw = get_hw_service()
    new_mode = hw.toggle_display_mode() if hw else "MOCK"
    return jsonify({"status": "success", "display_mode": new_mode})
