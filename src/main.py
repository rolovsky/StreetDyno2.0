import time
import serial
import sys
import os
import glob
import threading
from flask import Flask, jsonify, render_template_string, send_from_directory
from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("❌ [FEHLER] RPi.GPIO nicht installiert. Bitte prüfen!")

# --- OLED DISPLAY IMPORT ---
try:
    from display_oled import OLEDDisplay
except ImportError:
    try:
        from hw.display_oled import OLEDDisplay
    except ImportError:
        print("❌ [FEHLER] display_oled.py wurde nicht gefunden. Bitte prüfen!")
        sys.exit(1)

# ==========================================
# --- KONFIGURATION STREETDYNO 2.0 ---
# ==========================================
ARDUINO_PORT = '/dev/ttyUSB0'  
ARDUINO_BAUD = 500000          

# !!! HIER DEINEN TASTER-PIN EINTRAGEN (BCM-Nummer) !!!
BUTTON_PIN = 17 

AUTO_START_RPM = 2500
AUTO_STOP_RPM = 2000
MIN_SPEED_KMH = 30.0
LOG_DIR = "/home/rolovsky/streetdyno2.0/logs"

# Globaler Datenspeicher für das Web-Dashboard
telemetry = {
    "rpm": 0, "afr": 0.0, "egt": 0.0, "speed": 0.0, "fix": False, "status": "🟢 IDLE"
}
# ==========================================

# --- FLASK WEBSERVER SETUP ---
app = Flask(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StreetDyno 2.0 - PX125 Lusso</title>
    <style>
        body { background-color: #111; color: #fff; font-family: 'Courier New', Courier, monospace; text-align: center; margin: 0; padding: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; max-width: 600px; margin: 0 auto; }
        .box { background: #222; padding: 20px; border-radius: 10px; border: 1px solid #333; }
        .value { font-size: 3em; font-weight: bold; margin: 10px 0; }
        .label { color: #888; font-size: 1.2em; text-transform: uppercase; }
        #speed { color: #00ffcc; }
        #rpm { color: #ff9800; }
        #afr { color: #ff3366; }
        #egt { color: #ffcc00; }
        #status { font-size: 1.5em; padding: 15px; margin-bottom: 20px; border-radius: 5px; background: #333; }
        .btn { display: inline-block; margin-top: 20px; padding: 15px 30px; background: #ff9800; color: #111; text-decoration: none; font-size: 1.2em; font-weight: bold; border-radius: 5px; }
    </style>
</head>
<body>
    <div id="status">Warte auf Daten...</div>
    <div class="grid">
        <div class="box"><div class="label">Speed (km/h)</div><div class="value" id="speed">0.0</div></div>
        <div class="box"><div class="label">RPM</div><div class="value" id="rpm">0</div></div>
        <div class="box"><div class="label">AFR</div><div class="value" id="afr">0.0</div></div>
        <div class="box"><div class="label">CHT / EGT (°C)</div><div class="value" id="egt">0.0</div></div>
    </div>
    <a href="/logs" class="btn">📂 Log-Dateien herunterladen</a>

    <script>
        setInterval(() => {
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('rpm').innerText = data.rpm.toFixed(0);
                    document.getElementById('speed').innerText = data.speed.toFixed(1);
                    document.getElementById('afr').innerText = data.afr.toFixed(2);
                    document.getElementById('egt').innerText = data.egt.toFixed(1);
                    
                    let statusDiv = document.getElementById('status');
                    statusDiv.innerText = data.status + (data.fix ? " (GPS 3D Fix)" : " (Suche Satelliten...)");
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
        a { color: #ff9800; text-decoration: none; font-size: 1.2em; display: block; margin: 10px 0; padding: 10px; background: #222; border-radius: 5px;}
        a:hover { background: #333; }
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
def index():
    return render_template_string(HTML_DASHBOARD)

@app.route('/api/data')
def api_data():
    return jsonify(telemetry)

@app.route('/logs')
def list_logs():
    files = sorted([os.path.basename(x) for x in glob.glob(os.path.join(LOG_DIR, '*.csv'))], reverse=True)
    rendered_html = HTML_LOGS.replace("{% for file in files %}", "").replace("{% endfor %}", "")
    links = "".join([f'<a href="/download/{f}">📄 {f}</a>' for f in files])
    return rendered_html.replace("<h2>Deine Prüfstands-Logs:</h2>", f"<h2>Deine Prüfstands-Logs:</h2>{links}")

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(LOG_DIR, filename, as_attachment=True)

# --- HARDWARE & LOGGING LOOP (Hintergrund-Thread) ---
def hardware_loop():
    print("🚀 [SYSTEM] Hardware-Thread gestartet...")
    
    # 1. Hardware Initialisieren
    gps = GPS_L76K()
    gps.start()
    logger = CSVLogger(log_dir=LOG_DIR)
    
    # 2. OLED Initialisieren
    oled = OLEDDisplay()
    display_modes = ["RPM", "SPEED", "AFR", "EGT"]
    current_mode_idx = 0
    oled.set_mode(display_modes[current_mode_idx])
    last_oled_update = 0.0

    # 3. Taster-Interrupt Setup
    def button_callback(channel):
        nonlocal current_mode_idx
        current_mode_idx = (current_mode_idx + 1) % len(display_modes)
        oled.set_mode(display_modes[current_mode_idx])
        print(f"🔄 [OLED] Modus gewechselt zu: {display_modes[current_mode_idx]}")

    try:
        GPIO.setmode(GPIO.BCM)
        # Pull-Up Widerstand aktivieren (Schalter schaltet gegen Masse)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        # Bouncetime (300ms) verhindert doppeltes Schalten bei einem Druck
        GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=300)
        print(f"✅ [TASTER] GPIO {BUTTON_PIN} scharfgeschaltet.")
    except Exception as e:
        print(f"❌ [FEHLER] Taster konnte nicht eingerichtet werden: {e}")

    # 4. Serielle Verbindung
    try:
        ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
    except Exception as e:
        print(f"❌ [FEHLER] Arduino nicht gefunden: {e}")
        return

    while True:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line: continue
            
            try:
                parts = line.split(';')
                if len(parts) == 3:
                    rpm_val = float(parts[0])
                    afr_val = float(parts[1])
                    egt_val = float(parts[2])
                else: continue
            except ValueError: continue 

            current_gps_data = gps._data 
            current_speed = current_gps_data.speed_kmh if current_gps_data else 0.0
            current_fix = current_gps_data.fix if current_gps_data else False
            current_lat = current_gps_data.lat if current_gps_data and current_gps_data.lat else 0.0
            current_lon = current_gps_data.lon if current_gps_data and current_gps_data.lon else 0.0

            # --- Dyno Auto-Record Logik ---
            if not logger.is_logging:
                if rpm_val > AUTO_START_RPM and current_speed > MIN_SPEED_KMH:
                    logger.start()
                    telemetry["status"] = "🔴 REC (Vollgas!)"
                else:
                    telemetry["status"] = "🟢 IDLE (Warte auf Pull)"
            else:
                if rpm_val < AUTO_STOP_RPM:
                    logger.stop()
                    telemetry["status"] = "🟢 IDLE (Log gespeichert!)"

            if logger.is_logging:
                logger.log(rpm_val, afr_val, egt_val, current_speed, current_lat, current_lon, current_fix)

            # --- Globale Telemetrie für den Webserver aktualisieren ---
            telemetry["rpm"] = rpm_val
            telemetry["afr"] = afr_val
            telemetry["egt"] = egt_val
            telemetry["speed"] = current_speed
            telemetry["fix"] = current_fix

            # --- OLED Display Update (Frame-Limiter auf 10 Hz) ---
            current_time = time.time()
            if current_time - last_oled_update >= 0.1:
                oled.show_status(
                    rpm=rpm_val, 
                    speed=current_speed, 
                    afr=afr_val, 
                    egt=egt_val, 
                    info="PX125", 
                    gps_fix=current_fix, 
                    is_logging=logger.is_logging
                )
                last_oled_update = current_time

        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    print("\n🏁 --- SYSTEM BEREIT ---")
    print("🌐 Web-Dashboard läuft auf: http://10.42.0.1:8080 (oder Heimnetz-IP)\n")
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)