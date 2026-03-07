import time
import sys
import serial
from datetime import datetime, timezone
from config import *

# Mock-Check für GPIO
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False
    GPIO = None

# Hardware Importe
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

# Joystick Pins
JS_UP = 6
JS_DOWN = 19
JS_PRESS = 13

def setup_gpio_global():
    if IS_PI and GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in [JS_UP, JS_DOWN, JS_PRESS]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print("[OK] GPIO Global Setup abgeschlossen.")

def main():
    print("StreetDyno 2.0 - Booting...")
    setup_gpio_global()

    display = OLEDDisplay()
    gps = GPS_L76K()
    gps.start()

    # Serielle Verbindung zum Arduino (Baudrate muss mit Arduino übereinstimmen)
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
        print(f"[OK] Verbindung zu Arduino an {SERIAL_PORT} hergestellt.")
    except Exception as e:
        print(f"[Fehler] Arduino Verbindung fehlgeschlagen: {e}")
        ser = None

    logger = None
    logging_active = False
    last_press_time = 0
    
    # Initialwerte für Sensoren
    rpm_val = 0.0
    afr_val = 14.7
    egt_val = 0.0
    
    # Verfügbare Display-Modi
    modes = ["RPM", "SPEED", "AFR", "EGT"]
    current_mode_idx = 0

    try:
        while True:
            loop_start = time.time()

            # 1. Daten vom Arduino lesen (Format: RPM;AFR;EGT)
            if ser and ser.in_waiting > 0:
                try:
                    line = ser.readline().decode('utf-8').strip()
                    if line:
                        parts = line.split(';')
                        if len(parts) >= 2:
                            rpm_val = float(parts[0])
                            afr_val = float(parts[1])
                            if len(parts) >= 3:
                                egt_val = float(parts[2])
                except (ValueError, IndexError):
                    pass 

            # 2. Joystick Abfrage für Modus-Wechsel und Logging
            if IS_PI and GPIO:
                if not GPIO.input(JS_UP):
                    current_mode_idx = (current_mode_idx - 1) % len(modes)
                    display.set_mode(modes[current_mode_idx])
                    time.sleep(0.2) # Entprellen
                elif not GPIO.input(JS_DOWN):
                    current_mode_idx = (current_mode_idx + 1) % len(modes)
                    display.set_mode(modes[current_mode_idx])
                    time.sleep(0.2) # Entprellen

                if not GPIO.input(JS_PRESS):
                    if time.time() - last_press_time > 0.5:
                        logging_active = not logging_active
                        last_press_time = time.time()
                        if logging_active:
                            logger = CSVLogger(filename_prefix="VMC177_Run")
                        else:
                            logger = None

            # 3. GPS Daten abrufen
            gps_data = gps.get_data()

            # 4. Anzeige aktualisieren
            display.show_status(
                rpm=rpm_val,
                speed=gps_data.speed_kmh,
                afr=afr_val,
                egt=egt_val,
                info="LIVE",
                gps_fix=gps_data.fix,
                is_logging=logging_active
            )

            # 5. Daten-Logging (ca. 10Hz)
            if logging_active and logger:
                ts = datetime.now(timezone.utc).isoformat()
                logger.log(ts, rpm_val, gps_data.speed_kmh, gps_data.lat, gps_data.lon, afr_val, egt_val, "VMC177")

            # Timing kontrollieren für konstante 10Hz
            time.sleep(max(0, 0.1 - (time.time() - loop_start)))

    except KeyboardInterrupt:
        print("\nBeende StreetDyno...")
    finally:
        if gps: gps.stop()
        if display: display.clear()
        if ser: ser.close()
        if IS_PI and GPIO: GPIO.cleanup()

if __name__ == "__main__":
    main()