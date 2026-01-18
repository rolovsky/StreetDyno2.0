import serial

class GPSData:
    def __init__(self, fix=False, speed_kmh=0.0, sats=0):
        self.fix = fix
        self.speed_kmh = speed_kmh
        self.sats = sats

class GPS_L76K:
    def __init__(self, port='/dev/ttyS0', baudrate=9600):
        try:
            self.ser = serial.Serial(port, baudrate, timeout=0.1)
            print(f"🛰 GPS UART aktiv auf {port}")
        except:
            self.ser = None
            print("⚠️ GPS UART nicht gefunden!")

    def start(self): pass
    def stop(self):
        if self.ser: self.ser.close()

    def get_data(self):
        if not self.ser: return GPSData()
        try:
            # Wir lesen nur, wenn Daten im Puffer sind, um 10Hz nicht zu blockieren
            if self.ser.in_waiting > 0:
                line = self.ser.readline().decode('ascii', errors='replace')
                if "RMC" in line: # Recommended Minimum Navigation Information
                    parts = line.split(',')
                    if len(parts) > 7 and parts[2] == 'A':
                        speed_knots = float(parts[7]) if parts[7] else 0.0
                        return GPSData(fix=True, speed_kmh=round(speed_knots * 1.852, 1))
        except: pass
        return GPSData()