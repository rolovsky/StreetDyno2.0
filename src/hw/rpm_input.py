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
        self.timestamps = deque()  # Speichert die Zeitpunkte der Impulse
        self.last_rpm = 0.0

    def start(self):
        if IS_PI:
            GPIO.setmode(GPIO.BCM)
            # Setup für PC817 Optokoppler: Pull-Up Widerstand aktivieren
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Interrupt bei fallender Flanke (Active Low des Optokopplers)
            GPIO.add_event_detect(self.pin, GPIO.FALLING, callback=self._callback)
            print(f"[OK] [Hardware] RPM an Pin {self.pin} (Optokoppler-Modus) gestartet.")
        else:
            print(f"[Mock] RPM Simulation gestartet.")

    def _callback(self, channel):
        # Wird bei jedem Zündimpuls aufgerufen
        self.timestamps.append(time.time())

    def stop(self):
        if IS_PI:
            GPIO.remove_event_detect(self.pin)

    def get_data(self) -> RPMData:
        if not IS_PI:
            # Im Codespace simulieren wir 1250 RPM für den VMC 177
            return RPMData(rpm=1250.0)

        now = time.time()
        # Entferne alte Impulse, die außerhalb des Zeitfensters liegen
        while self.timestamps and (now - self.timestamps[0] > self.window_s):
            self.timestamps.popleft()

        count = len(self.timestamps)
        if count < 2:
            self.last_rpm = 0.0
            return RPMData(rpm=0.0)

        # Berechnung: (Impulse / Fensterzeit) * 60 / Impulse_pro_Umdrehung
        # Wir nutzen die Zeitspanne zwischen erstem und letztem Impuls für höhere Genauigkeit
        time_span = self.timestamps[-1] - self.timestamps[0]
        
        if time_span > 0:
            # (Anzahl Impulse - 1) Intervalle innerhalb der Zeitspanne
            revs_per_second = (count - 1) / time_span / self.ppr
            self.last_rpm = revs_per_second * 60.0
        else:
            self.last_rpm = 0.0

        return RPMData(rpm=self.last_rpm)
    
    