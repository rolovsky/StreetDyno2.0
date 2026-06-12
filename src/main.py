import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from flask import Flask, jsonify, render_template_string, send_from_directory, request

# ==========================================
# --- KONFIGURATION v5.1 (STABLE LOGGING + AFR WARN) ---
# ==========================================
AFR_OFFSET = 0.0        # Justiert auf dein Tacho-Standgas (~13.2)
EGT_OFFSET = 0.0        
RPM_MULTIPLIER = 0.82   
RPM_ALPHA = 0.15        
AFR_ALPHA = 0.05        # MASSIVE Dämpfung für AFR (Tacho-Look)
AFR_MAX_VALID = 16.5    # Spike-Blocker für Schiebebetrieb
AUTO_START_RPM = 1350   
MIN_SPEED_KMH = 2.0     
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
time_synced = False

def sync_time_with_gps(gps_data):
    """ Setzt die Systemzeit des Pi basierend auf GPS UTC """
    global time_synced
    if not time_synced and gps_data and gps_data.fix:
        try:
            new_time = gps_data.utc_time.strftime('%Y-%m-%d %H:%M:%S')
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
    <div id="status" class="status-bar">V5.0 READY</div>
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
    try:
        df = pd.read_csv(fpath)
        df['rpm_s'] = df['RPM'].rolling(window=15, center=True).median()
        df['hp_s'] = ((df['rpm_s'] * (df['rpm_s'].diff()/0.1)) / 175000).clip(lower=0).rolling(window=30, center=True).mean()
        plt.style.use('dark_background')
        fig, ax1 = plt.subplots(figsize=(10, 6))
        ax1.plot(df['rpm_s'], df['hp_s'], color='#00ffcc', linewidth=4)
        ax1.set_xlabel('RPM'); ax1.set_ylabel('PS')
        pname = f"p_{int(time.time())}.png"; plt.savefig(os.path.join(PLOT_DIR, pname), dpi=100); plt.close()
        return f'<body style="background:#111; color:white; text-align:center; padding:20px;"><h1>{df["hp_s"].max():.1f} PS</h1><img src="/plots/{pname}" style="width:100%;"><br><a href="/logs" style="color:#ff9800;">ZURÜCK</a></body>'
    except Exception as e: return str(e)

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
                                logger.filename = os.path.join(LOG_DIR, f"dyno_log_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv")
                                logger.start(); telemetry["status"]="🔴 REC"
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
