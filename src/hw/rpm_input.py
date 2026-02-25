import time
from collections import deque

# Mock-Check für Codespaces/PC
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

class RPMData:
    def __init__(self, rpm: float):
        self.rpm = rpm

class RPMInput:
    def __init__(self, pin: int, pulses_per_rev: int, window_s: float):
        self.pin = pin
        self.ppr = pulses_per_rev
        self.window_s = window_s
        self.timestamps = deque()
        self.last_rpm = 0.0

    def start(self):
        if IS_PI:
            # WICHTIG: Kein setmode hier! Das macht die main.py zentral.
            # Pin per Pull-Down auf 0V ziehen, um Störungen abzusaugen
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            # Auf steigende Flanke der SIP Blackbox reagieren (inkl. 5ms Entprellung)
            GPIO.add_event_detect(self.pin, GPIO.RISING, callback=self._callback, bouncetime=5)
            print(f"[OK] [Hardware] RPM-Eingang an Pin {self.pin} aktiviert (Pull-Down / Rising).")
        else:
            print(f"[Mock] RPM Simulation aktiv.")

    def _callback(self, channel):
        self.timestamps.append(time.time())

    def stop(self):
        if IS_PI:
            try:
                GPIO.remove_event_detect(self.pin)
            except:
                pass

    def get_data(self) -> RPMData:
        if not IS_PI:
            return RPMData(rpm=1250.0)

        now = time.time()
        while self.timestamps and (now - self.timestamps[0] > self.window_s):
            self.timestamps.popleft()

        count = len(self.timestamps)
        if count < 2:
            return RPMData(rpm=0.0)

        time_span = self.timestamps[-1] - self.timestamps[0]
        if time_span > 0:
            revs_per_second = (count - 1) / time_span / self.ppr
            self.last_rpm = revs_per_second * 60.0
        else:
            self.last_rpm = 0.0

        return RPMData(rpm=self.last_rpm)