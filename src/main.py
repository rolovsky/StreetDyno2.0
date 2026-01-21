import time
import RPi.GPIO as GPIO
from datetime import datetime, timezone
from config import *

# Hardware Importe
from hw.rpm_input import RPMInput
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

# Joystick Pins für Waveshare 1.3 OLED HAT
JS_UP = 6
JS_DOWN = 19
JS_PRESS = 13

def setup_joystick():
    if hasattr(GPIO, 'setmode'):
        GPIO.setmode(GPIO.BCM)
        for pin in [JS_UP, JS_DOWN, JS_PRESS]:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def main():
    print("StreetDyno 2.0 - Booting...")
    setup_joystick()
    
    display = OLEDDisplay()
    if display.device:
        display.show_status(0, 0, 0, "BOOTING...", False)
        time.sleep(0.5)

    rpm_sensor = RPMInput(RPM_PIN, PULSES_PER_REV, RPM_AVG_WINDOW_S)
    rpm_sensor.start()
   
    gps = GPS_L76K()
    gps.start()
    
    logger = CSVLogger(filename_prefix="VMC177_Run")
    print("Logging aktiv...")

    try:
        while True:
            loop_start = time.time()
            
            # 1. Joystick Abfrage für Display-Modus
            if not GPIO.input(JS_UP):
                display.set_mode("RPM")
            elif not GPIO.input(JS_DOWN):
                display.set_mode("SPEED")
            elif not GPIO.input(JS_PRESS):
                display.set_mode("AFR")

            # 2. Daten sammeln
            rpm_val = rpm_sensor.get_data().rpm
            gps_data = gps.get_data()
            ts = datetime.now(timezone.utc).isoformat()

            # 3. Display Update (Minimalistisch & Fokus-orientiert)
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=14.7, 
                info="LIVE", 
                gps_fix=gps_data.fix
            )

            # 4. Loggen
            logger.log(ts, rpm_val, gps_data.speed_kmh, 0, 0, 14.7, "1:50_MIX")

            # Exakte 10Hz einhalten
            elapsed = time.time() - loop_start
            time.sleep(max(0, 0.1 - elapsed))

    except KeyboardInterrupt:
        print("\nBeende Logger...")
    finally:
        gps.stop()
        if display.device:
            display.clear()
        GPIO.cleanup()

if __name__ == "__main__":
    main()