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
    # Nutze die Pins aus deiner config.py
    for pin in [JS_UP, JS_DOWN, JS_LEFT, JS_RIGHT, JS_PRESS]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print("[OK] Joystick-GPIOs (BCM) aktiv.")

def main():
    # 1. Initialisierung
    setup_gpio()
    display = OLEDDisplay()
    gps = GPS_L76K()
    gps.start()
    
    # Serial Setup mit hoher Baudrate
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0) # Timeout 0 für Non-Blocking
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
            # Wir prüfen die Eingänge sofort. 
            # Die kleinen sleeps (0.15) verhindern 'Geister-Klicks' (Entprellen)
            if not GPIO.input(JS_UP):
                current_mode_idx = (current_mode_idx - 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.15) 
            
            elif not GPIO.input(JS_DOWN):
                current_mode_idx = (current_mode_idx + 1) % len(modes)
                display.set_mode(modes[current_mode_idx])
                time.sleep(0.15)

            if not GPIO.input(JS_PRESS):
                # Toggle Logging (0.5s Sperre gegen Doppelklicks)
                if time.time() - last_press_time > 0.5:
                    logging_active = not logging_active
                    last_press_time = time.time()
                    print(f"\n[LOGGING] {'Gestartet' if logging_active else 'Gestoppt'}")

            # --- B. ARDUINO DATEN (Turbo-Cleanup) ---
            if ser and ser.in_waiting > 0:
                try:
                    # Lies ALLES im Puffer, um Latenz zu vermeiden
                    raw_data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    # Zerlege in Zeilen und nimm die letzte komplette
                    lines = raw_data.split('\n')
                    # Wir gehen rückwärts durch die Zeilen, bis wir ein gültiges Paket finden
                    for i in range(len(lines)-1, -1, -1):
                        line = lines[i].strip()
                        if ";" in line:
                            parts = line.split(';')
                            if len(parts) >= 3:
                                rpm_val = float(parts[0])
                                afr_val = float(parts[1])
                                egt_val = float(parts[2])
                                break # Das ist das aktuellste Paket, fertig.
                except Exception as e:
                    pass # Kaputte Pakete ignorieren

            # --- C. GPS & DISPLAY UPDATE ---
            gps_data = gps.get_data()
            
            # Die Anzeige aktualisieren (Hauptzeitfresser, aber nötig)
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=afr_val, 
                egt=egt_val, 
                info=VEHICLE_NAME, 
                gps_fix=gps_data.fix, 
                is_logging=logging_active
            )

            # Minimales Sleep, um die CPU nicht zu grillen (1ms)
            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n[STOP] Beende StreetDyno...")
    finally:
        gps.stop()
        if ser: ser.close()
        GPIO.cleanup()

if __name__ == "__main__":
    main()