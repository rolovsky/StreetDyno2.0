import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request

# --- HARDWARE ---
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger
try: from display_oled import OLEDDisplay
except ImportError: from hw.display_oled import OLEDDisplay

# --- CONFIG ---
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"
PLOT_DIR = "/home/rolovsky/streetdyno2.0/plots"
os.makedirs(PLOT_DIR, exist_ok=True)

BUTTON_PIN = 21
START_COORD, FINISH_COORD = (46.1948699, 6.1280389), (46.1900060, 6.1280047)
AUTO_START_RPM, MIN_SPEED_KMH = 1300, 7.0
START_DELAY, STOP_DELAY = 10, 30

telemetry = {"rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, "fix": False, "status": "🟢 IDLE"}
app = Flask(__name__)

# UI: Mobile Portrait Optimized
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:15px; background:#222; border-radius:8px; margin-bottom:15px; font-weight:bold; text-align:center; border:1px solid #444; }
        .card { background:#1a1a1a; padding:15px; border-radius:12px; border:1px solid #333; text-align:center; margin-bottom:10px; }
        .value { font-size:4em; font-weight:bold; font-family: monospace; line-height:1em; }
        .label { color:#888; font-size:1em; text-transform:uppercase; margin-bottom:5px; }
        .btn { display:block; padding:20px; border-radius:10px; text-decoration:none; font-weight:bold; text-align:center; margin-top:10px; font-size:1.2em; }
    </style>
</head>
<body>
    <div id="status" class="status-bar">Warte...</div>
    <div class="card"><div class="label">Speed km/h</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
    <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
    <div class="card"><div class="label">AFR</div><div id="afr" class="value" style="color:#ff3366;">0.0</div></div>
    <div class="card"><div class="label">EGT</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    <a href="/logs" class="btn" style="background:#00ffcc; color:#111;">📂 PROTOKOLLE</a>
    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(data => {
                document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                document.getElementById('speed').innerText = data.speed.toFixed(1);
                document.getElementById('afr').innerText = data.afr.toFixed(2);
                document.getElementById('egt').innerText = data.egt.toFixed(1);
                let s = document.getElementById('status');
                s.innerText = data.status + (data.fix ? " (FIX)" : " (NO FIX)");
                s.style.color = data.status.includes('REC') ? '#ff3366' : '#00ffcc';
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
    rows = "".join([f'<div style="background:#222; margin-bottom:10px; padding:15px; border-radius:8px; border:1px solid #444;">'
                    f'<b>{f}</b><div style="display:flex; gap:10px; margin-top:10px;">'
                    f'<a href="/analyze?file={f}" style="flex:1; padding:12px; background:#00ffcc; color:#111; text-decoration:none; border-radius:5px; text-align:center; font-weight:bold;">ANALYSE</a>'
                    f'<a href="/download/{f}" style="flex:1; padding:12px; background:#ff9800; color:#111; text-decoration:none; border-radius:5px; text-align:center; font-weight:bold;">DOWN</a>'
                    f'</div></div>' for f in files])
    return f"<body style='background:#111; color:#fff; font-family:sans-serif; padding:15px;'><h2>Logs</h2>{rows}<br><a href='/' style='color:#00ffcc;'>ZURÜCK</a></body>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

@app.route('/analyze')
def analyze_file():
    fname = request.args.get('file')
    fpath = os.path.join(LOG_DIR, fname) if fname else max(glob.glob(os.path.join(LOG_DIR, '*.csv')), key=os.path.getctime)
    try:
        df = pd.read_csv(fpath)
        df['rpm_s'] = df['RPM'].rolling(window=15, center=True).median()
        df['hp_s'] = ((df['rpm_s'] * (df['rpm_s'].diff()/0.1)) / 175000).clip(lower=0).rolling(window=20, center=True).mean()
        plt.style.use('dark_background')
        fig, ax1 = plt.subplots(figsize=(10, 6))
        ax1.plot(df['rpm_s'], df['hp_s'], color='#00ffcc', linewidth=4)
        pname = f"p_{int(time.time())}.png"; plt.savefig(os.path.join(PLOT_DIR, pname)); plt.close()
        return f'<body style="background:#111; color:white; text-align:center; padding:20px;"><h1>{df["hp_s"].max():.1f} PS</h1><img src="/plots/{pname}" style="width:100%;"><br><br><a href="/logs" style="color:#ff9800;">ZURÜCK</a></body>'
    except Exception as e: return str(e)

@app.route('/plots/<path:filename>')
def send_plot(filename): return send_from_directory(PLOT_DIR, filename)

def hardware_loop():
    if HAS_GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
    gps.start()
    m_idx, last_upd, start_cnt, stop_cnt, l_data_t = 0, 0, 0, 0, time.time()
    rpm_window = [0, 0, 0]
    
    ser = None
    while True:
        # Reconnect Logik
        if ser is None or not ser.is_open:
            try:
                ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
            except:
                time.sleep(1)
                continue

        if HAS_GPIO and GPIO.input(BUTTON_PIN) == GPIO.LOW:
            m_idx = (m_idx + 1) % 4
            oled.set_mode(["RPM", "SPEED", "AFR", "EGT"][m_idx]); time.sleep(0.3)

        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('$'):
                    l_data_t = time.time()
                    parts = line[1:].split(';')
                    if len(parts) >= 3:
                        raw_rpm, afr, egt = float(parts[0]), float(parts[1]), float(parts[2])
                        rpm_window.pop(0); rpm_window.append(raw_rpm)
                        rpm = sorted(rpm_window)[1]
                        
                        g = gps.get_data(); spd, fix = (g.speed_kmh, g.fix) if g else (0.0, False)
                        telemetry.update({"rpm":rpm, "afr":afr, "egt":egt, "speed":spd, "fix":fix})
                        
                        if not logger.is_logging:
                            if rpm > AUTO_START_RPM and spd > MIN_SPEED_KMH:
                                start_cnt += 1
                                if start_cnt >= START_DELAY:
                                    logger.filename = os.path.join(LOG_DIR, f"dyno_log_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv")
                                    logger.start(); telemetry["status"]="🔴 REC"; start_cnt = 0
                            else: start_cnt = 0
                        else:
                            logger.log(rpm, afr, egt, spd, g.lat, g.lon, fix)
                            if rpm < 1100 and spd < 5.0:
                                stop_cnt += 1
                                if stop_cnt >= STOP_DELAY:
                                    logger.stop(); telemetry["status"]="🟢 IDLE"; stop_cnt = 0
                            else: stop_cnt = 0
        except:
            ser = None # Trigger Reconnect

        if time.time() - l_data_t > 1.0: telemetry["rpm"] = 0
        if time.time() - last_upd > 0.1:
            oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V3.6", telemetry["fix"], logger.is_logging)
            last_upd = time.time()
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8080)
