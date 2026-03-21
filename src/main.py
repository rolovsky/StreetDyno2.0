import time
import serial
import sys
from hw.gps_l76k import GPS_L76K
from data.logger import CSVLogger

# ==========================================
# --- KONFIGURATION STREETDYNO 2.0 ---
# ==========================================
ARDUINO_PORT = '/dev/ttyUSB0'  # Anpassen, falls dein Nano auf ACM0 liegt
ARDUINO_BAUD = 500000          # High-Speed Baudrate für 10Hz

# Die Dyno-Automatik
AUTO_START_RPM = 2500
AUTO_STOP_RPM = 2000
MIN_SPEED_KMH = 30.0
# ==========================================

def main():
    print("🚀 [SYSTEM] Starte StreetDyno 2.0 Kommandozentrale...")

    # 1. GPS Modul hochfahren
    gps = GPS_L76K()
    gps.start()
    print("✅ [GPS] Modul lauscht auf 5Hz Satelliten-Daten...")

    # 2. Daten-Logger scharfschalten
    logger = CSVLogger()

    # 3. Serielle Brücke zum Arduino aufbauen
    try:
        ser = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
        print(f"✅ [ARDUINO] Verbunden auf {ARDUINO_PORT}.")
    except Exception as e:
        print(f"❌ [FEHLER] Arduino nicht gefunden: {e}")
        sys.exit(1)

    print("\n🏁 --- SYSTEM BEREIT ---")
    print(f"Fahre los! Auto-Record startet im 3. Gang (> {MIN_SPEED_KMH} km/h & > {AUTO_START_RPM} RPM).\n")

    try:
        while True:
            # --- A. ARDUINO DATEN SAUGEN ---
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                
                try:
                    parts = line.split(';')
                    if len(parts) == 3:
                        rpm_val = float(parts[0])
                        afr_val = float(parts[1])
                        egt_val = float(parts[2])
                    else:
                        continue
                except ValueError:
                    continue # Datenmüll beim Startvorgang ignorieren

                # --- B. GPS DATEN KOPPELN ---
                current_gps_data = gps._data # Greift auf die Daten deiner gps_l76k.py zu
                current_speed = current_gps_data.speed_kmh if current_gps_data else 0.0
                current_fix = current_gps_data.fix if current_gps_data else False
                current_lat = current_gps_data.lat if current_gps_data and current_gps_data.lat else 0.0
                current_lon = current_gps_data.lon if current_gps_data and current_gps_data.lon else 0.0

                # --- C. LIVE TERMINAL OUTPUT ---
                status = "🔴 REC" if logger.is_logging else "🟢 IDLE"
                sys.stdout.write(f"\r[{status}] RPM: {rpm_val:5.0f} | AFR: {afr_val:5.2f} | EGT: {egt_val:5.1f}°C | Speed: {current_speed:5.1f} km/h | Fix: {current_fix}   ")
                sys.stdout.flush()

                # --- D. DIE DYNO AUTO-RECORD LOGIK ---
                if not logger.is_logging:
                    # Start-Bedingung checken
                    if rpm_val > AUTO_START_RPM and current_speed > MIN_SPEED_KMH:
                        print("\n\n🔥 [AUTO-DYNO] Schwellenwerte erreicht! Starte Vollgas-Aufzeichnung...")
                        logger.start()
                else:
                    # Stopp-Bedingung checken
                    if rpm_val < AUTO_STOP_RPM:
                        print("\n🛑 [AUTO-DYNO] Drehzahl gefallen. Pull beendet, Log gespeichert.")
                        logger.stop()

                # --- E. DATEN IN DIE CSV HÄMMERN ---
                if logger.is_logging:
                    logger.log(rpm_val, afr_val, egt_val, current_speed, current_lat, current_lon, current_fix)

            time.sleep(0.005) # Verhindert, dass die CPU des Pi kocht

    except KeyboardInterrupt:
        print("\n\n🛑 [SYSTEM] Not-Halt eingeleitet...")
        if logger.is_logging:
            logger.stop()
        gps.stop()
        ser.close()
        print("✅ [SYSTEM] Hardware sicher entkoppelt.")

if __name__ == '__main__':
    main()