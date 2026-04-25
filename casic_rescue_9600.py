import serial, time, os

def send_casic(ser, cmd):
    checksum = 0
    for char in cmd:
        checksum ^= ord(char)
    # Befehl feuern
    ser.write(f"${cmd}*{checksum:02X}\r\n".encode('ascii'))

print("🎯 CASIC Sniper V2: 9600-Baud Nahkampf...")

try:
    # Wir lauern jetzt auf 9600 Baud!
    ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=0.1)
    
    # 1. Hard Reset: Ausschalten
    print("💤 Schalte Modul aus...")
    os.system("sudo pinctrl set 4 op dh")
    time.sleep(1.5)
    
    # 2. Wecken und SOFORT blind feuern
    print("⚡ Wecke Modul und blockiere den Absturz...")
    os.system("sudo pinctrl set 4 op dl")
    
    # 2.5 Sekunden Dauerfeuer im 9600-Boot-Fenster
    end = time.time() + 2.5
    while time.time() < end:
        send_casic(ser, "PCAS02,1000") # Zwinge auf 1Hz
        send_casic(ser, "PCAS01,1")    # Zwinge auf 9600 Baud
        send_casic(ser, "PCAS00")      # SPEICHERN!
        time.sleep(0.02)
        
    ser.close()
    print("✅ Dauerfeuer beendet. Das NVRAM sollte jetzt überschrieben sein.")

except Exception as e:
    print(f"❌ Fehler: {e}")
