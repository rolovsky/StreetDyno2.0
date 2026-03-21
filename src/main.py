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
    "fix": False, "status": "🟢 IDLE"
}

# ==========================================
# --- FLASK WEBSERVER (Dashboard) ---
# ==========================================
app = Flask(__name__)

# (HTML Code bleibt identisch zur Vorversion für die Übersichtlichkeit gekürzt)
@app.route('/')
def index(): return render_template_string("<h1>StreetDyno 2.0 Dashboard</h1><p>Gehe zu /api/data fuer Rohwerte.</p>")

@app.route('/api/data')
def api_data(): return jsonify(telemetry)

# ==========================================
# --- HARDWARE LOOP (Hintergrund-Thread) ---
# ==========================================
def hardware_loop():
    print("🚀 [SYSTEM] Hardware-Thread startet...")
    
    # 1. GPS DEFINITIV INITIALISIEREN (Deine Befehlskette)
    print("📡 [GPS] Wecke Modul auf und setze Baudrate (115200 raw)...")
    # Schritt A: Wake Up (GPIO 4 LOW)
    os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dl") 
    time.sleep(0.5)
    # Schritt B: Port-Konfiguration (Dein magischer Befehl)
    os.system(f"sudo stty -F {GPS_PORT} 115200 raw -echo")
    time.sleep(0.5)

    # 2. Hardware-Objekte laden
    # WICHTIG: Du musst in hw/gps_l76k.py die Baudrate auf 115200 ändern!
    gps = GPS_L76K() 
    gps.start()
    
    logger = CSVLogger(log_dir=LOG_DIR)
    oled = OLEDDisplay()
    
    display_modes = ["RPM", "SPEED", "AFR", "EGT"]
    mode_idx = 0
    oled.set_mode(display_modes[mode_idx])
    last_oled_update = 0

    # Taster-Callback
    def btn_cb(channel):
        nonlocal mode_idx
        mode_idx = (mode_idx + 1) % len(display_modes)
        oled.set_mode(display_modes[mode_idx])

    if HAS_GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=btn_cb, bouncetime=400)

    # 3. Serielle Verbindung zum Arduino
    try:
        ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
        ser.flush()
    except Exception as e:
        print(f"❌ [SERIAL] Arduino Fehler: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                parts = line.split(';')
                if len(parts) == 3:
                    rpm_val, afr_val, egt_val = float(parts[0]), float(parts[1]), float(parts[2])
                    
                    # GPS Daten
                    curr_gps = gps._data
                    speed = curr_gps.speed_kmh if curr_gps else 0.0
                    fix = curr_gps.fix if curr_gps else False
                    lat, lon = (curr_gps.lat, curr_gps.lon) if curr_gps else (0.0, 0.0)

                    telemetry.update({"rpm": rpm_val, "afr": afr_val, "egt": egt_val, "speed": speed, "fix": fix})

                    # Auto-Logging
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
                    if time.time() - last_oled_update > 0.1:
                        oled.show_status(rpm_val, speed, afr_val, egt_val, "PX125", fix, logger.is_logging)
                        last_oled_update = time.time()

        except Exception as e:
            print(f"⚠️ [LOOP] Fehler: {e}")
        
        time.sleep(0.005)

if __name__ == '__main__':
    threading.Thread(target=hardware_loop, daemon=True).start()
    print("\n🏁 --- STREETDYNO BEREIT ---")
    print("🌐 Dashboard: http://10.42.0.1:8080\n")
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)