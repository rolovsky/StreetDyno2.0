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
    rpm_sensor = RPMInput(RPM_PIN, PULSES_PER_REV, RPM_AVG_WINDOW_S)
    rpm_sensor.start()
   
    gps = GPS_L76K()
    gps.start()
    
    logger = None
    logging_active = False
    last_press_time = 0

    print("System bereit. Drücke Joystick-Center zum Loggen.")

    try:
        while True:
            loop_start = time.time()
            
            # --- JOYSTICK ABFRAGE ---
            if not GPIO.input(JS_UP):
                display.set_mode("RPM")
            elif not GPIO.input(JS_DOWN):
                display.set_mode("SPEED")
            
            # Start/Stop Toggle (Debounce 0.5s)
            if not GPIO.input(JS_PRESS):
                if time.time() - last_press_time > 0.5:
                    logging_active = not logging_active
                    last_press_time = time.time()
                    if logging_active:
                        logger = CSVLogger(filename_prefix="VMC177_Run")
                        print(f"🔴 Log gestartet: {logger.filename}")
                    else:
                        logger = None
                        print("⚪ Log gestoppt.")
            
            # --- DATEN SAMMELN ---
            rpm_val = rpm_sensor.get_data().rpm
            gps_data = gps.get_data()

            # --- DISPLAY UPDATE ---
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=14.7, # Platzhalter für AFR
                info="LIVE", 
                gps_fix=gps_data.fix,
                is_logging=logging_active
            )

            # --- LOGGEN WENN AKTIV ---
            if logging_active and logger:
                ts = datetime.now(timezone.utc).isoformat()
                logger.log(ts, rpm_val, gps_data.speed_kmh, 0, 0, 14.7, "VMC177")

            # 10Hz Takt einhalten
            elapsed = time.time() - loop_start
            time.sleep(max(0, 0.1 - elapsed))

    except KeyboardInterrupt:
        print("\nBeende System...")
    finally:
        gps.stop()
        display.clear()
        GPIO.cleanup()

if __name__ == "__main__":
    main()