import time
import serial
from config import *

try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def setup_gpio():
    if IS_PI:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        # Pins aus der config.py laden
        for pin in [JS_UP, JS_DOWN, JS_PRESS]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print("[OK] Joystick-GPIOs initialisiert.")

def main():
    setup_gpio()
    display = OLEDDisplay()
    gps = GPS_L76K()
    gps.start()
    
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.01)
        ser.flushInput() # Puffer beim Start leeren
        print(f"[OK] Arduino an {SERIAL_PORT} bereit.")
    except Exception as e:
        print(f"[X] Serial Fehler: {e}")
        ser = None

    # Status-Variablen
    rpm_val, afr_val, egt_val = 0.0, 14.7, 0.0
    logging_active = False
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    current_mode_idx = 0
    last_press_time = 0

    while True:
        # --- 1. JOYSTICK ABFRAGE ---
        if IS_PI:
            if not GPIO.input(JS_UP):
                current_mode_idx = (current_mode_idx - 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.2) # Entprellen
            
            elif not GPIO.input(JS_DOWN):
                current_mode_idx = (current_mode_idx + 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.2)

            if not GPIO.input(JS_PRESS):
                if time.time() - last_press_time > 0.5:
                    logging_active = not logging_active
                    last_press_time = time.time()
                    print(f"Logging: {logging_active}")
                    # Hier könnte man den Logger starten/stoppen

        # --- 2. DATEN VOM ARDUINO (Turbo-Mode ohne Verzögerung) ---
        if ser and ser.in_waiting > 0:
            try:
                # Wir lesen alles, was da ist, und nehmen die letzte Zeile
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                lines = data.split('\n')
                for i in range(len(lines)-1, -1, -1):
                    line = lines[i].strip()
                    if ";" in line:
                        parts = line.split(';')
                        if len(parts) >= 3:
                            rpm_val = float(parts[0])
                            afr_val = float(parts[1])
                            egt_val = float(parts[2])
                            break # Neueste Daten gefunden, fertig.
            except: pass

        # --- 3. GPS & DISPLAY ---
        gps_data = gps.get_data()
        display.show_status(
            rpm_val, 
            gps_data.speed_kmh, 
            afr_val, 
            egt_val, 
            VEHICLE_NAME, 
            gps_data.fix, 
            logging_active
        )

        time.sleep(0.02) # Kurze Pause für CPU-Entlastung

if __name__ == "__main__":
    main()