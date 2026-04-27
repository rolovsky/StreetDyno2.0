import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request

# ==========================================
# --- KONFIGURATION (OFFSET JOKER) ---
# ==========================================
AFR_OFFSET = 1.5        # Deine Korrektur für die Lambda-Werte
EGT_OFFSET = 0.0        
RPM_MULTIPLIER = 1.0    # Falls die 3 Impulse noch Feinschliff brauchen
# ==========================================

LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"
PLOT_DIR = "/home/rolovsky/streetdyno2.0/plots"
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

# Dashboard HTML (Portrait / Mobile)
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:12px; background:#222; border-radius:10px; margin-bottom:12px; text-align:center; border:1px solid #444; font-weight:bold; font-size:0.9em; }
        .card { background:#1a1a1a; padding:15px; border-radius:15px; border:1px solid #333; text-align:center; margin-bottom:10px; }
        .label { color:#888; font-size:1em; text-transform:uppercase; margin-bottom:2px; }
        .value { font-size:4.5em; font-weight:bold; font-family: monospace; line-height:1em; }
        .btn { display:block; padding:20px; border-radius:12px; text-decoration:none; font-weight:bold; text-align:center; margin-top:10px; font-size:1.2em; background:#00ffcc; color:#111; }
    </style>
</head>
<body>
    <div id="status" class="status-bar">Warte...</div>
    <div class="card"><div class="label">Speed</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
    <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
    <div class="card"><div class="label">AFR</div><div id="afr" class="value" style="color:#ff3366;">0.0</div></div>
    <div class="card"><div class="label">EGT</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    <a href="/logs" class="btn">📂 LOG-ARCHIV</a>
    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(d => {
                document.getElementById('rpm').innerText = d.rpm.toFixed(0);
                document.getElementById('speed').innerText = d.speed.toFixed(1);
                document.getElementById('afr').innerText = d.afr.toFixed(2);
                document.getElementById('egt').innerText = d.egt.toFixed(1);
                let s = document.getElementById('status');
                s.innerText = d.status + (d.fix ? " (GPS FIX)" : " (KEIN FIX)");
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
    rows = "".join([f'<div style="background:#222; margin-bottom:10px; padding:15px; border-radius:10px; border:1px solid #444;">'
                    f'<b>{f}</b><br><div style="margin-top:10px;">'
                    f'<a href="/analyze?file={f}" style="color:#00ffcc; text-decoration:none;">ANALYSE</a> | '
                    f'<a href="/download/{f}" style="color:#ff9800; text-decoration:none;">DOWNLOAD</a></div></div>' for f in files])
    return f"<body style='background:#111; color:#fff; padding:15px;'><h2>Logs</h2>{rows}<br><a href='/' style='color:#00ffcc;'>Zurück</a></body>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

@app.route('/analyze')
def analyze_file():
    fname = request.args.get('file')
    fpath = os.path.join(LOG_DIR, fname)
    try:
        df = pd.read_csv(fpath)
        df['rpm_s'] = df['RPM'].rolling(window=25, center=True).median()
        df['hp_s'] = ((df['rpm_s'] * (df['rpm_s'].diff()/0.1)) / 175000).clip(lower=0).rolling(window=40, center=True).mean()
        plt.style.use('dark_background')
        fig, ax1 = plt.subplots(figsize=(10, 6))
        ax1.plot(df['rpm_s'], df['hp_s'], color='#00ffcc', linewidth=4)
        pname = f"p_{int(time.time())}.png"; plt.savefig(os.path.join(PLOT_DIR, pname)); plt.close()
        return f'<body style="background:#111; color:white; text-align:center; padding:20px;">' \
               f'<h1>{df["hp_s"].max():.1f} PS</h1><img src="/plots/{pname}" style="width:100%;">' \
               f'<br><br><a href="/logs" style="color:#ff9800; font-size:1.2em;">ZURÜCK</a></body>'
    except Exception as e: return str(e)

@app.route('/plots/<path:filename>')
def send_plot(filename): return send_from_directory(PLOT_DIR, filename)

def hardware_loop():
    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
    gps.start()
    m_idx, last_upd, start_cnt, stop_cnt, l_data_t = 0, 0, 0, 0, time.time()
    
    rpm_window = [0] * 7
    afr_window = [0] * 5
    
    ser = None
    while True:
        if ser is None or not ser.is_open:
            try: ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
            except: time.sleep(1); continue

        if HAS_GPIO and GPIO.input(21) == GPIO.LOW:
            m_idx = (m_idx + 1) % 4
            oled.set_mode(["RPM", "SPEED", "AFR", "EGT"][m_idx]); time.sleep(0.3)

        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('$'):
                    l_data_t = time.time()
                    parts = line[1:].split(';')
                    if len(parts) >= 3:
                        r_rpm, r_afr, r_egt = float(parts[0]), float(parts[1]), float(parts[2])
                        
                        # Apply User Correction
                        p_rpm = r_rpm * RPM_MULTIPLIER
                        p_afr = r_afr + AFR_OFFSET
                        p_egt = r_egt + EGT_OFFSET
                        
                        # Filtering
                        rpm_window.pop(0); rpm_window.append(p_rpm)
                        rpm = sorted(rpm_window)[3]
                        
                        afr_window.pop(0); afr_window.append(p_afr)
                        afr = sum(afr_window) / 5.0
                        
                        g = gps.get_data(); spd = g.speed_kmh if g else 0.0
                        telemetry.update({"rpm":rpm, "afr":afr, "egt":p_egt, "speed":spd, "fix":g.fix if g else False})
                        
                        if not logger.is_logging:
                            if rpm > 1300 and spd > 7.0:
                                start_cnt += 1
                                if start_cnt >= 10:
                                    logger.filename = os.path.join(LOG_DIR, f"dyno_log_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv")
                                    logger.start(); telemetry["status"]="🔴 REC"; start_cnt = 0
                        else:
                            logger.log(rpm, afr, p_egt, spd, g.lat, g.lon, g.fix if g else False)
                            if rpm < 1100 and spd < 5.0:
                                stop_cnt += 1
                                if stop_cnt >= 30:
                                    logger.stop(); telemetry["status"]="🟢 IDLE"; stop_cnt = 0
        except: ser = None

        if time.time() - l_data_t > 1.0: telemetry["rpm"] = 0
        if time.time() - last_upd > 0.1:
            oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V4.0", telemetry["fix"], logger.is_logging)
            last_upd = time.time()
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8080)
