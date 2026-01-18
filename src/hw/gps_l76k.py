import time

class GPSData:
    def __init__(self, fix=False, lat=0.0, lon=0.0, speed_kmh=0.0, sats=0):
        self.fix = fix
        self.lat = lat
        self.lon = lon
        self.speed_kmh = speed_kmh
        self.sats = sats

class GPS_L76K:
    def __init__(self):
        self.running = False
        # Hier würde normalerweise die I2C/Serial Initialisierung stehen
        print("🛰 GPS L76K initialisiert (Simulation aktiv)")

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def get_data(self) -> GPSData:
        if self.running:
            # Im Codespace simulieren wir eine Fahrt mit 80 km/h (dein Test-Bereich)
            return GPSData(fix=True, lat=48.123, lon=11.456, speed_kmh=80.0, sats=8)
        return GPSData()