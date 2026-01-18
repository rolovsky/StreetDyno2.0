import time

# Mock-Check für Codespaces/PC
try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

class RPMData:
    def __init__(self, rpm: float):
        self.rpm = rpm

class RPMInput:  # <--- Dieser Name MUSS exakt so hier stehen!
    def __init__(self, pin: int, pulses_per_rev: int, window_s: float):
        self.pin = pin
        self.ppr = pulses_per_rev
        self.window_s = window_s
        self.last_rpm = 0

    def start(self):
        if IS_PI:
            print(f"[OK] [Hardware] RPM an Pin {self.pin} gestartet.")
        else:
            print(f"[Mock] RPM Simulation gestartet.")

    def stop(self):
        pass

    def get_data(self) -> RPMData:
        if IS_PI:
            # Hier käme die echte Logik
            return RPMData(rpm=0.0)
        else:
            # Im Codespace simulieren wir 1250 RPM für den VMC 177
            return RPMData(rpm=1250.0)