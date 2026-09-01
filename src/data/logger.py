import os
import time

class CSVLogger:
    def __init__(self, log_dir="/home/rolovsky/streetdyno2.0/logs"):
        self.log_dir = log_dir
        self.filepath = None
        self.is_logging = False

        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def start(self, filepath=None):
        if filepath is not None:
            self.filepath = filepath
        elif getattr(self, "filename", None) is not None:
            self.filepath = self.filename
        else:
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            self.filepath = os.path.join(self.log_dir, f"dyno_log_{timestamp}.csv")
            
        with open(self.filepath, "w") as f:
            # NEU: Lat, Lon und Alt im Header hinzugefügt
            f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
            
        self.is_logging = True
        print(f"\n[LOGGER] Aufzeichnung gestartet: {self.filepath}")

    def stop(self):
        self.is_logging = False
        print("\n[LOGGER] Aufzeichnung gestoppt.")

    def log(self, rpm, afr, egt, speed, lat=0.0, lon=0.0, alt=0.0, fix=False):
        if self.is_logging and self.filepath:
            timestamp = time.strftime("%H:%M:%S")
            with open(self.filepath, "a") as f:
                f.write(f"{timestamp},{rpm:.0f},{afr:.2f},{egt:.1f},{speed:.1f},{lat:.6f},{lon:.6f},{alt:.1f},{fix}\n")