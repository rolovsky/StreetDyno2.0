import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request

# --- HARDWARE CHECK ---
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

BUTTON_PIN, GPS_WAKE_PIN = 21, 4
START_COORD, FINISH_COORD = (46.1948699, 6.1280389), (46.1900060, 6.1280047)
AUTO_START_RPM, MIN_SPEED_KMH, AUTO_STOP_RPM = 1300, 5.0, 1100

telemetry = {"rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, "fix": False, "status": "🟢 IDLE"}
app = Flask(__name__)

# --- MOBILE OPTIMIZED TEMPLATE ---
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:15px; background:#222; border-radius:8px; margin-bottom:15px; font-weight:bold; text-align:center; border:1px solid #444; font-size:1.2em; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        @media (max-width: 500px) { .grid { grid-template-columns: 1fr; } }
        .card { background:#1a1a1a; padding:15px; border-radius:12px; border:1px solid #333; text-align:center; transition: background 0.3s; }
        .label { color:#888; font-size:0.9em; margin-bottom:5px; text-transform:uppercase; font-weight:bold; }
        .value { font-size:3.5em; font-weight:bold; font-family: 'Courier New', monospace; }
        .btn-group { margin-top:20px; display:flex; flex-direction:column; gap:10px; }
        .btn { padding:20px; border-radius:10px; text-decoration:none; font-weight:bold; font-size:1.2em; text-align:center; }
        .btn-logs { background:#00ffcc; color:#111; }
        .btn-down { background:#ff9800; color:#111; }
        .alarm { background: #661111 !important; border-color: #ff3333 !important; animation: blink 1s infinite; }
        @keyframes blink { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div id="status" class="status-bar">Warte...</div>
    
    <div class="grid">
        <div class="card"><div class="label">Speed (km/h)</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
        <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
        <div class="card"><div class="label">AFR (Gemisch)</div><div id="afr" class="value" style="color:#ff3366;">0.0</div></div>
        <div id="egt_card" class="card"><div class="label">EGT (Temp)</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    </div>

    <div class="btn-group">
        <a href="/logs" class="btn btn-logs">📂 LOGS & ANALYSE</a>
        <a href="/download_latest" class="btn btn-down">📥 LETZTER LOG DOWNLOAD</a>
    </div>

    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(data => {
                document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                document.getElementById('speed').innerText = data.speed.toFixed(1);
                document.getElementById('afr').innerText = data.afr.toFixed(2);
                document.getElementById('egt').innerText = data.egt.toFixed(1);
                
                let s = document.getElementById('status');
                s.innerText = data.status + (data.fix ? " (GPS FIX)" : " (KEIN FIX)");
                s.style.color = data.status.includes('REC') ? '#ff3366' : '#00ffcc';

                // EGT Alarm
                let egtC = document.getElementById('egt_card');
                if(data.egt > 630) { egtC.classList.add('alarm'); }
                else { egtC.classList.remove('alarm'); }
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
    rows = "".join([f'<div style="background:#222; margin-bottom:15px; padding:20px; border-radius:10px; border:1px solid #444;">'
                    f'<div style="font-weight:bold; margin-bottom:10px;">{f}</div>'
                    f'<div style="display:flex; gap:10px;">'
                    f'<a href="/analyze?file={f}" style="flex:1; padding:12px; background:#00ffcc; color:#111; text-decoration:none; border-radius:5px; text-align:center; font-weight:bold;">ANALYSE</a>'
                    f'<a href="/download/{f}" style="flex:1; padding:12px; background:#ff9800; color:#111; text-decoration:none; border-radius:5px; text-align:center; font-weight:bold;">DOWNLOAD</a>'
                    f'</div></div>' for f in files])
    return f"<body style='background:#111; color:#fff; font-family:sans-serif; padding:15px;'><h2>Fahrten-Logs</h2>{rows}<br><a href='/' style='display:block; padding:15px; background:#444; color:white; text-decoration:none; border-radius:10px; text-align:center;'> << ZURÜCK </a></body>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

@app.route('/download_latest')
def download_latest():
    list_of_files = glob.glob(os.path.join(LOG_DIR, '*.csv'))
    if not list_of_files: return "Keine Logs"
    latest = max(list_of_files, key=os.path.getctime)
    return send_from_directory(LOG_DIR, os.path.basename(latest), as_attachment=True)

@app.route('/analyze')
def analyze_file():
    fname = request.args.get('file')
    fpath = os.path.join(LOG_DIR, fname) if fname else max(glob.glob(os.path.join(LOG_DIR, '*.csv')), key=os.path.getctime)
    try:
        df = pd.read_csv(fpath)
        df['d_start'] = np.sqrt((df['Lat']-START_COORD[0])**2 + (df['Lon']-START_COORD[1])**2)
        df['d_finish'] = np.sqrt((df['Lat']-FINISH_COORD[0])**2 + (df['Lon']-FINISH_COORD[1])**2)
        s_idx, f_idx = df['d_start'].idxmin(), df['d_finish'].idxmin()
        idx1, idx2 = (s_idx, f_idx) if s_idx < f_idx else (f_idx, s_idx)
        
        if 10 < abs(idx1 - idx2) < 500:
            df = df.iloc[idx1:idx2].copy()
            title_txt = f"SEKTOR: {(len(df)*0.1):.2f}s"
        else: title_txt = "FULL LOG"

        df['rpm_s'] = df['RPM'].rolling(window=15, center=True).mean()
        df['hp_s'] = ((df['rpm_s'] * (df['rpm_s'].diff()/0.1)) / 175000).clip(lower=0).rolling(window=20, center=True).mean()
        
        plt.style.use('dark_background')
        fig, ax1 = plt.subplots(figsize=(10, 6))
        ax1.plot(df['rpm_s'], df['hp_s'], color='#00ffcc', linewidth=4)
        ax1.set_xlabel('RPM'); ax1.set_ylabel('PS', color='#00ffcc')
        ax2 = ax1.twinx(); ax2.plot(df.index, df['EGT'], color='#ffcc00', alpha=0.3)
        ax2.set_ylabel('Temp', color='#ffcc00')
        
        pname = f"p_{int(time.time())}.png"
        for o in glob.glob(os.path.join(PLOT_DIR, "p_*.png")): os.remove(o)
        plt.savefig(os.path.join(PLOT_DIR, pname), dpi=100); plt.close()
        
        return f'<body style="background:#111; color:white; text-align:center; padding:15px; font-family:sans-serif;">' \
               f'<h1 style="color:#00ffcc; font-size:2.5em;">{df["hp_s"].max():.1f} PS</h1>' \
               f'<img src="/plots/{pname}" style="width:100%; border-radius:10px; border:1px solid #444;">' \
               f'<br><br><a href="/logs" style="display:block; padding:20px; background:#ff9800; color:#111; text-decoration:none; border-radius:10px; font-weight:bold;">ZURÜCK</a></body>'
    except Exception as e: return str(e)

@app.route('/plots/<path:filename>')
def send_plot(filename): return send_from_directory(PLOT_DIR, filename)

def hardware_loop():
    if HAS_GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dl")
    
    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
    gps.start()
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    m_idx, last_upd, stop_cnt, l_data_t = 0, 0, 0, time.time()
    rpm_history = [0, 0, 0]
    
    try: ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    except: return
    
    while True:
        if HAS_GPIO and GPIO.input(BUTTON_PIN) == GPIO.LOW:
            m_idx = (m_idx + 1) % 4
            oled.set_mode(modes[m_idx]); time.sleep(0.3)

        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith('$'):
                l_data_t = time.time()
                parts = line[1:].split(';')
                if len(parts) >= 3:
                    try:
                        raw_rpm, afr, egt = float(parts[0]), float(parts[1]), float(parts[2])
                        rpm_history.pop(0); rpm_history.append(raw_rpm)
                        rpm = sorted(rpm_history)[1]
                        g = gps.get_data(); spd, fix = (g.speed_kmh, g.fix) if g else (0.0, False)
                        telemetry.update({"rpm":rpm, "afr":afr, "egt":egt, "speed":spd, "fix":fix})
                        if not logger.is_logging:
                            if rpm > AUTO_START_RPM and spd > MIN_SPEED_KMH:
                                logger.filename = os.path.join(LOG_DIR, f"dyno_log_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv")
                                logger.start(); telemetry["status"]="🔴 REC"; stop_cnt=0
                        else:
                            logger.log(rpm, afr, egt, spd, g.lat, g.lon, fix)
                            if rpm < AUTO_STOP_RPM and spd < 7.0: stop_cnt += 1
                            else: stop_cnt = 0
                            if stop_cnt > 25: logger.stop(); telemetry["status"]="🟢 IDLE"
                    except: pass
        if time.time() - l_data_t > 0.8: telemetry["rpm"] = 0
        if time.time() - last_upd > 0.1:
            oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V3.2", telemetry["fix"], logger.is_logging)
            last_upd = time.time()
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8080)
