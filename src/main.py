import time
import serial
import RPi.GPIO as GPIO
import threading
from config import *

# Hardware-Modules
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

# Globale Variable für den Datenaustausch zwischen Thread und Main-Loop
current_gps_data = None

def gps_thread_function(gps_instance):
    """Holt die GPS-Daten im Hintergrund ab, damit die Hauptschleife nicht laggt."""
    global current_gps_data
    while True:
        # Hier wird die aktualisierte get_data() Methode genutzt
        current_gps_data = gps_instance.get_data()
        time.sleep(0.1) # 10Hz reicht für GPS völlig aus

def setup_gpio():
    """Initialisiert die Joystick-Pins am Pi"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    # Nur die in config.py definierten Pins initialisieren
    for pin in [JS_UP, JS_DOWN, JS_PRESS]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print("[OK] Joystick-GPIOs (UP, DOWN, PRESS) aktiv.")

def main():
    global current_gps_data
    setup_gpio()
    display = OLEDDisplay()
    
    # --- NEU: Logger scharf schalten ---
    logger = CSVLogger()
    
    # GPS Setup & Hintergrund-Thread starten
    gps = GPS_L76K()
    gps.start()
    t = threading.Thread(target=gps_thread_function, args=(gps,), daemon=True)
    t.start()
    print("[OK] GPS-Hintergrund-Thread aktiv.")
    
    # Serial Setup zum Arduino (Non-Blocking)
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0) 
        ser.flushInput()
        print(f"[OK] Kommunikation mit Arduino auf {SERIAL_PORT} gestartet.")
    except Exception as e:
        print(f"[X] Serial Fehler: {e}")
        ser = None

    # Status-Variablen
    rpm_val, afr_val, egt_val = 0.0, 14.7, 0.0
    logging_active = False
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    current_mode_idx = 0
    last_press_time = 0

    print("[GO] StreetDyno 2.0 läuft im Low-Latency Mode.")

    try:
        while True:
            # --- 1. JOYSTICK ABFRAGE ---
            if not GPIO.input(JS_UP):
                current_mode_idx = (current_mode_idx - 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.15) # Entprellen
            
            elif not GPIO.input(JS_DOWN):
                current_mode_idx = (current_mode_idx + 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.15)

            if not GPIO.input(JS_PRESS):
                if time.time() - last_press_time > 0.5:
                    logging_active = not logging_active
                    last_press_time = time.time()
                    print(f"Logging: {'AN' if logging_active else 'AUS'}")
                    
                    # --- NEU: Logger bei Tastendruck triggern ---
                    if logging_active:
                        logger.start()
                    else:
                        logger.stop()

            # --- 2. ARDUINO DATEN (Turbo-Cleanup) ---
            if ser and ser.in_waiting > 0:
                try:
                    # Lies alles im Puffer, nimm nur das Neueste
                    raw = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    lines = raw.split('\n')
                    for i in range(len(lines)-1, -1, -1):
                        line = lines[i].strip()
                        if ";" in line:
                            parts = line.split(';')
                            if len(parts) >= 3:
                                rpm_val = float(parts[0])
                                afr_val = float(parts[1])
                                egt_val = float(parts[2])
                                break 
                except:
                    pass

            # --- 3. GPS & DISPLAY UPDATE ---
            # Daten aus dem globalen GPS-Speicher ziehen
            current_speed = current_gps_data.speed_kmh if current_gps_data else 0.0
            current_fix = current_gps_data.fix if current_gps_data else False
            
            display.show_status(
                rpm=rpm_val, 
                speed=current_speed, 
                afr=afr_val, 
                egt=egt_val, 
                info=VEHICLE_NAME, 
                gps_fix=current_fix, 
                is_logging=logging_active
            )

            # --- 4. NEU: In die CSV schreiben ---
            if logging_active:
                logger.log(rpm_val, afr_val, egt_val, current_speed, current_fix)

            # Winziges Sleep für die CPU (0.1ms), das OLED ist eh die Bremse
            time.sleep(0.0001)

    except KeyboardInterrupt:
        print("\n[STOP] StreetDyno beendet.")
    finally:
        # Falls du das Skript abwürgst, Datei sauber schließen
        if logging_active:
            logger.stop()
        gps.stop()
        if ser: ser.close()
        GPIO.cleanup()

if __name__ == "__main__":
    main()