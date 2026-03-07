import csv
import os
from datetime import datetime

class CSVLogger:
    def __init__(self, filename_prefix="dyno"):
        if not os.path.exists('logs'): os.makedirs('logs')
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filepath = f"logs/{filename_prefix}_{ts}.csv"
        self._write_header()
        print(f"Log start: {self.filepath}")

    def _write_header(self):
        header = ["timestamp", "rpm", "speed_kmh", "lat", "lon", "afr", "egt", "note"]
        with open(self.filepath, 'w', newline='') as f:
            csv.writer(f).writerow(header)

    def log(self, ts, rpm, speed, lat, lon, afr, egt, note=""):
        with open(self.filepath, 'a', newline='') as f:
            csv.writer(f).writerow([ts, rpm, speed, lat, lon, afr, egt, note])