import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request

from data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    plot_telemetry,
    export_to_google_sheets
)

# ==========================================
# --- KONFIGURATION v5.1 (STABLE LOGGING + AFR WARN) ---
# ==========================================
AFR_OFFSET = 0.0        # Justiert auf dein Tacho-Standgas (~13.2)
EGT_OFFSET = 0.0        
RPM_MULTIPLIER = 0.69   
RPM_ALPHA = 0.15        
AFR_ALPHA = 0.05        # MASSIVE Dämpfung für AFR (Tacho-Look)
AFR_MAX_VALID = 16.5    # Spike-Blocker für Schiebebetrieb
AUTO_START_RPM = 1350   
MIN_SPEED_KMH = 2.0     
# ==========================================

LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"
PLOT_DIR = "/home/rolovsky/streetdyno2.0/plots"
GOOGLE_CREDS_PATH = "/home/rolovsky/streetdyno2.0/service_account.json"
os.makedirs(PLOT_DIR, exist_ok=True)

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(21, GPIO.IN, pull_up_down=GPIO.PUD_UP)
except:
    HAS_GPIO = False

from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger
try: from display_oled import OLEDDisplay
except: from hw.display_oled import OLEDDisplay

telemetry = {"rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, "fix": False, "status": "🟢 IDLE"}
app = Flask(__name__)
time_synced = False

def sync_time_with_gps(gps_data):
    """ Setzt die Systemzeit des Pi basierend auf GPS UTC """
    global time_synced
    if not time_synced and gps_data and gps_data.fix:
        try:
            new_time = gps_data.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            os.system(f'sudo date -s "{new_time}"')
            print(f"--- GPS TIME SYNC: Systemzeit auf {new_time} gesetzt ---")
            time_synced = True
        except:
            pass

def smart_round(value):
    """ Intelligent runden für ein ruhiges Dashboard """
    if value > 4000: round_to = 100
    elif value > 2000: round_to = 50
    elif value > 1000: round_to = 25
    elif value > 500: round_to = 10
    else: return value
    return int(round((value + (round_to / 2)) / round_to) * round_to)

# --- HIGH-CONTRAST OUTDOOR LIVE COCKPIT HUD ---
DASH_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>StreetDyno 2.0 - Live HUD</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800;900&family=JetBrains+Mono:wght@700;800&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: #000000;
            color: #ffffff;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
            overflow-x: hidden;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            padding: 10px;
            user-select: none;
            -webkit-user-select: none;
        }

        /* Top Bar */
        .top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 12px;
            background: #0f0f12;
            border: 1px solid #222228;
            border-radius: 12px;
            margin-bottom: 8px;
            font-size: 0.85rem;
            font-weight: 700;
            letter-spacing: 0.05em;
        }
        .status-pill {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 20px;
            background: #18181c;
            border: 1px solid #333;
        }
        .rec-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #4caf50;
        }
        .rec-dot.active {
            background: #ff1744;
            box-shadow: 0 0 10px #ff1744;
            animation: pulse-dot 0.6s infinite alternate;
        }
        @keyframes pulse-dot { from { opacity: 0.3; } to { opacity: 1; } }

        .top-actions {
            display: flex;
            gap: 8px;
        }
        .btn-icon {
            background: #1c1c22;
            border: 1px solid #333;
            color: #fff;
            padding: 6px 12px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 0.8rem;
            text-decoration: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .btn-icon:active { background: #333; }

        /* Rev Bar / Shift Light */
        .rev-bar-container {
            width: 100%;
            height: 14px;
            background: #111116;
            border-radius: 7px;
            border: 1px solid #222;
            overflow: hidden;
            margin-bottom: 10px;
            position: relative;
        }
        .rev-bar-fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #00e676 0%, #ffea00 65%, #ff9100 80%, #ff1744 100%);
            transition: width 0.08s linear;
        }
        .shift-light {
            position: fixed;
            top: 0; left: 0; right: 0;
            height: 6px;
            background: transparent;
            z-index: 9999;
            pointer-events: none;
        }
        .shift-light.flash {
            background: #ff1744;
            box-shadow: 0 0 30px 10px #ff1744;
            animation: flash-border 0.12s infinite;
        }
        @keyframes flash-border { 50% { opacity: 0.1; } }

        /* Main Grid (Portrait & Landscape responsive) */
        .hud-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            flex-grow: 1;
            margin-bottom: 10px;
        }
        @media (orientation: landscape) {
            .hud-grid {
                grid-template-columns: repeat(4, 1fr);
            }
        }

        .hud-card {
            background: #0d0d11;
            border: 2px solid #1c1c24;
            border-radius: 20px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            text-align: center;
            position: relative;
            box-shadow: 0 6px 20px rgba(0,0,0,0.6);
        }
        .hud-card .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #71717a;
        }
        .hud-card .card-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 3.8rem;
            font-weight: 800;
            line-height: 1.0;
            margin: 8px 0;
            letter-spacing: -0.04em;
        }
        .hud-card .card-unit {
            font-size: 0.8rem;
            font-weight: 700;
            color: #52525b;
            text-transform: uppercase;
        }

        /* Value color themes */
        .card-speed .card-value { color: #00ffcc; text-shadow: 0 0 25px rgba(0,255,204,0.3); }
        .card-rpm .card-value { color: #ff9800; text-shadow: 0 0 25px rgba(255,152,0,0.3); }
        .card-afr .card-value { color: #00e676; text-shadow: 0 0 25px rgba(0,230,118,0.3); }
        .card-egt .card-value { color: #ffd600; text-shadow: 0 0 25px rgba(255,214,0,0.3); }

        /* Danger Alarms */
        @keyframes danger-blink {
            0% { background: #0d0d11; border-color: #ff1744; }
            50% { background: #3b0811; border-color: #ff1744; box-shadow: 0 0 35px rgba(255,23,68,0.7); }
            100% { background: #0d0d11; border-color: #ff1744; }
        }
        .card-danger {
            animation: danger-blink 0.4s infinite !important;
        }
        .card-danger .card-value {
            color: #ff1744 !important;
            text-shadow: 0 0 30px #ff1744 !important;
        }

        /* Bottom Action Bar */
        .bottom-bar {
            display: flex;
            gap: 10px;
            margin-top: 5px;
        }
        .btn-rec {
            flex: 2;
            padding: 16px;
            border-radius: 14px;
            font-size: 1.1rem;
            font-weight: 900;
            letter-spacing: 0.05em;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: #18181c;
            color: #00ffcc;
            border: 2px solid #00ffcc;
            box-shadow: 0 4px 15px rgba(0,255,204,0.2);
            transition: all 0.15s;
        }
        .btn-rec.recording {
            background: #ff1744;
            color: #ffffff;
            border: 2px solid #ff5252;
            box-shadow: 0 0 25px rgba(255,23,68,0.6);
            animation: pulse-btn 1s infinite alternate;
        }
        @keyframes pulse-btn { from { transform: scale(1); } to { transform: scale(0.98); } }
        .btn-rec:active { transform: scale(0.95); }

        .btn-nav {
            flex: 1;
            padding: 16px;
            border-radius: 14px;
            font-size: 1.0rem;
            font-weight: 800;
            background: #18181c;
            color: #ffffff;
            border: 2px solid #33333e;
            text-decoration: none;
            text-align: center;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .btn-nav:active { background: #27272a; }

        /* WakeLock Status Indicator */
        .wakelock-badge {
            font-size: 0.7rem;
            color: #4caf50;
            display: flex;
            align-items: center;
            gap: 4px;
        }
    </style>
</head>
<body>
    <div id="shiftLight" class="shift-light"></div>

    <div class="top-bar">
        <div class="status-pill">
            <div id="recDot" class="rec-dot"></div>
            <span id="statusText">V5.1 READY</span>
        </div>
        <div id="gpsPill" class="status-pill">
            <span>🛰️ GPS:</span>
            <span id="gpsText" style="color:#00ffcc;">SUCHE...</span>
        </div>
        <div class="top-actions">
            <button class="btn-icon" onclick="toggleFullScreen()">⛶ HUD</button>
        </div>
    </div>

    <!-- RPM Rev Bar -->
    <div class="rev-bar-container">
        <div id="revBar" class="rev-bar-fill"></div>
    </div>

    <!-- Main High-Contrast Numeric Grid -->
    <div class="hud-grid">
        <div class="hud-card card-speed">
            <div class="card-header">
                <span>Speed</span>
                <span>GPS</span>
            </div>
            <div id="speed" class="card-value">0.0</div>
            <div class="card-unit">KM / H</div>
        </div>

        <div class="hud-card card-rpm" id="cardRpm">
            <div class="card-header">
                <span>Drehzahl</span>
                <span id="gearBadge">N</span>
            </div>
            <div id="rpm" class="card-value">0</div>
            <div class="card-unit">U / MIN</div>
        </div>

        <div class="hud-card card-afr" id="cardAfr">
            <div class="card-header">
                <span>Lambda</span>
                <span id="afrZone">--</span>
            </div>
            <div id="afr" class="card-value">0.00</div>
            <div class="card-unit">AFR (12.8 OPT)</div>
        </div>

        <div class="hud-card card-egt" id="cardEgt">
            <div class="card-header">
                <span>Abgastemp</span>
                <span>MAX 630°C</span>
            </div>
            <div id="egt" class="card-value">0</div>
            <div class="card-unit">°C (EGT)</div>
        </div>
    </div>

    <!-- Bottom Action Controls -->
    <div class="bottom-bar">
        <button id="recBtn" class="btn-rec" onclick="toggleLogging()">
            🔴 PULL LOGGING STARTEN
        </button>
        <a href="/logs" class="btn-nav">📂 LOGS</a>
    </div>

    <script>
        let wakeLock = null;

        // Auto WakeLock (keeps phone screen on)
        async function requestWakeLock() {
            try {
                if ('wakeLock' in navigator) {
                    wakeLock = await navigator.wakeLock.request('screen');
                }
            } catch (err) {
                console.log('WakeLock error:', err);
            }
        }
        document.addEventListener('visibilitychange', async () => {
            if (wakeLock !== null && document.visibilityState === 'visible') {
                await requestWakeLock();
            }
        });
        window.addEventListener('click', requestWakeLock, { once: true });
        requestWakeLock();

        function toggleFullScreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen().catch(() => {});
            }
        }

        function toggleLogging() {
            fetch('/api/toggle_logging')
                .then(r => r.json())
                .then(d => {
                    updateRecState(d.is_logging);
                })
                .catch(e => console.error(e));
        }

        function updateRecState(isLogging) {
            const btn = document.getElementById('recBtn');
            const dot = document.getElementById('recDot');
            if (isLogging) {
                btn.classList.add('recording');
                btn.innerText = '⏹️ LOGGING STOPPEN';
                dot.classList.add('active');
            } else {
                btn.classList.remove('recording');
                btn.innerText = '🔴 PULL LOGGING STARTEN';
                dot.classList.remove('active');
            }
        }

        // Live 10Hz Polling
        setInterval(() => {
            fetch('/api/data')
                .then(r => r.json())
                .then(d => {
                    const rpm = d.rpm || 0;
                    const speed = d.speed || 0.0;
                    const afr = d.afr || 0.0;
                    const egt = d.egt || 0.0;

                    // Numeric values
                    document.getElementById('speed').innerText = speed.toFixed(1);
                    document.getElementById('rpm').innerText = rpm.toFixed(0);
                    document.getElementById('afr').innerText = afr.toFixed(2);
                    document.getElementById('egt').innerText = egt.toFixed(0);

                    // Rev Bar & Shift Light (>8000 RPM)
                    const revPct = Math.min(100, Math.max(0, (rpm / 9000) * 100));
                    document.getElementById('revBar').style.width = revPct + '%';

                    const shiftLight = document.getElementById('shiftLight');
                    if (rpm >= 8000) {
                        shiftLight.classList.add('flash');
                    } else {
                        shiftLight.classList.remove('flash');
                    }

                    // AFR Color Zone & Lean Alert (>14.5 under load)
                    const cardAfr = document.getElementById('cardAfr');
                    const afrZone = document.getElementById('afrZone');
                    if (afr > 14.5 && speed > 8) {
                        cardAfr.classList.add('card-danger');
                        afrZone.innerText = '⚠️ MAGER!';
                    } else {
                        cardAfr.classList.remove('card-danger');
                        if (afr >= 12.5 && afr <= 13.5) {
                            afrZone.innerText = '🟢 OPTIMAL';
                            cardAfr.style.borderColor = '#00e676';
                        } else if (afr < 12.5 && afr > 10.0) {
                            afrZone.innerText = '🔵 FETT';
                            cardAfr.style.borderColor = '#29b6f6';
                        } else {
                            afrZone.innerText = '--';
                            cardAfr.style.borderColor = '#1c1c24';
                        }
                    }

                    // EGT Overheat Alert (>630°C)
                    const cardEgt = document.getElementById('cardEgt');
                    if (egt >= 630.0) {
                        cardEgt.classList.add('card-danger');
                    } else {
                        cardEgt.classList.remove('card-danger');
                    }

                    // GPS & Status
                    const gpsText = document.getElementById('gpsText');
                    gpsText.innerText = d.fix ? '3D FIX' : 'SUCHE...';
                    gpsText.style.color = d.fix ? '#00e676' : '#ffea00';

                    const statusText = document.getElementById('statusText');
                    statusText.innerText = d.status;

                    const isLogging = d.status.includes('REC');
                    updateRecState(isLogging);
                })
                .catch(() => {});
        }, 150);
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(DASH_HTML)

@app.route('/api/data')
def api_data(): return jsonify(telemetry)

@app.route('/api/toggle_logging')
def api_toggle_logging():
    global global_logger
    if 'global_logger' in globals() and global_logger:
        if global_logger.is_logging:
            global_logger.stop()
            telemetry["status"] = "🟢 IDLE"
        else:
            global_logger.start()
            telemetry["status"] = "🔴 REC"
        return jsonify({"is_logging": global_logger.is_logging, "status": telemetry["status"]})
    return jsonify({"error": "Logger not initialized"}), 500

@app.route('/logs')
def list_logs():
    files = sorted([os.path.basename(x) for x in glob.glob(os.path.join(LOG_DIR, '*.csv'))], reverse=True)
    
    rows = []
    for f in files:
        fpath = os.path.join(LOG_DIR, f)
        size_kb = os.path.getsize(fpath) / 1024.0
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%d.%m.%Y %H:%M')
        
        rows.append(f"""
        <div class="log-item">
            <div class="log-check">
                <input type="checkbox" class="compare-checkbox" value="{f}" onchange="updateCompareBtn()">
            </div>
            <div class="log-info">
                <div class="log-name">{f}</div>
                <div class="log-meta">📅 {mtime} &nbsp;|&nbsp; 💾 {size_kb:.1f} KB</div>
            </div>
            <div class="log-actions">
                <a href="/analyze?file={f}" class="btn-sm btn-analyze">⚡ ANALYSE</a>
                <a href="/download/{f}" class="btn-sm btn-down">⬇️ CSV</a>
            </div>
        </div>
        """)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>StreetDyno 2.0 - Log-Archiv</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                background: #0d0d11;
                color: #f5f5f7;
                font-family: 'Outfit', sans-serif;
                padding: 15px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid #222;
            }}
            h1 {{ font-size: 1.4rem; font-weight: 800; color: #00ffcc; }}
            .back-btn {{
                background: #1f1f24;
                color: #fff;
                padding: 8px 14px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: 700;
                font-size: 0.9rem;
            }}
            .compare-banner {{
                background: #18181c;
                border: 2px solid #00ffcc;
                border-radius: 12px;
                padding: 12px 16px;
                margin-bottom: 15px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                box-shadow: 0 4px 20px rgba(0,255,204,0.15);
            }}
            .btn-compare {{
                background: #00ffcc;
                color: #000;
                border: none;
                padding: 10px 18px;
                border-radius: 8px;
                font-weight: 800;
                font-size: 0.95rem;
                cursor: pointer;
                transition: opacity 0.2s;
            }}
            .btn-compare:disabled {{
                background: #333;
                color: #777;
                cursor: not-allowed;
            }}
            .log-list {{ display: flex; flex-direction: column; gap: 10px; }}
            .log-item {{
                background: #16161a;
                border: 1px solid #27272e;
                border-radius: 12px;
                padding: 12px 14px;
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            .log-check input {{
                width: 22px;
                height: 22px;
                accent-color: #00ffcc;
                cursor: pointer;
            }}
            .log-info {{ flex: 1; overflow: hidden; }}
            .log-name {{
                font-family: monospace;
                font-size: 0.95rem;
                font-weight: 700;
                color: #fff;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .log-meta {{ font-size: 0.75rem; color: #888; margin-top: 3px; }}
            .log-actions {{ display: flex; gap: 6px; }}
            .btn-sm {{
                padding: 8px 12px;
                border-radius: 6px;
                font-weight: 700;
                font-size: 0.8rem;
                text-decoration: none;
            }}
            .btn-analyze {{ background: #00ffcc; color: #000; }}
            .btn-down {{ background: #27272e; color: #ff9800; border: 1px solid #333; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📂 LOG-ARCHIV</h1>
            <a href="/" class="back-btn">⬅️ COCKPIT</a>
        </div>

        <div class="compare-banner">
            <div>
                <strong style="color:#00ffcc;">⚖️ 2 Runs Vergleichen</strong>
                <div style="font-size:0.75rem; color:#aaa;" id="compareCount">0 von 2 ausgewählt</div>
            </div>
            <button id="compareBtn" class="btn-compare" disabled onclick="launchComparison()">
                VERGLEICHEN
            </button>
        </div>

        <div class="log-list">
            {''.join(rows)}
        </div>

        <script>
            function updateCompareBtn() {{
                const checked = Array.from(document.querySelectorAll('.compare-checkbox:checked')).map(cb => cb.value);
                const btn = document.getElementById('compareBtn');
                const count = document.getElementById('compareCount');
                
                count.innerText = checked.length + ' von 2 ausgewählt';
                if (checked.length === 2) {{
                    btn.disabled = false;
                }} else {{
                    btn.disabled = true;
                }}
            }}

            function launchComparison() {{
                const checked = Array.from(document.querySelectorAll('.compare-checkbox:checked')).map(cb => cb.value);
                if (checked.length === 2) {{
                    window.location.href = '/compare?file1=' + encodeURIComponent(checked[0]) + '&file2=' + encodeURIComponent(checked[1]);
                }}
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/compare')
def compare_runs():
    file1 = request.args.get('file1')
    file2 = request.args.get('file2')
    if not file1 or not file2:
        return "<body style='background:#111; color:#fff; padding:20px;'><h3>Bitte 2 Dateien auswählen.</h3><a href='/logs'>Zurück</a></body>"

    p1 = os.path.join(LOG_DIR, file1)
    p2 = os.path.join(LOG_DIR, file2)
    if not os.path.exists(p1) or not os.path.exists(p2):
        return "<body style='background:#111; color:#fff; padding:20px;'><h3>Dateien nicht gefunden.</h3><a href='/logs'>Zurück</a></body>"

    try:
        # Load and compute Run 1
        df1 = pd.read_csv(p1)
        df1.columns = [c.strip() for c in df1.columns]
        df1 = clean_egt_data(df1)
        df1 = calculate_telemetry_metrics(df1)
        t1, det1 = detect_dyno_pull(df1)
        
        # Load and compute Run 2
        df2 = pd.read_csv(p2)
        df2.columns = [c.strip() for c in df2.columns]
        df2 = clean_egt_data(df2)
        df2 = calculate_telemetry_metrics(df2)
        t2, det2 = detect_dyno_pull(df2)

        gear1 = int(t1.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in t1.columns else 3
        gear2 = int(t2.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in t2.columns else 3

        max_ps1 = float(t1['PS'].max()) if 'PS' in t1.columns else 0.0
        max_ps2 = float(t2['PS'].max()) if 'PS' in t2.columns else 0.0
        max_nm1 = float(t1['Nm'].max()) if 'Nm' in t1.columns else 0.0
        max_nm2 = float(t2['Nm'].max()) if 'Nm' in t2.columns else 0.0
        avg_afr1 = float(t1['AFR'].mean()) if 'AFR' in t1.columns else 0.0
        avg_afr2 = float(t2['AFR'].mean()) if 'AFR' in t2.columns else 0.0

        # Delta metrics
        delta_ps = max_ps2 - max_ps1
        delta_nm = max_nm2 - max_nm1

        # Data arrays for Chart.js
        chart_data1 = t1[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')
        chart_data2 = t2[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')

        html = f"""
        <!DOCTYPE html>
        <html lang="de">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>StreetDyno 2.0 - Run Vergleich</title>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; }}
                body {{
                    background: #0d0d11;
                    color: #f5f5f7;
                    font-family: 'Outfit', sans-serif;
                    padding: 15px;
                }}
                .header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 15px;
                    padding-bottom: 10px;
                    border-bottom: 1px solid #222;
                }}
                h1 {{ font-size: 1.3rem; font-weight: 800; color: #00ffcc; }}
                .back-btn {{
                    background: #1f1f24;
                    color: #ff9800;
                    padding: 8px 14px;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: 700;
                    font-size: 0.9rem;
                }}
                .compare-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 10px;
                    margin-bottom: 15px;
                }}
                .comp-card {{
                    background: #16161a;
                    border: 2px solid #27272e;
                    border-radius: 12px;
                    padding: 12px;
                }}
                .comp-card.run1 {{ border-color: #00ffcc; }}
                .comp-card.run2 {{ border-color: #e040fb; }}
                .run-title {{ font-size: 0.8rem; font-weight: 800; text-transform: uppercase; margin-bottom: 4px; }}
                .run1 .run-title {{ color: #00ffcc; }}
                .run2 .run-title {{ color: #e040fb; }}
                .run-file {{ font-family: monospace; font-size: 0.75rem; color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 8px; }}
                .metric-row {{ display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 4px; }}
                .metric-val {{ font-weight: 800; color: #fff; }}
                .chart-box {{
                    background: #16161a;
                    border: 1px solid #27272e;
                    border-radius: 12px;
                    padding: 12px;
                    margin-bottom: 15px;
                    height: 380px;
                }}
                .delta-badge {{
                    display: inline-block;
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-size: 0.75rem;
                    font-weight: 800;
                    margin-left: 4px;
                }}
                .delta-pos {{ background: rgba(0,230,118,0.2); color: #00e676; }}
                .delta-neg {{ background: rgba(255,23,68,0.2); color: #ff1744; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>⚖️ DYNO RUN VERGLEICH</h1>
                <a href="/logs" class="back-btn">📂 LOGS</a>
            </div>

            <div class="compare-grid">
                <div class="comp-card run1">
                    <div class="run-title">🔵 Run 1 ({gear1}. Gang)</div>
                    <div class="run-file">{file1}</div>
                    <div class="metric-row"><span>Peak Leistung:</span><span class="metric-val">{max_ps1:.1f} PS</span></div>
                    <div class="metric-row"><span>Peak Drehmoment:</span><span class="metric-val">{max_nm1:.1f} Nm</span></div>
                    <div class="metric-row"><span>Ø AFR:</span><span class="metric-val">{avg_afr1:.2f}</span></div>
                </div>

                <div class="comp-card run2">
                    <div class="run-title">🟣 Run 2 ({gear2}. Gang)</div>
                    <div class="run-file">{file2}</div>
                    <div class="metric-row">
                        <span>Peak Leistung:</span>
                        <span class="metric-val">
                            {max_ps2:.1f} PS
                            <span class="delta-badge {('delta-pos' if delta_ps >= 0 else 'delta-neg')}">{('+' if delta_ps >= 0 else '')}{delta_ps:.1f} PS</span>
                        </span>
                    </div>
                    <div class="metric-row">
                        <span>Peak Drehmoment:</span>
                        <span class="metric-val">
                            {max_nm2:.1f} Nm
                            <span class="delta-badge {('delta-pos' if delta_nm >= 0 else 'delta-neg')}">{('+' if delta_nm >= 0 else '')}{delta_nm:.1f} Nm</span>
                        </span>
                    </div>
                    <div class="metric-row"><span>Ø AFR:</span><span class="metric-val">{avg_afr2:.2f}</span></div>
                </div>
            </div>

            <div class="chart-box">
                <canvas id="compareChart"></canvas>
            </div>

            <script>
                const data1 = {chart_data1};
                const data2 = {chart_data2};

                const ctx = document.getElementById('compareChart').getContext('2d');
                new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        datasets: [
                            {{
                                label: 'Run 1 Leistung (PS)',
                                data: data1.map(d => ({{ x: d.RPM_smoothed, y: d.PS }})),
                                borderColor: '#00ffcc',
                                backgroundColor: '#00ffcc',
                                borderWidth: 2.5,
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y'
                            }},
                            {{
                                label: 'Run 2 Leistung (PS)',
                                data: data2.map(d => ({{ x: d.RPM_smoothed, y: d.PS }})),
                                borderColor: '#e040fb',
                                backgroundColor: '#e040fb',
                                borderWidth: 2.5,
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y'
                            }},
                            {{
                                label: 'Run 1 Drehmoment (Nm)',
                                data: data1.map(d => ({{ x: d.RPM_smoothed, y: d.Nm }})),
                                borderColor: '#ff9800',
                                backgroundColor: '#ff9800',
                                borderWidth: 2.0,
                                borderDash: [4, 4],
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y1'
                            }},
                            {{
                                label: 'Run 2 Drehmoment (Nm)',
                                data: data2.map(d => ({{ x: d.RPM_smoothed, y: d.Nm }})),
                                borderColor: '#ffd600',
                                backgroundColor: '#ffd600',
                                borderWidth: 2.0,
                                borderDash: [4, 4],
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y1'
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {{ mode: 'index', intersect: false }},
                        scales: {{
                            x: {{
                                type: 'linear',
                                title: {{ display: true, text: 'Drehzahl (U/min)', color: '#888' }},
                                grid: {{ color: '#222' }},
                                ticks: {{ color: '#aaa' }}
                            }},
                            y: {{
                                type: 'linear',
                                position: 'left',
                                title: {{ display: true, text: 'Leistung (PS)', color: '#00ffcc' }},
                                grid: {{ color: '#222' }},
                                ticks: {{ color: '#00ffcc' }},
                                min: 0
                            }},
                            y1: {{
                                type: 'linear',
                                position: 'right',
                                title: {{ display: true, text: 'Drehmoment (Nm)', color: '#ff9800' }},
                                grid: {{ drawOnChartArea: false }},
                                ticks: {{ color: '#ff9800' }},
                                min: 0
                            }}
                        }},
                        plugins: {{
                            legend: {{ labels: {{ color: '#fff', boxWidth: 12, font: {{ size: 10 }} }} }},
                            tooltip: {{
                                backgroundColor: '#18181c',
                                borderColor: '#444',
                                borderWidth: 1,
                                titleColor: '#fff',
                                bodyColor: '#ccc'
                            }}
                        }}
                    }}
                }});
            </script>
        </body>
        </html>
        """
        return render_template_string(html)
    except Exception as e:
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehler beim Vergleich: {str(e)}</h3><a href='/logs'>Zurück</a></body>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

@app.route('/analyze')
def analyze_file():
    fname = request.args.get('file')
    if not fname: return "No file selected."
    fpath = os.path.join(LOG_DIR, fname)
    if not os.path.exists(fpath):
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Datei nicht gefunden</h3><br><a href='/logs'>Zurück</a></body>"
    try:
        df = pd.read_csv(fpath)
        
        # Spaltennamen bereinigen
        col_mapping = {col: col.strip() for col in df.columns}
        df = df.rename(columns=col_mapping)
        
        # Prüfen ob alle Spalten existieren
        required_cols = ['Time', 'RPM', 'AFR', 'EGT', 'Speed_kmh']
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehlende Spalten in CSV: {missing_cols}</h3><br><a href='/logs'>Zurück</a></body>"
            
        df = clean_egt_data(df)
        df = calculate_telemetry_metrics(df)
        
        # Dyno-Pull automatisch erkennen
        trimmed_df, detected = detect_dyno_pull(df, min_rpm=2800.0, min_duration_sec=0.8, drop_threshold=400.0)
        detected_gear = int(trimmed_df.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed_df.columns else 3
        title_suffix = f" (🎯 {detected_gear}. Gang)" if detected else " (Gesamtes Log)"
        
        # Plot generieren
        pname = f"p_{os.path.splitext(fname)[0]}.png"
        plot_path = os.path.join(PLOT_DIR, pname)
        plot_telemetry(trimmed_df, title_suffix, plot_path)
        
        # Leistungswerte berechnen
        max_ps = trimmed_df['PS'].max() if 'PS' in trimmed_df.columns else 0.0
        max_nm = trimmed_df['Nm'].max() if 'Nm' in trimmed_df.columns else 0.0
        max_egt = trimmed_df['EGT_cleaned'].max() if 'EGT_cleaned' in trimmed_df.columns else 0.0
        
        # RPM bei Peak Werten finden
        peak_ps_idx = trimmed_df['PS'].idxmax() if 'PS' in trimmed_df.columns else None
        peak_ps_rpm = trimmed_df.loc[peak_ps_idx, 'RPM_smoothed'] if (peak_ps_idx is not None and not pd.isna(peak_ps_idx)) else 0.0
        
        peak_nm_idx = trimmed_df['Nm'].idxmax() if 'Nm' in trimmed_df.columns else None
        peak_nm_rpm = trimmed_df.loc[peak_nm_idx, 'RPM_smoothed'] if (peak_nm_idx is not None and not pd.isna(peak_nm_idx)) else 0.0
        
        # Mittlerer AFR während des Pulls
        avg_afr = trimmed_df['AFR'].mean() if 'AFR' in trimmed_df.columns else 0.0
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>StreetDyno 2.0 - Analyse</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        body {{
            background: #0d0d0d;
            color: #f5f5f7;
            font-family: 'Outfit', sans-serif;
            margin: 0;
            padding: 15px;
            -webkit-font-smoothing: antialiased;
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            padding: 5px 0;
            border-bottom: 1px solid #222;
        }}
        .header h1 {{
            font-size: 1.5rem;
            font-weight: 800;
            margin: 0;
            background: linear-gradient(135deg, #00ffcc, #0099ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .back-link {{
            color: #ff9800;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: opacity 0.2s;
        }}
        .back-link:hover {{
            opacity: 0.8;
        }}
        .filename-banner {{
            font-size: 0.85rem;
            color: #888;
            background: #161618;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 15px;
            font-family: monospace;
            border: 1px solid #222;
            overflow-x: auto;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            margin-bottom: 20px;
        }}
        .card {{
            background: linear-gradient(145deg, #18181b, #121214);
            border: 1px solid #27272a;
            border-radius: 16px;
            padding: 15px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        }}
        .card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; width: 100%; height: 3px;
        }}
        .card-ps::before {{ background: #00ffcc; }}
        .card-nm::before {{ background: #ff9800; }}
        .card-afr::before {{ background: #ff3366; }}
        .card-egt::before {{ background: #ffcc00; }}
        
        .card .label {{
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #8e8e93;
            margin-bottom: 5px;
        }}
        .card .value {{
            font-size: 1.8rem;
            font-weight: 800;
            line-height: 1.1;
            margin: 5px 0;
        }}
        .card-ps .value {{ color: #00ffcc; }}
        .card-nm .value {{ color: #ff9800; }}
        .card-afr .value {{ color: #ff3366; }}
        .card-egt .value {{ color: #ffcc00; }}
        
        .card .sub {{
            font-size: 0.75rem;
            color: #a1a1aa;
            margin-top: 2px;
        }}
        .plot-container {{
            background: #121214;
            border: 1px solid #27272a;
            border-radius: 16px;
            padding: 8px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            overflow: hidden;
        }}
        .plot-container img {{
            width: 100%;
            height: auto;
            border-radius: 12px;
            display: block;
        }}
        .cloud-section {{
            background: #18181b;
            border: 1px solid #27272a;
            border-radius: 16px;
            padding: 15px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}
        .cloud-section h3 {{
            margin-top: 0;
            font-size: 1.1rem;
            font-weight: 600;
            color: #f5f5f7;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .cloud-section p {{
            font-size: 0.85rem;
            color: #a1a1aa;
            line-height: 1.4;
            margin: 5px 0 15px 0;
        }}
        .btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: 100%;
            padding: 14px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 1rem;
            border: none;
            cursor: pointer;
            box-sizing: border-box;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #007aff, #0055d4);
            color: #fff;
        }}
        .btn-primary:active {{
            transform: scale(0.98);
        }}
        .btn:disabled {{
            background: #27272a;
            color: #71717a;
            cursor: not-allowed;
            transform: none;
        }}
        .info-box {{
            background: rgba(255, 152, 0, 0.08);
            border: 1px solid rgba(255, 152, 0, 0.2);
            border-radius: 10px;
            padding: 12px;
            font-size: 0.8rem;
            color: #ffb74d;
            line-height: 1.4;
        }}
        .info-box ol {{
            margin: 8px 0 0 15px;
            padding: 0;
        }}
        .info-box li {{
            margin-bottom: 4px;
        }}
        .status-message {{
            font-size: 0.9rem;
            font-weight: 600;
            text-align: center;
            margin-top: 10px;
            padding: 8px;
            border-radius: 8px;
            display: none;
        }}
        .status-success {{ background: rgba(76, 175, 80, 0.15); color: #4caf50; border: 1px solid rgba(76, 175, 80, 0.3); }}
        .status-error {{ background: rgba(244, 67, 54, 0.15); color: #f44336; border: 1px solid rgba(244, 67, 54, 0.3); }}
        
        .spinner {{
            width: 18px;
            height: 18px;
            border: 2px solid rgba(255,255,255,0.3);
            border-top: 2px solid #ffffff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            display: none;
        }}
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>STREETDYNO ANALYSE</h1>
        <a href="/logs" class="back-link">📂 ZURÜCK</a>
    </div>
    
    <div class="filename-banner">
        Datei: {fname} {title_suffix}
    </div>
    
    <div class="grid">
        <div class="card card-ps">
            <span class="label">Leistung</span>
            <span class="value">{max_ps:.1f} PS</span>
            <span class="sub">@{int(peak_ps_rpm)} U/min</span>
        </div>
        <div class="card card-nm">
            <span class="label">Drehmoment</span>
            <span class="value">{max_nm:.1f} Nm</span>
            <span class="sub">@{int(peak_nm_rpm)} U/min</span>
        </div>
        <div class="card card-afr">
            <span class="label">AFR (Mittel)</span>
            <span class="value">{avg_afr:.2f}</span>
            <span class="sub">während Dyno-Pull</span>
        </div>
        <div class="card card-egt">
            <span class="label">EGT (Peak)</span>
            <span class="value">{max_egt:.0f}°C</span>
            <span class="sub">bereinigter Spitzenwert</span>
        </div>
    </div>
    
    <div class="plot-container">
        <img src="/plots/{pname}" alt="Leistungsdiagramm">
    </div>
    
    <div class="cloud-section">
        <h3>☁️ Google Sheets Cloud Sync</h3>
        <p>Exportiere diese Leistungsdaten direkt von deinem Smartphone in deine Google Tabelle.</p>
        
        <div style="margin-bottom: 15px;">
            <label style="font-size: 0.8rem; color: #a1a1aa; font-weight: 600; display: block; margin-bottom: 6px;">GOOGLE APPS SCRIPT WEB APP URL:</label>
            <input type="text" id="scriptUrl" value="https://script.google.com/macros/s/AKfycbzTiR-DMjniaRbbbSBZh1DZpJ83q4dEBeMveRqgsjso18oCYDaGT1yRtrreacwmZ0ae/exec" 
                   style="width: 100%; padding: 12px; background: #121214; border: 1px solid #27272a; border-radius: 8px; color: #fff; font-family: monospace; font-size: 0.85rem; box-sizing: border-box;">
        </div>

        <button id="exportBtn" class="btn btn-primary" onclick="exportToSheets()"><span class="spinner" id="btnSpinner"></span><span id="btnText">☁️ In Tabelle eintragen</span></button>
        
        <div id="statusMsg" class="status-message"></div>
        
        <div style="margin-top: 20px;" class="info-box">
            <b>Einrichtung (Apps Script):</b>
            <ol>
                <li>Öffne deine Google Tabelle im Browser.</li>
                <li>Gehe auf <i>Erweiterungen &gt; Apps Script</i>.</li>
                <li>Füge den unten stehenden, optimierten Apps Script Code ein und speichere ihn.</li>
                <li>Klicke auf <i>Bereitstellen &gt; Neue Bereitstellung</i> (Typ: <b>Web-App</b>, Ausführen als: <b>Ich</b>, Wer hat Zugriff: <b>Jeder</b>).</li>
                <li>Kopiere die erzeugte Web-App-URL und füge sie oben ein.</li>
            </ol>
            <details style="margin-top: 10px; cursor: pointer;">
                <summary style="font-weight: 600; color: #ff9800; font-size: 0.85rem;">Apps Script Code anzeigen (Klicken)</summary>
                <textarea readonly style="width: 100%; height: 160px; background: #000; color: #00ffcc; border: 1px solid #333; font-family: monospace; font-size: 0.75rem; margin-top: 8px; padding: 8px; box-sizing: border-box; border-radius: 6px;" onclick="this.select()">
function doPost(e) {{
  try {{
    var payload = JSON.parse(e.postData.contents);
    var sheetName = payload.worksheet || "RawData";
    var data = payload.data;
    
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(sheetName);
    
    var headers = ["Time", "RPM", "RPM_smoothed", "AFR", "EGT", "EGT_cleaned", "Speed_kmh", "PS", "Nm"];
    
    if (!sheet) {{
      sheet = ss.insertSheet(sheetName);
    }} else {{
      sheet.clear();
    }}
    sheet.appendRow(headers);
    
    var rowsToWrite = [];
    for (var i = 0; i < data.length; i++) {{
      var row = data[i];
      var rowData = [];
      for (var j = 0; j < headers.length; j++) {{
        var key = headers[j];
        rowData.push(row[key] !== undefined && row[key] !== null ? row[key] : "");
      }}
      rowsToWrite.push(rowData);
    }}
    
    if (rowsToWrite.length > 0) {{
      sheet.getRange(2, 1, rowsToWrite.length, headers.length).setValues(rowsToWrite);
    }}
    
    return ContentService.createTextOutput(JSON.stringify({{success: true}}))
      .setMimeType(ContentService.MimeType.JSON);
  }} catch (err) {{
    return ContentService.createTextOutput(JSON.stringify({{success: false, error: err.toString()}}))
      .setMimeType(ContentService.MimeType.JSON);
  }}
}}
                </textarea>
            </details>
        </div>
    </div>

    <script>
        // Gespeicherte Apps Script URL laden
        document.addEventListener('DOMContentLoaded', () => {{
            const savedUrl = localStorage.getItem('google_apps_script_url');
            if (savedUrl) {{
                document.getElementById('scriptUrl').value = savedUrl;
            }}
        }});

        function exportToSheets() {{
            const scriptUrl = document.getElementById('scriptUrl').value.trim();
            const btn = document.getElementById('exportBtn');
            const spinner = document.getElementById('btnSpinner');
            const btnText = document.getElementById('btnText');
            const statusMsg = document.getElementById('statusMsg');
            
            if (!scriptUrl) {{
                statusMsg.style.display = 'block';
                statusMsg.className = 'status-message status-error';
                statusMsg.innerText = '✗ Bitte gib eine gültige Apps Script URL ein!';
                return;
            }}
            
            // URL lokal im Browser sichern
            localStorage.setItem('google_apps_script_url', scriptUrl);
            
            btn.disabled = true;
            spinner.style.display = 'inline-block';
            btnText.innerText = 'Sende Daten...';
            statusMsg.style.display = 'none';
            
            // 1. Daten vom Pi holen
            fetch(`/api/get_pull_data?file={fname}`)
                .then(r => {{
                    if (!r.ok) throw new Error('Pi-Verbindung fehlgeschlagen');
                    return r.json();
                }})
                .then(payload => {{
                    const baseName = payload.filename.split('.')[0];
                    const worksheetName = baseName.replace("dyno_log_", "");
                    
                    // 2. Clientseitig zu Google Apps Script senden
                    return fetch(scriptUrl, {{
                        method: 'POST',
                        mode: 'no-cors',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify({{
                            worksheet: worksheetName,
                            data: payload.data
                        }})
                    }});
                }})
                .then(() => {{
                    spinner.style.display = 'none';
                    btn.disabled = false;
                    btnText.innerText = '☁️ In Tabelle eintragen';
                    statusMsg.style.display = 'block';
                    statusMsg.className = 'status-message status-success';
                    statusMsg.innerText = '✓ Erfolgreich über dein Smartphone übertragen!';
                }})
                .catch(err => {{
                    spinner.style.display = 'none';
                    btn.disabled = false;
                    btnText.innerText = '☁️ In Tabelle eintragen';
                    statusMsg.style.display = 'block';
                    statusMsg.className = 'status-message status-error';
                    statusMsg.innerText = '✗ Fehler: ' + err.message;
                }});
        }}
    </script>
</body>
</html>
"""
        return render_template_string(html)
    except Exception as e:
        return f"<body style='background:#111; color:#fff; padding:20px;'><h3>Fehler bei der Analyse:</h3><pre>{str(e)}</pre><br><a href='/logs'>Zurück</a></body>"

@app.route('/api/get_pull_data')
def api_get_pull_data():
    fname = request.args.get('file')
    if not fname:
        return jsonify({"error": "No file selected."}), 400
    fpath = os.path.join(LOG_DIR, fname)
    if not os.path.exists(fpath):
        return jsonify({"error": "File not found."}), 404
        
    try:
        df = pd.read_csv(fpath)
        col_mapping = {col: col.strip() for col in df.columns}
        df = df.rename(columns=col_mapping)
        
        df = clean_egt_data(df)
        df = calculate_telemetry_metrics(df)
        trimmed_df, detected = detect_dyno_pull(df, min_rpm=2800.0, min_duration_sec=0.8, drop_threshold=400.0)
        detected_gear = int(trimmed_df.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed_df.columns else 3
        
        # Nur relevante Spalten für den Export auswählen
        cols = ['Time', 'RPM', 'RPM_smoothed', 'AFR', 'EGT', 'EGT_cleaned', 'Speed_kmh', 'PS', 'Nm', 'Detected_Gear']
        for col in cols:
            if col not in trimmed_df.columns:
                trimmed_df[col] = None
        
        # In Liste von Dictionaries konvertieren
        data = trimmed_df[cols].replace({np.nan: None}).to_dict(orient='records')
        return jsonify({
            "filename": fname,
            "detected": detected,
            "detected_gear": detected_gear,
            "data": data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/plots/<path:filename>')
def send_plot(filename): return send_from_directory(PLOT_DIR, filename)

global_logger = None

def hardware_loop():
    global global_logger
    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
    global_logger = logger
    gps.start()
    l_data_t = last_upd = time.time()
    current_filtered_rpm = last_raw_rpm = current_filtered_afr = 0
    
    ser = None
    while True:
        if ser is None or not ser.is_open:
            try: ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
            except: time.sleep(1); continue

        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('$'):
                    l_data_t = time.time()
                    parts = line[1:].split(';')
                    if len(parts) >= 3:
                        r_rpm, r_afr, r_egt = float(parts[0]), float(parts[1]), float(parts[2])
                        
                        # --- DELTA FILTER ---
                        if last_raw_rpm > 0 and abs(r_rpm - last_raw_rpm) > 2500 and last_raw_rpm < 3000:
                            raw_to_use = last_raw_rpm
                        else:
                            raw_to_use = r_rpm; last_raw_rpm = r_rpm

                        # --- ANALOG SMOOTHING & SMART ROUNDING ---
                        target_rpm = raw_to_use * RPM_MULTIPLIER
                        current_filtered_rpm = (current_filtered_rpm * (1 - RPM_ALPHA)) + (target_rpm * RPM_ALPHA)
                        ui_rpm = smart_round(current_filtered_rpm)
                        
                        # --- AFR TACHO EMULATOR ---
                        p_afr = r_afr + AFR_OFFSET
                        effective_alpha = AFR_ALPHA if p_afr < AFR_MAX_VALID else (AFR_ALPHA / 2)
                        
                        if current_filtered_afr == 0: current_filtered_afr = p_afr
                        current_filtered_afr = (current_filtered_afr * (1 - effective_alpha)) + (p_afr * effective_alpha)
                        
                        p_egt = r_egt + EGT_OFFSET
                        g = gps.get_data(); spd = g.speed_kmh if g else 0.0
                        if g: sync_time_with_gps(g) # TIME SYNC VERSUCH
                        
                        telemetry.update({"rpm":ui_rpm, "afr":current_filtered_afr, "egt":p_egt, "speed":spd, "fix":g.fix if g else False})
                        
                        # LOGGING (Auto-Trigger oder Manuell über Web UI)
                        if not logger.is_logging:
                            if current_filtered_rpm > AUTO_START_RPM and spd > MIN_SPEED_KMH:
                                log_time = g.timestamp if (g and g.fix and g.timestamp) else datetime.now()
                                log_filename = os.path.join(LOG_DIR, f"dyno_log_{log_time.strftime('%Y%m%d-%H%M%S')}.csv")
                                logger.start(log_filename); telemetry["status"]="🔴 REC"
                        else:
                            logger.log(round(current_filtered_rpm, 1), current_filtered_afr, p_egt, spd, g.lat if g else 0.0, g.lon if g else 0.0, g.fix if g else False)
                            
                            # Auto-Stop nur wenn Drehzahl und Speed auf Standgas abfallen
                            if current_filtered_rpm < 1100 and spd < 1.0:
                                logger.stop(); telemetry["status"]="🟢 IDLE"
        except: ser = None

        if time.time() - l_data_t > 1.0: 
            telemetry["rpm"] = current_filtered_rpm = last_raw_rpm = current_filtered_afr = 0
            
        if time.time() - last_upd > 0.1:
            oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V5.1", telemetry["fix"], logger.is_logging)
            last_upd = time.time()
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8080)
