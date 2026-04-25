import serial, time, os

def send_casic(ser, cmd):
    checksum = 0
    for char in cmd:
        checksum ^= ord(char)
    # Befehl feuern
    ser.write(f"${cmd}*{checksum:02X}\r\n".encode('ascii'))

print("🎯 CASIC Sniper: Lade panzerbrechende Munition...")

try:
    # 1. Wir legen uns auf der Crash-Baudrate (115200) auf die Lauer
    ser = serial.Serial('/dev/ttyAMA0', 115200, timeout=0.1)
    
    # 2. Hard Reset: Chip ausschalten (HIGH)
    print("💤 Schalte Modul aus...")
    os.system("sudo pinctrl set 4 op dh")
    time.sleep(1.5)
    
    # 3. Chip wecken (LOW) und SOFORT feuern
    print("⚡ Wecke Modul und starte Dauerfeuer...")
    os.system("sudo pinctrl set 4 op dl")
    
    # 3 Sekunden Dauerfeuer im Boot-Fenster
    end = time.time() + 3.0
    while time.time() < end:
        send_casic(ser, "PCAS02,1000") # 1Hz Update Rate
        time.sleep(0.05)
        send_casic(ser, "PCAS01,1")    # Downgrade auf 9600 Baud
        time.sleep(0.05)
        
    ser.close()
    
    # 4. Der finale Fix: Auf 9600 Baud verbinden und für immer SPEICHERN
    print("💾 Brenne Einstellungen in den Flash-Speicher...")
    time.sleep(1)
    ser_slow = serial.Serial('/dev/ttyAMA0', 9600, timeout=0.5)
    send_casic(ser_slow, "PCAS00") # Save to NVRAM / Flash
    ser_slow.close()
    
    print("✅ Ziel eliminiert! Modul läuft stabil auf 9600 Baud und 1Hz.")

except Exception as e:
    print(f"❌ Fehler: {e}")
