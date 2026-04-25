import serial
import time
import os
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

GPS_WAKE_PIN = 4
PORT = "/dev/ttyAMA0"

print("🎯 Starte Operation 'Sniper': Software-Reset im Boot-Fenster...")

# Befehle vorbereiten (Wichtig: Das L76X braucht zwingend \r\n am Ende!)
cmd_1hz = b'$PMTK220,1000*1F\r\n'   # Update-Rate auf 1Hz drosseln
cmd_9600 = b'$PMTK251,9600*17\r\n'  # Baudrate auf 9600 zurücksetzen

# 1. Modul in den Tiefschlaf schicken (Reset)
if HAS_GPIO:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPS_WAKE_PIN, GPIO.OUT)
    GPIO.output(GPS_WAKE_PIN, GPIO.LOW)
else:
    os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dl")

time.sleep(2) # Kondensatoren entladen lassen

try:
    # 2. Port auf der "Crash-Baudrate" öffnen
    ser = serial.Serial(PORT, 115200, timeout=0.1)
    
    print("⚡ Wecke Modul und feuere PMTK-Befehle...")
    # 3. Modul aufwecken
    if HAS_GPIO:
        GPIO.output(GPS_WAKE_PIN, GPIO.HIGH)
    else:
        os.system(f"sudo pinctrl set {GPS_WAKE_PIN} op dh")
        
    # 4. Dauerfeuer! Genau in das Boot-Zeitfenster hinein.
    end_time = time.time() + 2.5
    while time.time() < end_time:
        ser.write(cmd_1hz)
        time.sleep(0.05)
        ser.write(cmd_9600)
        time.sleep(0.05)
        
    print("✅ Dauerfeuer beendet. Das Modul sollte jetzt auf 9600 Baud laufen.")
    ser.close()
    
except Exception as e:
    print(f"❌ Serieller Fehler: {e}")

if HAS_GPIO:
    GPIO.cleanup()
