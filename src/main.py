import time
import serial
import RPi.GPIO as GPIO
import threading
from config import *

# Hardware-Modules
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

# Globale Variablen für den Thread-Austausch
current_gps_data = None

def gps_thread_function(gps_instance):
    """Dieser Thread kümmert sich nur um das GPS, ohne die Main-Loop zu bremsen."""
    global current_gps_data
    while True:
        current_gps_data = gps_instance.get_data()
        time.sleep(0.1) # GPS aktualisiert eh nur mit 10Hz

def setup_gpio():
    """Initialisiert die Joystick-Pins am Pi"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in [JS_UP, JS_DOWN, JS_PRESS]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print("[OK] Joystick-GPIOs aktiv.")

def main():
    global current_gps_data
    setup_gpio()
    display = OLEDDisplay()
    
    # GPS Setup & Thread Start
    gps = GPS_L76K()
    gps.start()
    t = threading.Thread(target=gps_thread_function, args=(gps,), daemon=True)
    t.start()
    print("[OK] GPS-Hintergrund-Thread gestartet.")
    
    # Serial Setup (Non-Blocking)
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0) 
        ser.flushInput()
        print(f"[OK] Arduino-Stream aktiv ({SERIAL_BAUD} Baud).")
    except Exception as e:
        print(f"[X] Serial Fehler: {e}")
        ser = None

    # Status-Variablen
    rpm_val, afr_val, egt_val = 0.0, 14.7, 0.0
    logging_active = False
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    current_mode_idx = 0
    last_press_time = 0

    print("[GO] StreetDyno 2.0 im Low-Latency Mode!")

    try:
        while True:
            # --- 1. JOYSTICK (Sofort-Reaktion) ---
            if not GPIO.input(JS_UP):
                current_mode_idx = (current_mode_idx - 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.1) # Kürzere Entprellzeit für mehr Speed
            
            elif not GPIO.input(JS_DOWN):
                current_mode_idx = (current_mode_idx + 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.1)

            if not GPIO.input(JS_PRESS):
                if time.time() - last_press_time > 0.4:
                    logging_active = not logging_active
                    last_press_time = time.time()

            # --- 2. ARDUINO DATEN (Echtzeit-Parsing) ---
            if ser and ser.in_waiting > 0:
                try:
                    # Wir lesen alles und springen sofort zum Ende
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

            # --- 3. DISPLAY UPDATE ---
            # Wir nutzen die GPS-Daten aus dem Thread (falls vorhanden)
            speed = current_gps_data.speed_kmh if current_gps_data else 0.0
            fix = current_gps_data.fix if current_gps_data else False
            
            display.show_status(
                rpm=rpm_val, 
                speed=speed, 
                afr=afr_val, 
                egt=egt_val, 
                info=VEHICLE_NAME, 
                gps_fix=fix, 
                is_logging=logging_active
            )

            # Kein künstliches Sleep mehr, oder nur minimalst
            # Das OLED-Update selbst dauert ca. 20ms, das ist unsere natürliche Bremse.
            time.sleep(0.0001)

    except KeyboardInterrupt:
        print("\n[STOP] Beende...")
    finally:
        gps.stop()
        if ser: ser.close()
        GPIO.cleanup()

if __name__ == "__main__":
    main()