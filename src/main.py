import time
import serial
from config import *
from hw.gps_l76k import GPS_L76K
from hw.display_oled import OLEDDisplay
from data.logger import CSVLogger

def main():
    display = OLEDDisplay()
    gps = GPS_L76K()
    gps.start()
    
    # Serial Setup
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.05)
        print(f"[OK] Lese von {SERIAL_PORT}...")
    except Exception as e:
        print(f"[X] Fehler: {e}")
        ser = None

    # Initialwerte
    rpm_val, afr_val, egt_val = 0.0, 14.7, 0.0
    logging_active = False

    while True:
        loop_start = time.time()

        # 1. DATEN VOM ARDUINO LESEN
        if ser and ser.in_waiting > 0:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if ";" in line:
                    parts = line.split(';')
                    if len(parts) >= 3:
                        # EXPLIZITE ZUWEISUNG - Wir erzwingen Float-Wandlung
                        rpm_val = float(parts[0])
                        afr_val = float(parts[1])
                        egt_val = float(parts[2])
            except (ValueError, IndexError):
                pass # Fehlerhafte Zeilen ignorieren

        # 2. GPS DATEN HOLEN
        gps_data = gps.get_data()

        # 3. DISPLAY AKTUALISIEREN
        # WICHTIG: Prüfe hier die REIHENFOLGE! 
        # rpm_val MUSS an erster Stelle stehen.
        display.show_status(
            rpm=rpm_val, 
            speed=gps_data.speed_kmh, 
            afr=afr_val, 
            egt=egt_val, 
            info=VEHICLE_NAME, 
            gps_fix=gps_data.fix, 
            is_logging=logging_active
        )

        # Timing für ca. 10-20Hz
        time.sleep(0.05)

if __name__ == "__main__":
    main()