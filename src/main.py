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

# --- DASHBOARD UI (Jetzt mit Mager-Warnung) ---
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:12px; background:#222; border-radius:10px; margin-bottom:12px; text-align:center; border:1px solid #444; font-weight:bold; }
        .card { background:#1a1a1a; padding:15px; border-radius:15px; border:1px solid #333; text-align:center; margin-bottom:10px; }
        .label { color:#888; font-size:1em; text-transform:uppercase; margin-bottom:2px; }
        .value { font-size:4.5em; font-weight:bold; font-family: monospace; line-height:1em; transition: color 0.2s; }
        .btn { display:block; padding:20px; border-radius:12px; text-decoration:none; font-weight:bold; text-align:center; margin-top:10px; font-size:1.2em; background:#00ffcc; color:#111; }
        @keyframes blink { 50% { opacity: 0.3; } }
        .danger { color: #ff0000 !important; animation: blink 0.4s infinite; }
    </style>
</head>
<body>
    <div id="status" class="status-bar">V5.1 READY</div>
    <div class="card"><div class="label">Speed km/h</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
    <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
    <div class="card"><div class="label">AFR (Smooth)</div><div id="afr" class="value" style="color:#ff3366;">0.0</div></div>
    <div class="card"><div class="label">EGT °C</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    <a href="/logs" class="btn">📂 LOG-ARCHIV</a>
    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(d => {
                document.getElementById('rpm').innerText = d.rpm.toFixed(0);
                document.getElementById('speed').innerText = d.speed.toFixed(1);
                
                let afrEl = document.getElementById('afr');
                afrEl.innerText = d.afr.toFixed(2);
                // MAGER-WARNUNG: Blinkt rot wenn AFR > 14.5 unter Last
                if (d.afr > 14.5 && d.speed > 10) {
                    afrEl.classList.add('danger');
                } else {
                    afrEl.classList.remove('danger');
                }

                document.getElementById('egt').innerText = d.egt.toFixed(1);
                
                let s = document.getElementById('status');
                s.innerText = d.status + (d.fix ? " (FIX)" : " (NO FIX)");
                s.style.color = d.status.includes('REC') ? '#ff3366' : '#00ffcc';
            });
        }, 200);
    </script>
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(DASH_HTML)

@app.route('/api/data')
def api_data(): return jsonify(telemetry)

@app.route('/logs')
def list_logs():
    files = sorted([os.path.basename(x) for x in glob.glob(os.path.join(LOG_DIR, '*.csv'))], reverse=True)
    rows = "".join([f'<div style="background:#222; margin-bottom:12px; padding:15px; border-radius:10px; border:1px solid #444;">'
                    f'<b>{f}</b><br><div style="margin-top:10px; display:flex; gap:10px;">'
                    f'<a href="/analyze?file={f}" style="flex:1; background:#00ffcc; color:#111; text-align:center; padding:10px; text-decoration:none; border-radius:5px; font-weight:bold;">ANALYSE</a>'
                    f'<a href="/download/{f}" style="flex:1; background:#ff9800; color:#111; text-align:center; padding:10px; text-decoration:none; border-radius:5px; font-weight:bold;">DOWN</a>'
                    f'</div></div>' for f in files])
    return f"<body style='background:#111; color:#fff; font-family:sans-serif; padding:15px;'><h2>Logs</h2>{rows}<br><a href='/' style='color:#00ffcc;'>Zurück</a></body>"

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
        trimmed_df, detected = detect_dyno_pull(df, min_rpm=3000.0, min_duration_sec=1.0, drop_threshold=500.0)
        title_suffix = " (Automatisch getrimmt)" if detected else " (Gesamtes Log)"
        
        # Plot generieren
        pname = f"p_{os.path.splitext(fname)[0]}.png"
        plot_path = os.path.join(PLOT_DIR, pname)
        plot_telemetry(trimmed_df, title_suffix, plot_path)
        
        # Leistungswerte berechnen
        max_ps = trimmed_df['PS'].max()
        max_nm = trimmed_df['Nm'].max()
        max_egt = trimmed_df['EGT_cleaned'].max()
        
        # RPM bei Peak Werten finden
        peak_ps_idx = trimmed_df['PS'].idxmax()
        peak_ps_rpm = trimmed_df.loc[peak_ps_idx, 'RPM_smoothed'] if not pd.isna(peak_ps_idx) else 0.0
        
        peak_nm_idx = trimmed_df['Nm'].idxmax()
        peak_nm_rpm = trimmed_df.loc[peak_nm_idx, 'RPM_smoothed'] if not pd.isna(peak_nm_idx) else 0.0
        
        # Mittlerer AFR während des Pulls
        avg_afr = trimmed_df['AFR'].mean()
        
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
        trimmed_df, detected = detect_dyno_pull(df, min_rpm=3000.0, min_duration_sec=1.0, drop_threshold=500.0)
        
        # Nur relevante Spalten für den Export auswählen
        cols = ['Time', 'RPM', 'RPM_smoothed', 'AFR', 'EGT', 'EGT_cleaned', 'Speed_kmh', 'PS', 'Nm']
        for col in cols:
            if col not in trimmed_df.columns:
                trimmed_df[col] = None
        
        # In Liste von Dictionaries konvertieren
        data = trimmed_df[cols].replace({np.nan: None}).to_dict(orient='records')
        return jsonify({
            "filename": fname,
            "detected": detected,
            "data": data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/plots/<path:filename>')
def send_plot(filename): return send_from_directory(PLOT_DIR, filename)

def hardware_loop():
    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
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
                        
                        # LOGGING (Trigger und Log auf GEGLÄTTETE Daten!)
                        if not logger.is_logging:
                            if current_filtered_rpm > AUTO_START_RPM and spd > MIN_SPEED_KMH:
                                log_time = g.timestamp if (g and g.fix and g.timestamp) else datetime.now()
                                log_filename = os.path.join(LOG_DIR, f"dyno_log_{log_time.strftime('%Y%m%d-%H%M%S')}.csv")
                                logger.start(log_filename); telemetry["status"]="🔴 REC"
                        else:
                            # HIER IST DER FIX: Loggt 'current_filtered_rpm' statt 'target_rpm'
                            logger.log(round(current_filtered_rpm, 1), current_filtered_afr, p_egt, spd, g.lat, g.lon, g.fix if g else False)
                            
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
