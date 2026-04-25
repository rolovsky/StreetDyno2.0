import serial
import time
import os
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

GPS_WAKE_PIN = 4
GPS_PORT = "/dev/ttyAMA0"

print("🚀 Starte GPS-Scanner und Hardware-Reset...")

# 1. HARD RESET DES MODULS
if HAS_GPIO:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPS_WAKE_PIN, GPIO.OUT)
    print("💤 Schalte GPS aus...")
    GPIO.output(GPS_WAKE_PIN, GPIO.LOW)
    time.sleep(2)
    print("⚡ Wecke GPS auf...")
    GPIO.output(GPS_WAKE_PIN, GPIO.HIGH)
    time.sleep(2)
else:
    print("⚠️ RPi.GPIO fehlt, nutze pinctrl für Reset...")
    os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dl")
    time.sleep(2)
    os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dh")
    time.sleep(2)

# 2. BAUDRATEN-SCANNER
baudrates = [9600, 115200, 38400, 57600, 4800, 19200]
gefunden = False

for baud in baudrates:
    print(f"\n🔍 Prüfe Baudrate: {baud}...")
    try:
        os.system(f"sudo stty -F {GPS_PORT} {baud} raw -echo")
        ser = serial.Serial(GPS_PORT, baud, timeout=1.5)
        
        # Lese 5 Zeilen, um sicherzugehen
        for _ in range(5):
            if ser.in_waiting > 0:
                # errors='ignore' verhindert Absturz bei Müll-Daten
                line = ser.readline().decode('ascii', errors='ignore').strip()
                
                # Ist es gültiges NMEA?
                if line.startswith('$GP') or line.startswith('$GN') or line.startswith('$GL'):
                    print(f"✅ BINGO! Gültige Daten bei {baud} Baud gefunden:")
                    print(f"   -> {line}")
                    gefunden = True
                    break
        ser.close()
        
        if gefunden:
            break
            
    except Exception as e:
        print(f"❌ Fehler bei {baud}: {e}")

if not gefunden:
    print("\n💀 Scanner beendet. Kein lesbarer NMEA-Text gefunden. Modul tot oder falsch verkabelt?")
else:
    print(f"\n🎯 Das GPS funkt stabil auf {baud} Baud. Merke dir diesen Wert für die main.py!")

if HAS_GPIO:
    GPIO.cleanup()
