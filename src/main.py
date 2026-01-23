import time
import sys

# Mock-Check
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False
    GPIO = None
    print("[Info] Simulations-Modus aktiv.")

from datetime import datetime, timezone
from config import *

# Hardware Importe
from hw.rpm_input import RPMInput
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

# Joystick Pins
JS_UP = 6
JS_DOWN = 19
JS_PRESS = 13

def setup_gpio_global():
    if IS_PI and GPIO:
        # Globalen Modus setzen, bevor irgendeine Hardware-Klasse geladen wird
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        # Joystick Setup
        for pin in [JS_UP, JS_DOWN, JS_PRESS]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        print("[OK] GPIO Global Setup abgeschlossen.")

def main():
    print("StreetDyno 2.0 - Booting...")
    
    # 1. GPIO Modus global festlegen
    setup_gpio_global()
    
    # 2. Display initialisieren (setzt intern seine Pins als OUTPUT)
    display = OLEDDisplay()
    
    # 3. RPM Sensor initialisieren
    rpm_sensor = RPMInput(RPM_PIN, PULSES_PER_REV, RPM_AVG_WINDOW_S)
    rpm_sensor.start()
   
    # 4. GPS initialisieren
    gps = GPS_L76K()
    gps.start()
    
    logger = None
    logging_active = False
    last_press_time = 0

    try:
        while True:
            loop_start = time.time()
            
            # Joystick Abfrage
            if IS_PI and GPIO:
                if not GPIO.input(JS_UP):
                    display.set_mode("RPM")
                elif not GPIO.input(JS_DOWN):
                    display.set_mode("SPEED")
                
                if not GPIO.input(JS_PRESS):
                    if time.time() - last_press_time > 0.5:
                        logging_active = not logging_active
                        last_press_time = time.time()
                        if logging_active:
                            logger = CSVLogger(filename_prefix="VMC177_Run")
                        else:
                            logger = None
            
            rpm_val = rpm_sensor.get_data().rpm
            gps_data = gps.get_data()

            # Anzeige aktualisieren
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=14.7, 
                info="LIVE", 
                gps_fix=gps_data.fix,
                is_logging=logging_active
            )

            # Logging
            if logging_active and logger:
                ts = datetime.now(timezone.utc).isoformat()
                logger.log(ts, rpm_val, gps_data.speed_kmh, 0, 0, 14.7, "VMC177")

            # Timing (10Hz)
            time.sleep(max(0, 0.1 - (time.time() - loop_start)))

    except KeyboardInterrupt:
        print("\nShutdown...")
    finally:
        if gps: gps.stop()
        if rpm_sensor: rpm_sensor.stop()
        if display: display.clear()
        # Cleanup am Ende verhindert, dass Pins für den nächsten Start blockiert sind
        if IS_PI and GPIO:
            GPIO.cleanup()

if __name__ == "__main__":
    main()