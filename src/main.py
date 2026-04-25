import time, serial, sys, os, glob, threading
from datetime import datetime
import pandas as pd
import numpy as np
from flask import Flask, jsonify, render_template_string

# --- HARDWARE-ERKENNUNG ---
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger
try: 
    from hw.display_oled import OLEDDisplay
except: 
    from display_oled import OLEDDisplay

# --- KONFIGURATION ---
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"
os.makedirs(LOG_DIR, exist_ok=True)

BUTTON_PIN = 21
telemetry = {
    "rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, 
    "fix": False, "status": "⚪ INITIALIZING"
}

app = Flask(__name__)

# --- DASHBOARD TEMPLATE (VOLLVERSION) ---
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:15px; background:#222; border-radius:8px; margin-bottom:15px; font-weight:bold; text-align:center; border:1px solid #444; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .card { background:#1a1a1a; padding:15px; border-radius:12px; border:1px solid #333; text-align:center; }
        .label { color:#888; font-size:0.8em; text-transform:uppercase; }
        .value { font-size:3em; font-weight:bold; font-family: 'Courier New', monospace; margin: 5px 0; }
        .btn-group { margin-top:20px; display:flex; flex-direction:column; gap:10px; }
        .btn { padding:15px; border-radius:10px; text-decoration:none; font-weight:bold; text-align:center; background:#00ffcc; color:#111; }
        .alarm { background: #661111 !important; border: 2px solid #ff3333; }
    </style>
</head>
<body>
    <div id="status" class="status-bar">Warte auf Synchronisation...</div>
    <div class="grid">
        <div class="card"><div class="label">Speed (km/h)</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
        <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
        <div class="card"><div class="label">AFR</div><div id="afr" class="value" style="color:#ff3366;">0.0</div></div>
        <div id="egt_card" class="card"><div class="label">EGT (°C)</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    </div>
    <div class="btn-group">
        <a href="/logs" class="btn">📊 LOGS ANSEHEN</a>
    </div>
    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(data => {
                document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                document.getElementById('speed').innerText = data.speed.toFixed(1);
                document.getElementById('afr').innerText = data.afr.toFixed(2);
                document.getElementById('egt').innerText = data.egt.toFixed(1);
                document.getElementById('status').innerText = data.status + (data.fix ? " (GPS FIX)" : " (SEARCHING GPS)");
                let egtC = document.getElementById('egt_card');
                if(data.egt > 650) egtC.classList.add('alarm'); else egtC.classList.remove('alarm');
            });
        }, 200);
    </script>
</body>
</html>
"""

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template_string(DASH_HTML)

@app.route('/api/data')
def api_data():
    return jsonify(telemetry)

@app.route('/logs')
def list_logs():
    files = sorted([os.path.basename(x) for x in glob.glob(os.path.join(LOG_DIR, '*.csv'))], reverse=True)
    rows = "".join([f'<div style="background:#222; margin:10px; padding:15px; border-radius:8px;"><b>{f}</b><br><br><a href="#" style="color:#00ffcc;">DOWNLOAD (WIP)</a></div>' for f in files])
    return f"<body style='background:#111; color:#fff; padding:20px;'><h2>Fahrten-Archiv</h2>{rows}<br><a href='/' style='color:white;'>Zurück</a></body>"

# --- HARDWARE STEUERUNG (THREAD) ---
def hardware_loop():
    if HAS_GPIO:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except:
            print("⚠️ GPIO 21 busy - nutze System-Fix.")
        os.system(f"sudo pinctrl set {BUTTON_PIN} ip pu")
    
    oled, gps, logger = OLEDDisplay(), GPS_L76K(), CSVLogger(log_dir=LOG_DIR)
    gps.start()
    
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    m_idx, l_data_t = 0, time.time()
    
    while True:
        try:
            if not os.path.exists('/dev/ttyUSB0'):
                telemetry["status"] = "🔌 ARDUINO DISCONNECTED"
                time.sleep(2)
                continue
            
            with serial.Serial('/dev/ttyUSB0', 115200, timeout=1) as ser:
                ser.flushInput()
                telemetry["status"] = "⚪ IDLE"
                
                while True:
                    # Button-Abfrage für OLED
                    if HAS_GPIO and GPIO.input(BUTTON_PIN) == GPIO.LOW:
                        m_idx = (m_idx + 1) % len(modes)
                        oled.set_mode(modes[m_idx])
                        time.sleep(0.3)

                    if ser.in_waiting > 0:
                        raw = ser.readline().decode('utf-8', errors='ignore').strip()
                        
                        # Fix für verklebte Zeilen: Nimm das letzte Paket
                        if raw.count('$') > 1:
                            raw = '$' + raw.split('$')[-1]
                        
                        if raw.startswith('$'):
                            try:
                                parts = raw[1:].split(';')
                                if len(parts) >= 3:
                                    # 'inf' Filter
                                    rpm_raw = parts[0].replace('inf', '0')
                                    
                                    telemetry.update({
                                        "rpm": float(rpm_raw),
                                        "afr": float(parts[1]),
                                        "egt": float(parts[2])
                                    })
                                    l_data_t = time.time()
                                    
                                    # GPS Daten abrufen
                                    g_data = gps.get_data()
                                    if g_data:
                                        telemetry.update({"speed": g_data.speed_kmh, "fix": g_data.fix})
                            except ValueError:
                                continue

                    # Standby-Check
                    if time.time() - l_data_t > 1.5:
                        telemetry["rpm"] = 0
                    
                    # OLED Update
                    oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V3.8", telemetry["fix"], logger.is_logging)
                    time.sleep(0.05)

        except Exception as e:
            print(f"⚠️ Hardware Error: {e}")
            time.sleep(2)

if __name__ == '__main__':
    # Starte Hardware-Logik im Hintergrund
    t = threading.Thread(target=hardware_loop, daemon=True)
    t.start()
    
    # Flask Dashboard auf Port 8085
    app.run(host='0.0.0.0', port=8085, debug=False, use_reloader=False)
