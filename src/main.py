import time
from datetime import datetime
from config import *

# Hardware Importe
from hw.rpm_input import RPMInput
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def main():
    print("🚀 StreetDyno 2.0 - Booting...")
    
    # 1. OLED initialisieren & Start-Logo
    display = OLEDDisplay()
    if display.device:
        display.show_status(0, 0, 0, "BOOTING...", False)
        time.sleep(0.5)

    # 2. Hardware-Checks
    status_info = "HW: "
    
    # RPM Check
    rpm_sensor = RPMInput(RPM_PIN, PULSES_PER_REV, RPM_AVG_WINDOW_S)
    rpm_sensor.start()
    status_info += "RPM ✅ "
    
    # GPS Check
    gps = GPS_L76K()
    if gps.ser:
        status_info += "GPS ✅"
    else:
        status_info += "GPS ❌"
    
    # Logger Check
    try:
        logger = CSVLogger(filename_prefix="VMC177_Run")
        status_info += " LOG ✅"
    except:
        status_info += " LOG ❌"

    print(status_info)
    if display.device:
        display.show_status(0, 0, 0, status_info, False)
        time.sleep(1.5)

    # 3. Hauptschleife (10 Hz)
    print("🔴 Logging aktiv...")
    try:
        while True:
            loop_start = time.time()
            
            # Daten sammeln
            rpm_val = rpm_sensor.get_data().rpm
            gps_data = gps.get_data()
            ts = datetime.utcnow().isoformat()

            # Display Update
            display.show_status(
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                afr=14.7, # Platzhalter
                info="SD 2.0 LIVE", 
                gps_fix=gps_data.fix
            )

            # Loggen
            logger.log(ts, rpm_val, gps_data.speed_kmh, 0, 0, 14.7, "1:50_MIX")

            # Exakte 10Hz einhalten
            elapsed = time.time() - loop_start
            time.sleep(max(0, 0.1 - elapsed))

    except KeyboardInterrupt:
        print("\nBeende Logger...")
    finally:
        rpm_sensor.stop()
        gps.stop()
        if display.device:
            display.clear()

if __name__ == "__main__":
    main()