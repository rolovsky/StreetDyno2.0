import serial
import time
import csv
import os
import sys
import RPi.GPIO as GPIO
from datetime import datetime
import gpsd
from collections import deque
import statistics

# ==========================================
# --- KONFIGURATION (Golden Master) ---
# ==========================================
SERIAL_PORT = '/dev/ttyUSB0'  # Falls dein Arduino an ttyACM0 hängt, hier ändern!
BAUD_RATE = 115200
BUTTON_PIN = 21               # Waveshare Taster Pin
LOG_DIR = '/home/pi/dyno_logs'

# Filter-Parameter für die RPM-Sanierung
RPM_WINDOW = 5
EMA_ALPHA = 0.3

# ==========================================
# --- GLOBALE VARIABLEN ---
# ==========================================
is_logging = False
csv_writer = None
log_file = None
rpm_history = deque(maxlen=RPM_WINDOW)
rpm_display = 0.0

# ==========================================
# --- SETUP ---
# ==========================================
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# GPS initialisieren
try:
    gpsd.connect()
    print("[OK] gpsd verbunden.")
except Exception as e:
    print(f"[FEHLER] gpsd nicht erreichbar: {e}")

# Waveshare Taster Setup (mit sauberem Hardware-PullUp & Entprellung)
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def toggle_logging(channel):
    global is_logging, csv_writer, log_file
    
    if not is_logging:
        # Start Logging
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{LOG_DIR}/dyno_log_{timestamp}.csv"
        log_file = open(filename, mode='w', newline='')
        csv_writer = csv.writer(log_file)
        # Header schreiben
        csv_writer.writerow(['Time', 'RPM', 'AFR', 'EGT', 'Speed_kmh', 'Lat', 'Lon', 'GPS_Fix'])
        is_logging = True
        # Saubere Konsolen-Trennung beim Start
        print(f"\n[REC] Logging GESTARTET: {filename}")
    else:
        # Stop Logging
        is_logging = False
        if log_file:
            log_file.close()
        print("\n[STOP] Logging BEENDET.")

# Interrupt für den Taster (300ms Bouncetime gegen Prellen)
GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=toggle_logging, bouncetime=300)

# Arduino Serial Setup
try:
    arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
    print(f"[OK] Arduino auf {SERIAL_PORT} mit {BAUD_RATE} Baud verbunden.")
except Exception as e:
    print(f"[FEHLER] Arduino nicht gefunden: {e}")
    sys.exit(1)

print("--- STREETDYNO BEREIT ---")
print("Drücke den Waveshare-Taster zum Starten/Stoppen der Aufnahme.\n")

# ==========================================
# --- MAIN LOOP ---
# ==========================================
try:
    while True:
        if arduino.in_waiting > 0:
            try:
                # Rohdaten vom Arduino lesen (Format: RPM,AFR,EGT)
                line = arduino.readline().decode('utf-8').strip()
                data = line.split(',')
                
                if len(data) == 3:
                    raw_rpm = float(data[0])
                    afr = float(data[1])
                    egt = float(data[2])
                    
                    # --- DER FILTER-BLOCK ---
                    # 1. Spikes über den Median killen
                    rpm_history.append(raw_rpm)
                    median_rpm = statistics.median(rpm_history)
                    
                    # 2. Kurve mit EMA glätten (das ist der Wert für Display UND Log!)
                    rpm_display = (EMA_ALPHA * median_rpm) + ((1 - EMA_ALPHA) * rpm_display)
                    
                    # --- GPS DATEN HOLEN ---
                    speed_kmh = 0.0
                    lat, lon = 0.0, 0.0
                    has_fix = False
                    
                    try:
                        packet = gpsd.get_current()
                        if packet.mode >= 2: # 2D oder 3D Fix
                            has_fix = True
                            speed_kmh = packet.speed() * 3.6 # m/s in km/h
                            lat, lon = packet.position()
                    except Exception:
                        pass # gpsd wirft manchmal Exceptions, wenn kein Signal da ist
                    
                    # --- INLINE DASHBOARD ---
                    status_sym = "🔴 REC" if is_logging else "🟢 RDY"
                    fix_sym = "🛰️ OK" if has_fix else "🛰️ --"
                    # \r überschreibt die aktuelle Zeile für ein flackerfreies Dashboard
                    sys.stdout.write(f"\r[{status_sym}] {fix_sym} | RPM: {int(rpm_display):04d} | AFR: {afr:.1f} | EGT: {int(egt):03d}°C | Speed: {speed_kmh:.1f} km/h   ")
                    sys.stdout.flush()
                    
                    # --- LOGGING ---
                    if is_logging and csv_writer:
                        now = datetime.now().strftime("%H:%M:%S")
                        # HIER IST DER FIX: Wir schreiben rpm_display statt raw_rpm
                        csv_writer.writerow([now, round(rpm_display, 1), afr, egt, round(speed_kmh, 1), lat, lon, has_fix])
                        
            except ValueError:
                # Überspringt kaputte serielle Zeilen (z.B. beim Start)
                pass
            
        time.sleep(0.05) # Kurze Pause, um die CPU zu schonen

except KeyboardInterrupt:
    print("\n[INFO] Programm manuell beendet.")
finally:
    if is_logging and log_file:
        log_file.close()
    arduino.close()
    GPIO.cleanup()
