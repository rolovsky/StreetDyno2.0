import serial
import os
import time

print("🛑 Stoppe gpsd, um den Port freizumachen...")
os.system("sudo systemctl stop gpsd.socket")
os.system("sudo systemctl stop gpsd")
time.sleep(1)

port = "/dev/serial0" # Standard-Port für Raspberry Pi HATs

try:
    # 1. Verbinden mit Werks-Einstellungen (9600 Baud)
    ser = serial.Serial(port, 9600, timeout=1)
    print("🚀 Setze Baudrate des GPS-Chips auf 115200...")
    ser.write(b'$PCAS01,5*19\r\n')
    time.sleep(0.5)
    ser.close()

    # 2. Neu verbinden mit der neuen Hochgeschwindigkeits-Baudrate
    ser = serial.Serial(port, 115200, timeout=1)
    
    print("⚡ Aktiviere 5Hz (200ms) Update-Rate...")
    ser.write(b'$PCAS02,200*1D\r\n')
    time.sleep(0.5)
    
    # 3. Einstellungen im Flash-Speicher des Moduls speichern
    print("💾 Speichere Konfiguration im GPS-Modul...")
    ser.write(b'$PCAS00*01\r\n')
    time.sleep(0.5)
    
    ser.close()
    print("✅ [BULLSEYE] GPS läuft jetzt auf 5Hz & 115200 Baud!")

except Exception as e:
    print(f"❌ Fehler: {e}")

print("🔄 Starte gpsd wieder...")
os.system("sudo systemctl start gpsd.socket")
os.system("sudo systemctl start gpsd")
print("🏁 Fertig! gpsd findet die neue Baudrate automatisch.")