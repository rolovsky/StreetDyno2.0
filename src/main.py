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
try: from hw.display_oled import OLEDDisplay
except: from display_oled import OLEDDisplay

# --- CONFIG ---
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"
os.makedirs(LOG_DIR, exist_ok=True)
BUTTON_PIN, GPS_WAKE_PIN = 21, 4
telemetry = {"rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, "fix": False, "status": "⚪ IDLE"}
app = Flask(__name__)

# --- DASHBOARD HTML (Multimeter Mode) ---
DASH_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background:#111; color:#fff; font-family: sans-serif; margin:0; padding: 10px; }
        .status-bar { padding:15px; background:#222; border-radius:8px; margin-bottom:15px; text-align:center; border:1px solid #444; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .card { background:#1a1a1a; padding:15px; border-radius:12px; border:1px solid #333; text-align:center; }
        .label { color:#888; font-size:0.9em; text-transform:uppercase; font-weight:bold; }
        .value { font-size:3.5em; font-weight:bold; font-family: monospace; }
    </style>
</head>
<body>
    <div id="status" class="status-bar">Warte auf Daten...</div>
    <div class="grid">
        <div class="card"><div class="label">Speed (km/h)</div><div id="speed" class="value" style="color:#00ffcc;">0.0</div></div>
        <div class="card"><div class="label">RPM</div><div id="rpm" class="value" style="color:#ff9800;">0</div></div>
        <div class="card"><div class="label">AFR / VOLT (A0)</div><div id="afr" class="value" style="color:#ff3366;">0.000</div></div>
        <div class="card"><div class="label">EGT (Temp)</div><div id="egt" class="value" style="color:#ffcc00;">0.0</div></div>
    </div>
    <div style="margin-top:20px; text-align:center;">
        <a href="/logs" style="padding:15px; background:#444; color:white; text-decoration:none; border-radius:8px;">📊 LOGS</a>
    </div>
    <script>
        setInterval(() => {
            fetch('/api/data').then(r => r.json()).then(data => {
                document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                document.getElementById('speed').innerText = data.speed.toFixed(1);
                // Volt mit 3 Stellen anzeigen für Kalibrierung
                document.getElementById('afr').innerText = data.afr.toFixed(3);
                document.getElementById('egt').innerText = data.egt.toFixed(1);
                document.getElementById('status').innerText = data.status + (data.fix ? " (GPS FIX)" : " (NO FIX)");
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
    rows = "".join([f'<div style="background:#222; padding:10px; margin-bottom:5px; border-radius:5px;">{f}</div>' for f in files])
    return f"<body style='background:#111; color:white;'><h2>Logs</h2>{rows}<br><a href='/' style='color:cyan;'>Zurück</a></body>"

# --- HARDWARE LOOP ---
def hardware_loop():
    if HAS_GPIO:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except: pass
        os.system(f"sudo pinctrl set {BUTTON_PIN} ip pu")

    gps, logger, oled = GPS_L76K(), CSVLogger(log_dir=LOG_DIR), OLEDDisplay()
    gps.start()
    l_data_t = time.time()
    
    while True:
        try:
            if not os.path.exists('/dev/ttyUSB0'):
                time.sleep(1); continue
            
            with serial.Serial('/dev/ttyUSB0', 115200, timeout=1) as ser:
                ser.flushInput()
                while True:
                    if ser.in_waiting > 0:
                        raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if raw_line.count('$') > 1: raw_line = '$' + raw_line.split('$')[-1]
                        
                        if raw_line.startswith('$'):
                            try:
                                parts = raw_line[1:].split(';')
                                if len(parts) >= 3:
                                    rpm_str = parts[0].replace('inf', '0')
                                    telemetry.update({
                                        "rpm": float(rpm_str),
                                        "afr": float(parts[1]), # Hier kommt jetzt das Volt-Signal an
                                        "egt": float(parts[2])
                                    })
                                    l_data_t = time.time()
                                    g = gps.get_data()
                                    if g: telemetry.update({"speed": g.speed_kmh, "fix": g.fix})
                            except: continue
                    
                    if time.time() - l_data_t > 1.5: telemetry["rpm"] = 0
                    oled.show_status(telemetry["rpm"], telemetry["speed"], telemetry["afr"], telemetry["egt"], "V3.8-CAL", telemetry["fix"], logger.is_logging)
                    time.sleep(0.05)
        except: time.sleep(2)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8085)
