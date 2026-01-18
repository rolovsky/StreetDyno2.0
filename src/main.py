import time
from datetime import datetime
from config import *

# Unsere neuen Module
from hw.rpm_input import RPMInput
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def main():
    print("--- 🚀 StreetDyno 2.0 Start (VMC 177 Setup) ---")
    
    # 1. Initialisierung
    logger = CSVLogger(filename_prefix="VMC_Testrun")
    rpm_sensor = RPMInput(RPM_PIN, PULSES_PER_REV, RPM_AVG_WINDOW_S)
    gps = GPS_L76K()
    display = OLEDDisplay()

    # 2. Start
    rpm_sensor.start()
    gps.start()
    
    print("🔴 Logging läuft mit 10Hz... (STRG+C zum Stoppen)")

    try:
        while True:
            loop_start = time.time()
            
            # Daten sammeln
            rpm_val = rpm_sensor.get_data().rpm
            gps_data = gps.get_data()
            ts = datetime.utcnow().isoformat()

            # 3. Loggen (10Hz)
            logger.log(
                ts=ts, 
                rpm=rpm_val, 
                speed=gps_data.speed_kmh, 
                lat=gps_data.lat, 
                lon=gps_data.lon, 
                afr=14.7, # Dummy für jetzt
                note="1:50_Mix"
            )

            # 4. Konsolen-Feedback (nur alle 1 Sekunde, um Terminal nicht zu fluten)
            if int(loop_start * 10) % 10 == 0:
                print(f"[{ts}] RPM: {rpm_val} | GPS: {gps_data.speed_kmh} km/h | Fix: {gps_data.fix}")

            # Timing für 10Hz
            elapsed = time.time() - loop_start
            time.sleep(max(0, 0.1 - elapsed))

    except KeyboardInterrupt:
        print("\nStopping Logger...")
    finally:
        rpm_sensor.stop()
        gps.stop()
        print("✅ Log gespeichert im Ordner /logs/")

if __name__ == "__main__":
    main()