import serial, time

def send_casic(ser, cmd):
    checksum = 0
    for char in cmd.encode('ascii'):
        checksum ^= char
    full_cmd = f"${cmd}*{checksum:02X}\r\n".encode('ascii')
    print(f"Sende: {full_cmd.strip()}")
    ser.write(full_cmd)
    time.sleep(0.5)

try:
    # Wir docken bei seiner aktuellen 115200-Geschwindigkeit an
    ser = serial.Serial('/dev/ttyAMA0', 115200, timeout=1)
    print("💉 Setze L76K-spezifische Narkose...")
    
    # 1. Befehl: 1Hz Update Rate
    send_casic(ser, "PCAS02,1000")
    
    # 2. Befehl: 9600 Baudrate
    send_casic(ser, "PCAS01,1")
    
    ser.close()
    print("✅ Chip erfolgreich auf 9600 Baud / 1Hz gedrosselt!")
except Exception as e:
    print(f"❌ Fehler: {e}")
