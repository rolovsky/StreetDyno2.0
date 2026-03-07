import time
import serial
import RPi.GPIO as GPIO
from config import *

# Hardware-Modules
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def setup_gpio():
    """Initialisiert die Joystick-Pins am Pi"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    # Nur die Pins nutzen, die in der config.py definiert sind
    for pin in [JS_UP, JS_DOWN, JS_PRESS]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print("[OK] Joystick-GPIOs (UP, DOWN, PRESS) aktiv.")

def main():
    # 1. Initialisierung
    setup_gpio()
    display = OLEDDisplay()
    gps = GPS_L76K()
    gps.start()
    
    # Serial Setup mit hoher Baudrate und Non-Blocking Mode
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

    print("[GO] StreetDyno 2.0 läuft...")

    try:
        while True:
            # --- A. JOYSTICK (Echtzeit-Abfrage) ---
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
                    print(f"\n[LOGGING] {'Gestartet' if logging_active else 'Gestoppt'}")

            # --- B. ARDUINO DATEN (Turbo-Cleanup gegen Delay) ---
            if ser and ser.in_waiting > 0:
                try:
                    # Gesamten Puffer lesen, um Latenz zu vermeiden
                    raw_data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    lines = raw_data.split('\n')
                    # Rückwärts suchen nach dem aktuellsten vollständigen Paket
                    for i in range(len(lines)-1, -1, -1):
                        line = lines[i].strip()
                        if ";" in line:
                            parts = line.split(';')
                            if len(parts) >= 3:
                                rpm_val = float(parts[0])
                                afr_val = float(parts[1])
                                egt_val = float(parts[2])
                                break 
                except Exception:
                    pass 

            # --- C. GPS & DISPLAY UPDATE ---
            gps_data = gps.get_data()
            
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=afr_val, 
                egt=egt_val, 
                info=VEHICLE_NAME, 
                gps_fix=gps_data.fix, 
                is_logging=logging_active
            )

            # Minimales Sleep für die CPU-Entlastung
            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n[STOP] Beende StreetDyno...")
    finally:
        gps.stop()
        if ser: ser.close()
        GPIO.cleanup()

if __name__ == "__main__":
    main()