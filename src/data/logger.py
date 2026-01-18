import csv
import os
from datetime import datetime

class CSVLogger:
    def __init__(self, filename_prefix="dyno_log"):
        # Erstellt den Ordner 'logs', falls er nicht existiert
        if not os.path.exists('logs'):
            os.makedirs('logs')
            
        # Generiert einen Dateinamen mit Zeitstempel (z.B. logs/dyno_log_20231027_1230.csv)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filepath = f"logs/{filename_prefix}_{timestamp}.csv"
        
        # Header schreiben
        self._write_header()
        print(f"📄 Log-Datei erstellt: {self.filepath}")

    def _write_header(self):
        header = ["timestamp", "rpm", "speed_kmh", "lat", "lon", "afr", "note"]
        with open(self.filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)

    def log(self, ts, rpm, speed, lat, lon, afr, note=""):
        with open(self.filepath, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([ts, rpm, speed, lat, lon, afr, note])