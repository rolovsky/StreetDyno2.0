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

# OLED-Suche (Haupt- oder Unterordner)
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
ARDUINO_BAUD = 115200          

# Waveshare OLED HAT: Key1 = 21, GPS-Wake = 4
BUTTON_PIN = 21   
GPS_WAKE_PIN = 4  
GPS_PORT = "/dev/ttyAMA0"

AUTO_START_RPM = 2500
AUTO_STOP_RPM = 2000
MIN_SPEED_KMH = 30.0
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"

# Globaler Datenspeicher
telemetry = {
    "rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, 
    "fix": False, "status": "🟢 IDLE", "mode": "RPM"
}

# ==========================================
# --- FLASK WEBSERVER SETUP (Frontend) ---
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
        body { background-color: #111; color: #fff; font-family: 'Courier New', monospace; text-align: center; margin: 0; padding: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; max-width: 600px; margin: 0 auto; }
        .box { background: #222; padding: 20px; border-radius: 10px; border: 1px solid #333; }
        .value { font-size: 3em; font-weight: bold; margin: 10px 0; }
        #speed { color: #00ffcc; } #rpm { color: #ff9800; } #afr { color: #ff3366; } #egt { color: #ffcc00; }
        #status { font-size: 1.5em; padding: 15px; margin-bottom: 20px; border-radius: 5px; background: #333; }
        .btn { display: inline-block; margin-top: 20px; padding: 15px 30px; background: #ff9800; color: #111; text-decoration: none; font-size: 1.2em; font-weight: bold; border-radius: 5px; }
    </style>
</head>
<body>
    <div id="status">Warte auf Daten...</div>
    <div class="grid">
        <div class="box"><div>KM/H</div><div class="value" id="speed">0.0</div></div>
        <div class="box"><div>RPM</div><div class="value" id="rpm">0</div></div>
        <div class="box"><div>AFR</div><div class="value" id="afr">0.0</div></div>
        <div class="box"><div>EGT</div><div class="value" id="egt">0.0</div></div>
    </div>
    <a href="/logs" class="btn">📂 LOGS ANZEIGEN</a>
    <script>
        setInterval(() => {
            fetch('/api/data').then(res => res.json()).then(data => {
                document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                document.getElementById('speed').innerText = data.speed.toFixed(1);
                document.getElementById('afr').innerText = data.afr.toFixed(2);
                document.getElementById('egt').innerText = data.egt.toFixed(1);
                let statusDiv = document.getElementById('status');
                statusDiv.innerText = data.status + (data.fix ? " (GPS FIX)" : " (NO FIX)");
                statusDiv.style.color = data.status.includes('REC') ? '#ff3366' : '#00ffcc';
            });
        }, 200);
    </script>
</body>
</html>
"""

HTML_LOGS = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StreetDyno Logs</title>
    <style>
        body { background-color: #111; color: #fff; font-family: monospace; padding: 20px; }
        a { color: #ff9800; text-decoration: none; font-size: 1.2em; display: block; margin: 10px 0; padding: 10px; background: #222; border-radius: 5px; margin-bottom: 5px; }
        .back { color: #00ffcc; margin-bottom: 20px; display: inline-block; }
    </style>
</head>
<body>
    <a href="/" class="back">🔙 Zurück zum Dashboard</a>
    <h2>Deine Prüfstands-Logs:</h2>
    {% for file in files %}
        <a href="/download/{{ file }}">📄 {{ file }}</a>
    {% endfor %}
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
    rendered_html = HTML_LOGS.replace("{% for file in files %}", "").replace("{% endfor %}", "")
    links = "".join([f'<a href="/download/{f}">📄 {f}</a>' for f in files])
    return rendered_html.replace("<h2>Deine Prüfstands-Logs:</h2>", f"<h2>Deine Prüfstands-Logs:</h2>{links}")

@app.route('/download/<filename>')
def download(filename): return send_from_directory(LOG_DIR, filename, as_attachment=True)

# ==========================================
# --- HARDWARE LOOP (Hintergrund-Thread) ---
# ==========================================
def hardware_loop():
    print("🚀 [SYSTEM] Hardware-Thread gestartet...")
    
    # 1. GPS DEFINITIV INITIALISIEREN (Wakeup + STTY Fix)
    print("📡 [GPS] Wecke Modul auf und setze Baudrate (115200 raw)...")
    os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dl") 
    time.sleep(0.5)
    os.system(f"sudo stty -F {GPS_PORT} 115200 raw -echo")
    time.sleep(0.5)

    # 2. Hardware Objekte
    gps = GPS_L76K() 
    gps.start()
    logger = CSVLogger(log_dir=LOG_DIR)
    oled = OLEDDisplay()
    
    display_modes = ["RPM", "SPEED", "AFR", "EGT"]
    mode_idx = 0
    oled.set_mode(display_modes[mode_idx])
    last_oled_update = 0

    # 3. Taster-Setup (OLED HAT Key1)
    def button_callback(channel):
        nonlocal mode_idx
        mode_idx = (mode_idx + 1) % len(display_modes)
        oled.set_mode(display_modes[mode_idx])
        print(f"🔄 [OLED] Modus: {display_modes[mode_idx]}")

    if HAS_GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=400)

    # 4. Serielle Verbindung zum Arduino
    try:
        ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
        ser.flush()
    except Exception as e:
        print(f"❌ [SERIAL] Arduino nicht gefunden: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line or ';' not in line: continue
                
                parts = line.split(';')
                if len(parts) == 3:
                    rpm_val, afr_val, egt_val = float(parts[0]), float(parts[1]), float(parts[2])
                    
                    # GPS Daten verarbeiten
                    curr_gps = gps._data 
                    speed = curr_gps.speed_kmh if curr_gps else 0.0
                    fix = curr_gps.fix if curr_gps else False
                    lat = curr_gps.lat if curr_gps and curr_gps.lat else 0.0
                    lon = curr_gps.lon if curr_gps and curr_gps.lon else 0.0

                    # Telemetrie Update
                    telemetry.update({"rpm": rpm_val, "afr": afr_val, "egt": egt_val, "speed": speed, "fix": fix})

                    # Auto-Record Logik
                    if not logger.is_logging:
                        if rpm_val > AUTO_START_RPM and speed > MIN_SPEED_KMH:
                            logger.start()
                            telemetry["status"] = "🔴 REC"
                        else:
                            telemetry["status"] = "🟢 IDLE"
                    else:
                        logger.log(rpm_val, afr_val, egt_val, speed, lat, lon, fix)
                        if rpm_val < AUTO_STOP_RPM:
                            logger.stop()
                            telemetry["status"] = "🟢 IDLE"

                    # OLED Update (10 Hz)
                    if time.time() - last_oled_update >= 0.1:
                        oled.show_status(rpm_val, speed, afr_val, egt_val, "PX125", fix, logger.is_logging)
                        last_oled_update = time.time()

        except Exception as e:
            print(f"⚠️ [LOOP] Fehler: {e}")
        
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    print("\n🏁 --- STREETDYNO 2.0 BEREIT ---")
    print("🌐 Dashboard: http://10.42.0.1:8080\n")
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)