import time
from collections import deque

# Versuch, RPi.GPIO zu laden. Falls es fehlschlägt (z.B. in Codespaces), 
# nutzen wir einen Dummy-Modus.
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False
    print("⚠️ RPi.GPIO nicht gefunden oder kein Pi erkannt. Starte im Simulationsmodus.")

class RPMData:
    def __init__(self, rpm: float):
        self.rpm = rpm

class RPMInput:
    def __init__(self, pin: int, pulses_per_rev: int, window_s: float):
        self.pin = pin
        self.ppr = pulses_per_rev
        self.window_s = window_s
        self.timestamps = deque()
        self.last_rpm = 0

    def _callback(self, channel):
        self.timestamps.append(time.time())

    def start(self):
        if IS_PI:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(self.pin, GPIO.FALLING, callback=self._callback)
        else:
            print(f"🛠 [MOCK] RPM Input an Pin {self.pin} gestartet.")

    def stop(self):
        if IS_PI:
            GPIO.remove_event_detect(self.pin)
        else:
            print("🛠 [MOCK] RPM Input gestoppt.")

    def get_data(self) -> RPMData:
        if not IS_PI:
            # Simulation: Wir geben einfach 1500 RPM zurück, damit der Rest vom Code läuft
            return RPMData(rpm=1500.0)

        now = time.time()
        while self.timestamps and (now - self.timestamps[0] > self.window_s):
            self.timestamps.popleft()
        
        count = len(self.timestamps)
        if count < 2:
            self.last_rpm = 0
        else:
            self.last_rpm = (count / self.window_s) * 60 / self.ppr
        
        return RPMData(rpm=round(self.last_rpm, 0))