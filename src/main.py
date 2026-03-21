import time
import serial
import sys
import os
import glob
import threading
from flask import Flask, jsonify, render_template_string, send_from_directory

# Hardware-Imports
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger

# OLED-Suche
try:
    from display_oled import OLEDDisplay
except ImportError:
    try:
        from hw.display_oled import OLEDDisplay
    except ImportError:
        print("❌ [FEHLER] display_oled.py wurde nicht gefunden!")
        sys.exit(1)

# ==========================================
# --- KONFIGURATION STREETDYNO 2.0 ---
# ==========================================
ARDUINO_PORT = '/dev/ttyUSB0'  
ARDUINO_BAUD = 500000          

# Waveshare OLED HAT Pinout
BUTTON_PIN = 21   
GPS_WAKE_PIN = 4  

# Dyno-Parameter
AUTO_START_RPM = 2500
AUTO_STOP_RPM = 2000
MIN_SPEED_KMH = 30.0
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"

telemetry = {
    "rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, 
    "fix": False, "status": "🟢 IDLE"
}

# ==========================================
# --- FLASK WEBSERVER SETUP ---
# ==========================================
app = Flask(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StreetDyno 2.0 - PX125 Lusso</title>
    <style>
        body { background-color: #111; color: #fff; font-family: 'Courier New', monospace; text-align: center; padding: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; max-width: 600px; margin: 0 auto; }
        .box { background: #222; padding: 20px; border-radius: 10px; border: 1px solid #333; }
        .value { font-size: 3em; font-weight: bold; margin: 10px 0; }
        #speed { color: #00ffcc; } #rpm { color: #ff9800; } #afr { color: #ff3366; } #egt { color: #ffcc00; }
        #status { font-size: 1.5em; padding: 15px; margin-bottom: 20px; border-radius: 5px; background: #333; }
        .btn { display: inline-block; margin-top: 20px; padding: 15px 30px; background: #ff9800; color: #111; text-decoration: none; border-radius: 5px; font-weight: bold; }
    </style>
</head>
<body>
    <div id="status">Initialisiere...</div>
    <div class="grid">
        <div class="box"><div>KM/H</div><div class="value" id="speed">0.0</div></div>
        <div class="box"><div>RPM</div><div class="value" id="rpm">0</div></div>
        <div class="box"><div>AFR</div><div class="value" id="afr">0.0</div></div>
        <div class="box"><div>EGT</div><div class="value" id="egt">0.0</div></div>
    </div>
    <a href="/logs" class="btn">📂 LOGS</a>
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
def index(): return render_template_string(HTML_DASHBOARD)

@app.route('/api/data')
def api_data(): return jsonify(telemetry)

@app.route('/logs')
def list_logs():
    files = sorted([os.path.basename(x) for x in glob.glob(os.path.join(LOG_DIR, '*.csv'))], reverse=True)
    links = "".join([f'<li><a href="/download/{f}" style="color:#ff9800;">{f}</a></li>' for f in files])
    return f"<body style='background:#111;color:#fff;padding:40px;'><h2>Logs:</h2><ul>{links}</ul><a href='/'>Zurück</a></body>"

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

# ==========================================
# --- HARDWARE LOOP ---
# ==========================================
def hardware_loop():
    print("🚀 [SYSTEM] Hardware-Thread gestartet...")
    
    # 1. GPS DEFINITIV AUFWECKEN (Deine funktionierende Sequenz)
    print(f"📡 [GPS] Aktiviere Modul (GPIO {GPS_WAKE_PIN} LOW)...")
    os.system(f"pinctrl set {GPS_WAKE_PIN} op dl") 
    time.sleep(0.5)

    # 2. Hardware Initialisieren
    # WICHTIG: Falls GPS_L76K intern einen Port öffnet, 
    # muss dieser jetzt auf 115200 Baud laufen!
    gps = GPS_L76K() 
    gps.start()
    
    logger = CSVLogger(log_dir=LOG_DIR)
    oled = OLEDDisplay()
    
    display_modes = ["RPM", "SPEED", "AFR", "EGT"]
    mode_idx = 0
    oled.set_mode(display_modes[mode_idx])
    last_oled_update = 0

    # Taster
    def btn_cb(channel):
        nonlocal mode_idx
        mode_idx = (mode_idx + 1) % len(display_modes)
        oled.set_mode(display_modes[mode_idx])

    if HAS_GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=btn_cb, bouncetime=400)

    try:
        ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
        ser.flush()
    except Exception as e:
        print(f"❌ [SERIAL] Arduino fehlt: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                parts = line.split(';')
                if len(parts) == 3:
                    rpm_v, afr_v, egt_v = float(parts[0]), float(parts[1]), float(parts[2])
                    
                    # GPS Abfrage
                    curr = gps._data
                    sp, fix = (curr.speed_kmh, curr.fix) if curr else (0.0, False)
                    lat, lon = (curr.lat, curr.lon) if curr else (0.0, 0.0)

                    telemetry.update({"rpm": rpm_v, "afr": afr_v, "egt": egt_v, "speed": sp, "fix": fix})

                    if not logger.is_logging:
                        if rpm_v > AUTO_START_RPM and sp > MIN_SPEED_KMH:
                            logger.start()
                            telemetry["status"] = "🔴 REC"
                    else:
                        logger.log(rpm_v, afr_v, egt_v, sp, lat, lon, fix)
                        if rpm_v < AUTO_STOP_RPM:
                            logger.stop()
                            telemetry["status"] = "🟢 IDLE"

                    if time.time() - last_oled_update > 0.1:
                        oled.show_status(rpm_v, sp, afr_v, egt_v, "PX125", fix, logger.is_logging)
                        last_oled_update = time.time()
        except: pass
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)