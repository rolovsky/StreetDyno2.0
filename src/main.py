import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request, make_response

from data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    plot_telemetry,
    export_to_google_sheets
)
from data.jetting_advisor import analyze_carb_jetting
from config import load_carb_setup, save_carb_setup, CARB_SETUP

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

# --- HIGH-CONTRAST OUTDOOR LIVE COCKPIT HUD (IPHONE 15 PRO MAX OPTIMIZED) ---
DASH_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="StreetDyno">
    <meta name="theme-color" content="#000000">
    <meta name="format-detection" content="telephone=no">
    <title>StreetDyno 2.0 - Live HUD</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&family=JetBrains+Mono:wght@700;800&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        :root {
            --sat: env(safe-area-inset-top, 20px);
            --sab: env(safe-area-inset-bottom, 20px);
            --sal: env(safe-area-inset-left, 12px);
            --sar: env(safe-area-inset-right, 12px);
        }

        html, body {
            background: #000000;
            color: #ffffff;
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
            overflow: hidden;
            width: 100vw;
            height: 100vh;
            height: -webkit-fill-available;
            user-select: none;
            -webkit-user-select: none;
            -webkit-touch-callout: none;
        }

        body {
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            padding: max(12px, var(--sat)) max(14px, var(--sar)) max(12px, var(--sab)) max(14px, var(--sal));
        }

        /* Top Bar & Dynamic Island Clearance */
        .top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 14px;
            background: rgba(18, 18, 22, 0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            margin-bottom: 8px;
            font-size: 0.85rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }

        .status-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 5px 12px;
            border-radius: 30px;
            background: #141418;
            border: 1px solid #2a2a32;
        }
        .rec-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #00e676;
            box-shadow: 0 0 8px rgba(0, 230, 118, 0.4);
            transition: all 0.2s;
        }
        .rec-dot.active {
            background: #ff1744;
            box-shadow: 0 0 16px #ff1744;
            animation: pulse-dot 0.6s infinite alternate;
        }
        @keyframes pulse-dot { from { opacity: 0.3; } to { opacity: 1; } }

        .heartbeat-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #00ffcc;
            opacity: 0.25;
            transition: opacity 0.1s;
        }
        .heartbeat-dot.beat { opacity: 1; box-shadow: 0 0 8px #00ffcc; }

        .top-actions {
            display: flex;
            gap: 8px;
        }
        .btn-icon {
            background: #1c1c22;
            border: 1px solid #33333e;
            color: #fff;
            padding: 6px 14px;
            border-radius: 10px;
            font-weight: 800;
            font-size: 0.8rem;
            text-decoration: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            -webkit-tap-highlight-color: transparent;
        }
        .btn-icon:active { background: #333; transform: scale(0.96); }

        /* Rev Bar / Shift Light */
        .rev-bar-container {
            width: 100%;
            height: 16px;
            background: #111116;
            border-radius: 8px;
            border: 1px solid #22222a;
            overflow: hidden;
            margin-bottom: 10px;
            position: relative;
            box-shadow: inset 0 2px 6px rgba(0,0,0,0.8);
        }
        .rev-bar-fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #00e676 0%, #ffea00 60%, #ff9100 80%, #ff1744 100%);
            transition: width 0.08s linear;
        }
        .shift-light {
            position: fixed;
            top: 0; left: 0; right: 0;
            height: max(8px, var(--sat));
            background: transparent;
            z-index: 9999;
            pointer-events: none;
        }
        .shift-light.flash {
            background: #ff1744;
            box-shadow: 0 0 40px 15px #ff1744;
            animation: flash-border 0.1s infinite;
        }
        @keyframes flash-border { 50% { opacity: 0.15; } }

        /* Main Grid (Portrait 2x2 & Landscape 4x1 for iPhone 15 Pro Max) */
        .hud-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            flex-grow: 1;
            margin-bottom: 10px;
        }
        @media (orientation: landscape) {
            .hud-grid {
                grid-template-columns: repeat(4, 1fr);
                gap: 10px;
            }
        }

        .hud-card {
            background: linear-gradient(165deg, #131318 0%, #0a0a0d 100%);
            border: 2px solid rgba(255, 255, 255, 0.07);
            border-radius: 22px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            text-align: center;
            position: relative;
            box-shadow: 0 8px 30px rgba(0,0,0,0.7);
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
            font-size: clamp(3.2rem, 9.5vw, 4.8rem);
            font-weight: 800;
            line-height: 0.95;
            margin: 6px 0;
            letter-spacing: -0.05em;
        }
        .hud-card .card-unit {
            font-size: 0.8rem;
            font-weight: 800;
            color: #52525b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* High-Contrast Neon Accents */
        .card-speed .card-value { color: #00ffcc; text-shadow: 0 0 35px rgba(0,255,204,0.35); }
        .card-rpm .card-value { color: #ff9800; text-shadow: 0 0 35px rgba(255,152,0,0.35); }
        .card-afr .card-value { color: #00e676; text-shadow: 0 0 35px rgba(0,230,118,0.35); }
        .card-egt .card-value { color: #ffd600; text-shadow: 0 0 35px rgba(255,214,0,0.35); }

        /* Danger Alarms */
        @keyframes danger-blink {
            0% { background: #131318; border-color: #ff1744; }
            50% { background: #3b0811; border-color: #ff1744; box-shadow: 0 0 40px rgba(255,23,68,0.8); }
            100% { background: #131318; border-color: #ff1744; }
        }
        .card-danger {
            animation: danger-blink 0.35s infinite !important;
        }
        .card-danger .card-value {
            color: #ff1744 !important;
            text-shadow: 0 0 35px #ff1744 !important;
        }

        /* Bottom Action Bar (Glove-Friendly Touch Targets) */
        .bottom-bar {
            display: flex;
            gap: 10px;
            min-height: 56px;
        }
        .btn-rec {
            flex: 2.2;
            padding: 16px;
            border-radius: 16px;
            font-size: 1.05rem;
            font-weight: 900;
            letter-spacing: 0.04em;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: #18181f;
            color: #00ffcc;
            border: 2px solid #00ffcc;
            box-shadow: 0 4px 20px rgba(0,255,204,0.2);
            transition: all 0.15s;
            -webkit-tap-highlight-color: transparent;
        }
        .btn-rec.recording {
            background: #ff1744;
            color: #ffffff;
            border: 2px solid #ff5252;
            box-shadow: 0 0 30px rgba(255,23,68,0.7);
            animation: pulse-btn 1s infinite alternate;
        }
        @keyframes pulse-btn { from { transform: scale(1); } to { transform: scale(0.98); } }
        .btn-rec:active { transform: scale(0.96); }

        .btn-nav {
            flex: 1;
            padding: 16px;
            border-radius: 16px;
            font-size: 0.95rem;
            font-weight: 800;
            background: #18181f;
            color: #ffffff;
            border: 2px solid #2a2a34;
            text-decoration: none;
            text-align: center;
            display: flex;
            align-items: center;
            justify-content: center;
            -webkit-tap-highlight-color: transparent;
        }
        .btn-nav:active { background: #272730; transform: scale(0.96); }
    </style>
</head>
<body>
    <div id="shiftLight" class="shift-light"></div>

    <div class="top-bar">
        <div class="status-pill">
            <div id="recDot" class="rec-dot"></div>
            <span id="statusText">V5.1 READY</span>
            <div id="heartbeat" class="heartbeat-dot"></div>
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
                <span id="gearBadge">RPM</span>
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
        <a href="/tuning" class="btn-nav">🔧 VERGASER</a>
    </div>

    <script>
        let wakeLock = null;

        // Robust WakeLock for iOS Safari & Modern Browsers
        function initWakeLock() {
            try {
                if ('wakeLock' in navigator && navigator.wakeLock.request) {
                    navigator.wakeLock.request('screen')
                        .then(lock => { wakeLock = lock; })
                        .catch(() => {});
                }
            } catch (err) {}
        }
        document.addEventListener('visibilitychange', () => {
            if (wakeLock !== null && document.visibilityState === 'visible') {
                initWakeLock();
            }
        });
        window.addEventListener('touchstart', initWakeLock, { passive: true, once: true });
        window.addEventListener('click', initWakeLock, { once: true });
        initWakeLock();

        // Cross-Browser Fullscreen (iOS Safari Fallback)
        function toggleFullScreen() {
            try {
                const doc = document.documentElement;
                if (doc.requestFullscreen) {
                    if (!document.fullscreenElement) {
                        doc.requestFullscreen().catch(() => {});
                    } else {
                        document.exitFullscreen().catch(() => {});
                    }
                } else if (doc.webkitRequestFullscreen) {
                    if (!document.webkitFullscreenElement) {
                        doc.webkitRequestFullscreen();
                    } else {
                        doc.webkitExitFullscreen();
                    }
                }
            } catch (e) {}
        }

        function toggleLogging() {
            fetch('/api/toggle_logging?t=' + Date.now(), { cache: 'no-store' })
                .then(r => r.json())
                .then(d => {
                    updateRecState(d.is_logging);
                })
                .catch(e => console.error(e));
        }

        function updateRecState(isLogging) {
            const btn = document.getElementById('recBtn');
            const dot = document.getElementById('recDot');
            if (!btn || !dot) return;
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

        // Live 10Hz Polling with Cache Busting
        let beatState = false;
        setInterval(() => {
            fetch('/api/data?t=' + Date.now(), { cache: 'no-store' })
                .then(r => r.json())
                .then(d => {
                    if (!d) return;

                    // Heartbeat toggle
                    const hb = document.getElementById('heartbeat');
                    if (hb) {
                        beatState = !beatState;
                        if (beatState) hb.classList.add('beat');
                        else hb.classList.remove('beat');
                    }

                    const rpm = (typeof d.rpm === 'number') ? d.rpm : 0;
                    const speed = (typeof d.speed === 'number') ? d.speed : 0.0;
                    const afr = (typeof d.afr === 'number') ? d.afr : 0.0;
                    const egt = (typeof d.egt === 'number') ? d.egt : 0.0;

                    // Numeric values
                    const speedEl = document.getElementById('speed');
                    const rpmEl = document.getElementById('rpm');
                    const afrEl = document.getElementById('afr');
                    const egtEl = document.getElementById('egt');

                    if (speedEl) speedEl.innerText = speed.toFixed(1);
                    if (rpmEl) rpmEl.innerText = rpm.toFixed(0);
                    if (afrEl) afrEl.innerText = afr.toFixed(2);
                    if (egtEl) egtEl.innerText = egt.toFixed(0);

                    // Rev Bar & Shift Light (>8000 RPM)
                    const revBar = document.getElementById('revBar');
                    if (revBar) {
                        const revPct = Math.min(100, Math.max(0, (rpm / 9000) * 100));
                        revBar.style.width = revPct + '%';
                    }

                    const shiftLight = document.getElementById('shiftLight');
                    if (shiftLight) {
                        if (rpm >= 8000) {
                            shiftLight.classList.add('flash');
                        } else {
                            shiftLight.classList.remove('flash');
                        }
                    }

                    // AFR Color Zone & Lean Alert (>14.5 under load)
                    const cardAfr = document.getElementById('cardAfr');
                    const afrZone = document.getElementById('afrZone');
                    if (cardAfr && afrZone) {
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
                                cardAfr.style.borderColor = 'rgba(255, 255, 255, 0.07)';
                            }
                        }
                    }

                    // EGT Overheat Alert (>630°C)
                    const cardEgt = document.getElementById('cardEgt');
                    if (cardEgt) {
                        if (egt >= 630.0) {
                            cardEgt.classList.add('card-danger');
                        } else {
                            cardEgt.classList.remove('card-danger');
                        }
                    }

                    // GPS & Status
                    const gpsText = document.getElementById('gpsText');
                    if (gpsText) {
                        gpsText.innerText = d.fix ? '3D FIX' : 'SUCHE...';
                        gpsText.style.color = d.fix ? '#00e676' : '#ffea00';
                    }

                    const statusText = document.getElementById('statusText');
                    const statusStr = (typeof d.status === 'string') ? d.status : 'IDLE';
                    if (statusText) statusText.innerText = statusStr;

                    const isLogging = statusStr.indexOf('REC') !== -1;
                    updateRecState(isLogging);
                })
                .catch(err => {
                    console.error('Fetch error:', err);
                });
        }, 150);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    resp = make_response(render_template_string(DASH_HTML))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/api/data')
def api_data():
    resp = jsonify(telemetry)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

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
        resp = jsonify({"is_logging": global_logger.is_logging, "status": telemetry["status"]})
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
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


@app.route('/api/update_carb_setup', methods=['GET', 'POST'])
def api_update_carb_setup():
    try:
        if request.method == 'POST':
            data = request.get_json(force=True, silent=True) or request.form.to_dict()
        else:
            data = request.args.to_dict()
            
        if not data:
            return jsonify({"status": "error", "message": "Keine Daten empfangen"}), 400
            
        # Parse numeric types where appropriate
        cleaned = {}
        for k, v in data.items():
            if k in ['main_jet_hd', 'air_corrector_hlkd']:
                try: cleaned[k] = int(float(v))
                except: cleaned[k] = v
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

@app.route('/tuning')
def tuning_dashboard():
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
            analysis = {"valid": False, "error": str(e)}
            
    # Format zone cards HTML
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

    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
        <title>StreetDyno 2.0 - Vergaser Setup</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800;900&display=swap" rel="stylesheet">
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
            .section-card {{
                background: #16161a;
                border: 2px solid #27272e;
                border-radius: 16px;
                padding: 15px;
                margin-bottom: 15px;
            }}
            .form-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 10px;
                margin-top: 10px;
            }}
            .form-group {{
                display: flex;
                flex-direction: column;
                gap: 4px;
            }}
            .form-group.full {{ grid-column: span 2; }}
            label {{ font-size: 0.75rem; font-weight: 700; color: #888; text-transform: uppercase; }}
            input, select {{
                background: #0d0d11;
                border: 1px solid #333;
                color: #fff;
                padding: 10px;
                border-radius: 8px;
                font-family: monospace;
                font-size: 0.95rem;
                font-weight: bold;
            }}
            input:focus {{ border-color: #00ffcc; outline: none; }}
            .btn-save {{
                width: 100%;
                background: #00ffcc;
                color: #000;
                border: none;
                padding: 14px;
                border-radius: 10px;
                font-weight: 900;
                font-size: 1.0rem;
                cursor: pointer;
                margin-top: 15px;
                transition: transform 0.1s;
            }}
            .btn-save:active {{ transform: scale(0.98); }}
            .toast {{
                position: fixed;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                background: #00e676;
                color: #000;
                padding: 12px 24px;
                border-radius: 30px;
                font-weight: 800;
                box-shadow: 0 4px 20px rgba(0,230,118,0.5);
                display: none;
                z-index: 9999;
            }}
        </style>
    </head>
    <body>
        <div id="toast" class="toast">✅ Setup gespeichert!</div>

        <div class="header">
            <h1>🔧 VERGASER SETUP</h1>
            <a href="/" class="back-btn">⬅️ COCKPIT</a>
        </div>

        <!-- Setup Form -->
        <div class="section-card">
            <h2 style="font-size:1.1rem; color:#00ffcc; font-weight:800; margin-bottom:6px;">⚙️ Aktuell montierte Bedüsung</h2>
            <div style="font-size:0.75rem; color:#888; margin-bottom:10px;">Passe deine Düsen hier an. Der Jetting Advisor nutzt diese Werte als Referenz.</div>

            <form id="carbForm" onsubmit="saveSetup(event)">
                <div class="form-grid">
                    <div class="form-group">
                        <label>Hauptdüse (HD)</label>
                        <input type="number" name="main_jet_hd" value="{carb.get('main_jet_hd', 135)}" required>
                    </div>
                    <div class="form-group">
                        <label>Nebendüse (ND)</label>
                        <input type="text" name="idle_jet_nd" value="{carb.get('idle_jet_nd', '60/160')}" required>
                    </div>
                    <div class="form-group">
                        <label>HLKD (Luftkorrektur)</label>
                        <input type="number" name="air_corrector_hlkd" value="{carb.get('air_corrector_hlkd', 160)}" required>
                    </div>
                    <div class="form-group">
                        <label>Mischrohr</label>
                        <input type="text" name="emulsion_tube" value="{carb.get('emulsion_tube', 'Lemarxon x234')}" required>
                    </div>
                    <div class="form-group">
                        <label>Gasschieber</label>
                        <input type="text" name="throttle_slide" value="{carb.get('throttle_slide', 'Lemarxon Low')}" required>
                    </div>
                    <div class="form-group">
                        <label>Ansaugung / Trichter</label>
                        <input type="text" name="intake_funnel" value="{carb.get('intake_funnel', 'Polini Venturi Trichter')}" required>
                    </div>
                    <div class="form-group full">
                        <label>Vergaser-Typ</label>
                        <input type="text" name="carburetor_type" value="{carb.get('carburetor_type', 'BGM 24/24 Fastflow')}">
                    </div>
                    <div class="form-group full">
                        <label>Notizen / Setup</label>
                        <input type="text" name="notes" value="{carb.get('notes', 'VMC 177 / 60mm Welle')}">
                    </div>
                </div>
                <button type="submit" class="btn-save">💾 SETUP SPEICHERN</button>
            </form>
        </div>

        <!-- Jetting Advisor Diagnosis from Latest Pull -->
        <div class="section-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <h2 style="font-size:1.1rem; color:#ff9800; font-weight:800;">🔬 Live Bedüsungs-Diagnose</h2>
                <div style="font-size:0.75rem; color:#888; font-family:monospace;">{latest_file if latest_file else ''}</div>
            </div>

            <div style="background:#1f1f26; padding:10px 12px; border-radius:8px; margin-bottom:12px;">
                <strong style="color:#fff; font-size:0.85rem;">
                    {analysis.get('overall_verdict', 'Keine Daten') if analysis else 'Keine Daten'}
                </strong>
            </div>

            {zone_cards_html}
        </div>

        <script>
            function saveSetup(e) {{
                e.preventDefault();
                const form = document.getElementById('carbForm');
                const formData = new FormData(form);
                const obj = {{}};
                formData.forEach((value, key) => {{ obj[key] = value; }});

                fetch('/api/update_carb_setup', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(obj)
                }})
                .then(r => r.json())
                .then(d => {{
                    const toast = document.getElementById('toast');
                    toast.style.display = 'block';
                    setTimeout(() => {{ toast.style.display = 'none'; }}, 2500);
                }})
                .catch(err => alert('Fehler beim Speichern: ' + err));
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@app.route('/dyno_sheet')
def dyno_sheet_report():
    fname = request.args.get('file')
    if not fname: return "No file selected."
    fpath = os.path.join(LOG_DIR, fname)
    if not os.path.exists(fpath): return "File not found."

    slope_param = request.args.get('slope', 'auto')
    try: temp_param = float(request.args.get('temp', 20.0))
    except: temp_param = 20.0
    try: pressure_param = float(request.args.get('pressure', 1013.25))
    except: pressure_param = 1013.25
    norm_param = request.args.get('norm', 'DIN70020')

    try:
        df = pd.read_csv(fpath)
        df.columns = [c.strip() for c in df.columns]
        df = clean_egt_data(df)
        df = calculate_telemetry_metrics(df, slope_percent=slope_param, temp_c=temp_param, pressure_hpa=pressure_param, norm_standard=norm_param)
        trimmed, _ = detect_dyno_pull(df, slope_percent=slope_param, temp_c=temp_param, pressure_hpa=pressure_param, norm_standard=norm_param)

        carb = load_carb_setup()
        carb_diag = analyze_carb_jetting(trimmed, carb)

        gear = int(trimmed.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed.columns else 3
        max_ps = float(trimmed['PS'].max()) if 'PS' in trimmed.columns else 0.0
        max_ps_raw = float(trimmed['PS_Raw'].max()) if 'PS_Raw' in trimmed.columns else max_ps
        max_nm = float(trimmed['Nm'].max()) if 'Nm' in trimmed.columns else 0.0
        avg_afr = float(trimmed['AFR'].mean()) if 'AFR' in trimmed.columns else 0.0
        max_egt = float(trimmed['EGT_cleaned'].max()) if 'EGT_cleaned' in trimmed.columns else 0.0
        
        peak_ps_idx = trimmed['PS'].idxmax() if 'PS' in trimmed.columns else None
        peak_ps_rpm = trimmed.loc[peak_ps_idx, 'RPM_smoothed'] if (peak_ps_idx is not None and not pd.isna(peak_ps_idx)) else 0.0
        
        peak_nm_idx = trimmed['Nm'].idxmax() if 'Nm' in trimmed.columns else None
        peak_nm_rpm = trimmed.loc[peak_nm_idx, 'RPM_smoothed'] if (peak_nm_idx is not None and not pd.isna(peak_nm_idx)) else 0.0

        k_norm = float(trimmed['Weather_K_Norm'].iloc[0]) if 'Weather_K_Norm' in trimmed.columns else 1.0
        detected_slope = float(trimmed['Slope_Pct'].iloc[0]) if 'Slope_Pct' in trimmed.columns else 0.0

        chart_data = trimmed[['RPM_smoothed', 'PS', 'Nm', 'AFR']].dropna().sort_values('RPM_smoothed').to_dict(orient='records')
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%d.%m.%Y - %H:%M:%S')

        html = f"""
        <!DOCTYPE html>
        <html lang="de">
        <head>
            <meta charset="UTF-8">
            <title>StreetDyno 2.0 - Prüfstandsbericht ({fname})</title>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&family=JetBrains+Mono:wght@700;800&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; }}
                body {{
                    background: #ffffff;
                    color: #111111;
                    font-family: 'Outfit', sans-serif;
                    padding: 25px;
                    max-width: 900px;
                    margin: 0 auto;
                }}
                .print-actions {{
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 20px;
                    padding-bottom: 12px;
                    border-bottom: 2px solid #eee;
                }}
                .btn-print {{
                    background: #000;
                    color: #fff;
                    padding: 10px 20px;
                    border-radius: 8px;
                    font-weight: 800;
                    border: none;
                    cursor: pointer;
                    font-size: 0.95rem;
                }}
                .btn-back {{
                    background: #eee;
                    color: #333;
                    padding: 10px 20px;
                    border-radius: 8px;
                    font-weight: 800;
                    text-decoration: none;
                    font-size: 0.95rem;
                }}
                .report-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: flex-start;
                    border-bottom: 3px solid #000;
                    padding-bottom: 15px;
                    margin-bottom: 20px;
                }}
                .logo-title {{ font-size: 1.8rem; font-weight: 900; letter-spacing: -0.03em; }}
                .meta-table {{ font-size: 0.85rem; color: #555; text-align: right; }}
                
                .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }}
                .box {{
                    border: 1px solid #ccc;
                    border-radius: 8px;
                    padding: 12px 15px;
                    background: #fafafa;
                }}
                .box-title {{ font-size: 0.8rem; font-weight: 800; text-transform: uppercase; color: #666; margin-bottom: 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
                .spec-row {{ display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 4px; }}
                .spec-val {{ font-weight: bold; color: #000; font-family: monospace; }}

                .results-grid {{
                    display: grid;
                    grid-template-columns: repeat(4, 1fr);
                    gap: 10px;
                    margin-bottom: 20px;
                }}
                .result-card {{
                    background: #000;
                    color: #fff;
                    padding: 12px;
                    border-radius: 8px;
                    text-align: center;
                }}
                .res-label {{ font-size: 0.75rem; text-transform: uppercase; color: #aaa; font-weight: 700; }}
                .res-val {{ font-size: 1.8rem; font-weight: 900; font-family: 'JetBrains Mono', monospace; margin: 4px 0; color: #00ffcc; }}
                .res-sub {{ font-size: 0.75rem; color: #ccc; }}

                .chart-container {{
                    height: 380px;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    padding: 10px;
                    margin-bottom: 20px;
                    background: #fff;
                }}
                
                @media print {{
                    .print-actions {{ display: none; }}
                    body {{ padding: 0; }}
                    @page {{ size: A4; margin: 1.2cm; }}
                }}
            </style>
        </head>
        <body>
            <div class="print-actions">
                <a href="/analyze?file={fname}&slope={slope_param}&temp={temp_param}&pressure={pressure_param}&norm={norm_param}" class="btn-back">⬅️ ZURÜCK</a>
                <button class="btn-print" onclick="window.print()">🖨️ DRUCKEN / ALS PDF SPEICHERN</button>
            </div>

            <div class="report-header">
                <div>
                    <div class="logo-title">STREETDYNO 2.0</div>
                    <div style="font-weight:700; color:#444; font-size:1.05rem;">LEISTUNGSPRÜFSTANDSBERICHT</div>
                </div>
                <div class="meta-table">
                    <div><b>Datum:</b> {mtime}</div>
                    <div><b>Datei:</b> {fname}</div>
                    <div><b>Messgang:</b> {gear}. Gang</div>
                </div>
            </div>

            <div class="results-grid">
                <div class="result-card">
                    <div class="res-label">Pmax ({norm_param})</div>
                    <div class="res-val">{max_ps:.1f} PS</div>
                    <div class="res-sub">@{int(peak_ps_rpm)} U/min (Raw: {max_ps_raw:.1f})</div>
                </div>
                <div class="result-card">
                    <div class="res-label">Mmax ({norm_param})</div>
                    <div class="res-val" style="color:#ff9800;">{max_nm:.1f} Nm</div>
                    <div class="res-sub">@{int(peak_nm_rpm)} U/min</div>
                </div>
                <div class="result-card">
                    <div class="res-label">Mittel AFR</div>
                    <div class="res-val" style="color:#00e676;">{avg_afr:.2f}</div>
                    <div class="res-sub">im Volllastzug</div>
                </div>
                <div class="result-card">
                    <div class="res-label">Peak EGT</div>
                    <div class="res-val" style="color:#ffd600;">{max_egt:.0f}°C</div>
                    <div class="res-sub">Abgastemperatur</div>
                </div>
            </div>

            <div class="chart-container">
                <canvas id="dynoCanvas"></canvas>
            </div>

            <div class="grid-2">
                <div class="box">
                    <div class="box-title">⚙️ FAHRZEUG & VERGASER-SETUP</div>
                    <div class="spec-row"><span>Fahrzeug:</span><span class="spec-val">Vespa PX 125 (VMC 177)</span></div>
                    <div class="spec-row"><span>Gesamtmasse:</span><span class="spec-val">190.0 kg (Roller + Fahrer)</span></div>
                    <div class="spec-row"><span>Vergaser:</span><span class="spec-val">{carb.get('carburetor_type', 'BGM 24/24')}</span></div>
                    <div class="spec-row"><span>Bedüsung:</span><span class="spec-val">HD {carb.get('main_jet_hd', 135)} | ND {carb.get('idle_jet_nd', '60/160')} | HLKD {carb.get('air_corrector_hlkd', 160)}</span></div>
                    <div class="spec-row"><span>Mischrohr / Schieber:</span><span class="spec-val">{carb.get('emulsion_tube', 'x234')} | {carb.get('throttle_slide', 'Low')}</span></div>
                    <div class="spec-row"><span>Ansaugung / Auspuff:</span><span class="spec-val">{carb.get('intake_funnel', 'Venturi')} | {carb.get('exhaust', 'Polini Box')}</span></div>
                </div>

                <div class="box">
                    <div class="box-title">🌤️ ATMOSPHÄRE & KORREKTURFAKTOREN</div>
                    <div class="spec-row"><span>Korrektur-Norm:</span><span class="spec-val">{norm_param}</span></div>
                    <div class="spec-row"><span>Temperatur:</span><span class="spec-val">{temp_param:.1f}°C</span></div>
                    <div class="spec-row"><span>Luftdruck:</span><span class="spec-val">{pressure_param:.1f} hPa</span></div>
                    <div class="spec-row"><span>Korrekturfaktor (k):</span><span class="spec-val">{k_norm:.3f} ({((k_norm-1.0)*100):+.1f}%)</span></div>
                    <div class="spec-row"><span>Straßenneigung:</span><span class="spec-val">{detected_slope:+.1f}%</span></div>
                    <div class="spec-row"><span>Vergaser-Status:</span><span class="spec-val" style="color:#000;">{carb_diag.get('overall_status', 'OK')}</span></div>
                </div>
            </div>

            <script>
                const d = {chart_data};
                const ctx = document.getElementById('dynoCanvas').getContext('2d');
                new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        datasets: [
                            {{
                                label: 'Leistung (PS)',
                                data: d.map(p => ({{ x: p.RPM_smoothed, y: p.PS }})),
                                borderColor: '#0088cc',
                                backgroundColor: '#0088cc',
                                borderWidth: 3,
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y'
                            }},
                            {{
                                label: 'Drehmoment (Nm)',
                                data: d.map(p => ({{ x: p.RPM_smoothed, y: p.Nm }})),
                                borderColor: '#e65100',
                                backgroundColor: '#e65100',
                                borderWidth: 2.5,
                                borderDash: [4, 4],
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y1'
                            }},
                            {{
                                label: 'AFR (Lambda)',
                                data: d.map(p => ({{ x: p.RPM_smoothed, y: p.AFR }})),
                                borderColor: '#c2185b',
                                backgroundColor: '#c2185b',
                                borderWidth: 1.5,
                                pointRadius: 0,
                                tension: 0.3,
                                yAxisID: 'y2'
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
                                title: {{ display: true, text: 'Drehzahl (U/min)', font: {{ weight: 'bold' }} }},
                                grid: {{ color: '#eee' }}
                            }},
                            y: {{
                                type: 'linear',
                                position: 'left',
                                title: {{ display: true, text: 'Leistung (PS)', font: {{ weight: 'bold' }}, color: '#0088cc' }},
                                min: 0,
                                ticks: {{ color: '#0088cc' }}
                            }},
                            y1: {{
                                type: 'linear',
                                position: 'right',
                                title: {{ display: true, text: 'Drehmoment (Nm)', font: {{ weight: 'bold' }}, color: '#e65100' }},
                                min: 0,
                                grid: {{ drawOnChartArea: false }},
                                ticks: {{ color: '#e65100' }}
                            }},
                            y2: {{
                                type: 'linear',
                                position: 'right',
                                display: false,
                                min: 9.0,
                                max: 18.0
                            }}
                        }},
                        plugins: {{
                            legend: {{ labels: {{ font: {{ weight: 'bold', size: 11 }} }} }}
                        }}
                    }}
                }});
            </script>
        </body>
        </html>
        """
        return render_template_string(html)
    except Exception as e:
        return f"<h3>Fehler beim Erstellen des Dyno-Sheets: {str(e)}</h3><br><a href='/analyze?file={fname}'>Zurück</a>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

@app.route('/analyze')
def analyze_file():
    fname = request.args.get('file')
    slope_param = request.args.get('slope', 'auto')
    try: temp_param = float(request.args.get('temp', 20.0))
    except: temp_param = 20.0
    try: pressure_param = float(request.args.get('pressure', 1013.25))
    except: pressure_param = 1013.25
    norm_param = request.args.get('norm', 'DIN70020')
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
        df = calculate_telemetry_metrics(df, slope_percent=slope_param, temp_c=temp_param, pressure_hpa=pressure_param, norm_standard=norm_param)
        
        # Dyno-Pull automatisch erkennen
        trimmed_df, detected = detect_dyno_pull(df, min_rpm=2800.0, min_duration_sec=0.8, drop_threshold=400.0, slope_percent=slope_param, temp_c=temp_param, pressure_hpa=pressure_param, norm_standard=norm_param)
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
        
        # Straßenneigung & Hangabtriebskompensation
        detected_slope = float(trimmed_df['Slope_Pct'].iloc[0]) if 'Slope_Pct' in trimmed_df.columns else 0.0
        avg_slope_ps = float(trimmed_df['Slope_Power_PS'].mean()) if 'Slope_Power_PS' in trimmed_df.columns else 0.0
        
        # DIN 70020 Wetter-Normierung
        k_norm = float(trimmed_df['Weather_K_Norm'].iloc[0]) if 'Weather_K_Norm' in trimmed_df.columns else 1.0
        ps_raw = float(trimmed_df['PS_Raw'].max()) if 'PS_Raw' in trimmed_df.columns else max_ps
        
        # GPS-Koordinaten für Smartphone Open-Meteo Fetch
        gps_lat = 0.0
        gps_lon = 0.0
        if 'Lat' in df.columns and 'Lon' in df.columns:
            valid_coords = df[(df['Lat'] != 0.0) & (df['Lon'] != 0.0)]
            if len(valid_coords) > 0:
                gps_lat = float(valid_coords['Lat'].iloc[0])
                gps_lon = float(valid_coords['Lon'].iloc[0])
        
        # Vergaser-Bedüsungs-Diagnose
        carb_setup = load_carb_setup()
        carb_diag = analyze_carb_jetting(trimmed_df, carb_setup)
        
        diag_rows = ""
        if carb_diag.get("valid"):
            for z in carb_diag.get("zones", []):
                badge_bg = "#00e676" if "PERFEKT" in z["status_text"] else ("#ff1744" if "KRITISCH" in z["status_text"] else ("#ff9800" if "LEICHT MAGER" in z["status_text"] else "#29b6f6"))
                diag_rows += f"""
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
            diag_rows = "<div style='color:#888; font-size:0.8rem;'>Keine verwertbaren AFR-Punkte für die Vergaserdiagnose.</div>"

        
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
    
    <!-- DIN 70020 Weather & Slope Normalization Section -->
    <div style="background:#141418; border:2px solid #27272e; border-radius:14px; padding:14px; margin-bottom:15px;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:12px;">
            <div>
                <strong style="color:#00ffcc; font-size:0.95rem;">🌤️ DIN 70020 WETTER-NORM:</strong>
                <span style="font-family:monospace; font-weight:bold; color:#fff; font-size:1.0rem; margin-left:6px;">k = {k_norm:.3f}</span>
                <span style="font-size:0.8rem; color:#aaa; margin-left:4px;">({((k_norm-1.0)*100):+.1f}%)</span>
            </div>
            <a href="/dyno_sheet?file={fname}&slope={slope_param}&temp={temp_param}&pressure={pressure_param}&norm={norm_param}" target="_blank" 
               style="background:#00ffcc; color:#000; padding:6px 12px; border-radius:8px; font-weight:800; font-size:0.8rem; text-decoration:none; display:flex; align-items:center; gap:4px;">
                📄 A4 DYNO-SHEET
            </a>
        </div>

        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:10px;">
            <div>
                <label style="font-size:0.7rem; color:#888; font-weight:bold; display:block;">TEMPERATUR:</label>
                <input type="number" step="0.5" id="tempInput" value="{temp_param}" style="width:100%; background:#0d0d11; border:1px solid #333; color:#fff; padding:6px; border-radius:6px; font-family:monospace; font-weight:bold;">
            </div>
            <div>
                <label style="font-size:0.7rem; color:#888; font-weight:bold; display:block;">LUFTDRUCK:</label>
                <input type="number" step="0.5" id="pressInput" value="{pressure_param}" style="width:100%; background:#0d0d11; border:1px solid #333; color:#fff; padding:6px; border-radius:6px; font-family:monospace; font-weight:bold;">
            </div>
            <div>
                <label style="font-size:0.7rem; color:#888; font-weight:bold; display:block;">NORM:</label>
                <select id="normSelect" style="width:100%; background:#0d0d11; border:1px solid #333; color:#00ffcc; padding:6px; border-radius:6px; font-weight:bold; font-size:0.8rem;">
                    <option value="DIN70020" {('selected' if norm_param == 'DIN70020' else '')}>DIN 70020 (20°C)</option>
                    <option value="SAE_J1349" {('selected' if norm_param == 'SAE_J1349' else '')}>SAE J1349 (25°C)</option>
                    <option value="RAW" {('selected' if norm_param == 'RAW' else '')}>RAW (Keine)</option>
                </select>
            </div>
        </div>

        <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" onclick="fetchLiveWeather({gps_lat}, {gps_lon})" 
                    style="flex:2; background:#1c1c24; border:1px solid #00ffcc; color:#00ffcc; padding:8px; border-radius:8px; font-weight:bold; font-size:0.8rem; cursor:pointer;">
                🛰️ Wetter via Smartphone laden
            </button>
            <button type="button" onclick="applyWeather()" 
                    style="flex:1; background:#00e676; border:none; color:#000; padding:8px; border-radius:8px; font-weight:800; font-size:0.8rem; cursor:pointer;">
                🔄 NEU BERECHNEN
            </button>
        </div>

        <div style="margin-top:10px; padding-top:8px; border-top:1px solid #222; display:flex; justify-content:space-between; align-items:center; font-size:0.8rem;">
            <div>
                <span style="color:#888; font-weight:bold;">🏔️ NEIGUNG:</span>
                <span style="color:#fff; font-weight:bold; font-family:monospace;">{detected_slope:+.1f}%</span>
                <span style="color:#aaa;">({avg_slope_ps:+.1f} PS)</span>
            </div>
            <div>
                <select id="slopeSelect" onchange="changeSlope()" style="background:#0d0d11; border:1px solid #444; color:#00ffcc; padding:4px 8px; border-radius:6px; font-weight:bold; font-size:0.75rem;">
                    <option value="auto" {('selected' if slope_param == 'auto' else '')}>🛰️ Auto-GPS</option>
                    <option value="0.0" {('selected' if slope_param == '0.0' else '')}>0.0% Ebene</option>
                    <option value="0.8" {('selected' if slope_param == '0.8' else '')}>+0.8% Hausstrecke</option>
                    <option value="1.5" {('selected' if slope_param == '1.5' else '')}>+1.5% Bergauf</option>
                    <option value="-0.8" {('selected' if slope_param == '-0.8' else '')}>-0.8% Gegenhang</option>
                </select>
            </div>
        </div>
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
    
    <div style="background:#141418; border:2px solid #27272e; border-radius:16px; padding:16px; margin-bottom:20px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <h3 style="margin:0; font-size:1.1rem; color:#00ffcc; font-weight:800;">🔬 CARBURETOR JETTING ADVISOR</h3>
            <a href="/tuning" style="font-size:0.8rem; color:#ff9800; text-decoration:none; font-weight:700; background:#222; padding:4px 10px; border-radius:6px; border:1px solid #444;">⚙️ Setup</a>
        </div>
        <div style="background:#1f1f26; padding:10px 12px; border-radius:8px; margin-bottom:12px; font-size:0.85rem; color:#fff;">
            <strong>{carb_diag.get('overall_verdict', '') if carb_diag else ''}</strong>
        </div>
        {diag_rows}
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
                            logger.log(round(current_filtered_rpm, 1), current_filtered_afr, p_egt, spd, g.lat if g else 0.0, g.lon if g else 0.0, g.alt if g else 0.0, g.fix if g else False)
                            
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
