import serial
import time

class GPSData:
    def __init__(self, fix=False, speed_kmh=0.0, sats=0):
        self.fix = fix
        self.speed_kmh = speed_kmh
        self.sats = sats

class GPS_L76K:
    def __init__(self, port='/dev/ttyS0', baudrate=9600):
        self.ser = None
        try:
            # Verbindung herstellen
            self.ser = serial.Serial(port, baudrate, timeout=0.1)
            self.ser.flushInput()
            print(f"🛰 GPS UART aktiv auf {port}")
        except Exception as e:
            print(f"⚠️ GPS UART Fehler: {e}")

    def start(self):
        pass

    def stop(self):
        if self.ser:
            self.ser.close()

    def get_data(self):
        if not self.ser:
            return GPSData()
        
        try:
            if self.ser.in_waiting > 0:
                line = self.ser.readline().decode('ascii', errors='replace').strip()
                
                # Diagnose: Antennen-Warnung
                if "ANTENNA OPEN" in line:
                    # Wir geben trotzdem ein Objekt zurück, damit das Programm nicht abstürzt
                    return GPSData(fix=False, speed_kmh=0.0, sats=0)

                # Parsing der RMC-Sätze (Geschwindigkeit)
                if "RMC" in line:
                    parts = line.split(',')
                    # $GNRMC,123456.00,A,LAT,N,LON,E,SPEED_KNOTS,...
                    if len(parts) > 7 and parts[2] == 'A':
                        speed_knots = float(parts[7]) if parts[7] else 0.0
                        return GPSData(fix=True, speed_kmh=round(speed_knots * 1.852, 1), sats=8)
        except Exception as e:
            # Falls ein Byte-Fehler auftritt, einfach weitermachen
            pass
            
        return GPSData()