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
    print("[Info] Simulations-Modus aktiv.")

# Hardware Importe
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def setup_gpio_global():
    if IS_PI and GPIO:
        GPIO.setmode(GPIO.BCM) #
        GPIO.setwarnings(False) #
        for pin in [JS_UP, JS_DOWN, JS_PRESS]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP) #
        print("[OK] GPIO Global Setup abgeschlossen.")

def main():
    print("StreetDyno 2.0 - Booting...")
    setup_gpio_global()
    
    display = OLEDDisplay() #
    gps = GPS_L76K() #
    gps.start() #
    
    # Serielle Verbindung zum Arduino aufbauen
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
        print(f"[OK] Verbindung zu Arduino an {SERIAL_PORT} hergestellt.")
    except Exception as e:
        print(f"[Fehler] Arduino nicht gefunden: {e}")
        ser = None

    logger = None
    logging_active = False
    last_press_time = 0
    
    # Initialwerte
    rpm_val = 0.0
    afr_val = 14.7
    egt_val = 0.0

    try:
        while True:
            loop_start = time.time() #

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
                except Exception as e:
                    print(f"Serial Error: {e}")

            # 2. Joystick Abfrage
            if IS_PI and GPIO:
                if not GPIO.input(JS_UP):
                    display.set_mode("RPM") #
                elif not GPIO.input(JS_DOWN):
                    display.set_mode("SPEED") #
                
                if not GPIO.input(JS_PRESS):
                    if time.time() - last_press_time > 0.5: #
                        logging_active = not logging_active #
                        last_press_time = time.time()
                        if logging_active:
                            logger = CSVLogger(filename_prefix="VMC177_Run") #
                        else:
                            logger = None

            gps_data = gps.get_data() #

            # 3. Anzeige aktualisieren
            display.show_status(
                rpm=rpm_val,
                speed=gps_data.speed_kmh,
                afr=afr_val, # Jetzt mit echtem Wert!
                info="LIVE",
                gps_fix=gps_data.fix,
                is_logging=logging_active
            )

            # 4. Logging (10Hz)
            if logging_active and logger:
                ts = datetime.now(timezone.utc).isoformat() #
                # Header: timestamp, rpm, speed_kmh, lat, lon, afr, note
                logger.log(ts, rpm_val, gps_data.speed_kmh, gps_data.lat, gps_data.lon, afr_val, "VMC177")

            time.sleep(max(0, 0.1 - (time.time() - loop_start))) #

    except KeyboardInterrupt:
        print("\nShutdown...")
    finally:
        if gps: gps.stop()
        if display: display.clear()
        if ser: ser.close()
        if IS_PI and GPIO:
            GPIO.cleanup() #

if __name__ == "__main__":
    main()