import time
import threading
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

class RPMInput:
    def __init__(self, pin, ppr):
        self.pin = pin
        self.ppr = ppr
        self.rpm = 0
        self._pulses = 0
        self._running = False
        if GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def _count(self, channel):
        self._pulses += 1

    def start(self):
        if not GPIO: return
        self._running = True
        GPIO.add_event_detect(self.pin, GPIO.FALLING, callback=self._count)
        threading.Thread(target=self._logic, daemon=True).start()

    def _logic(self):
        while self._running:
            self._pulses = 0
            time.sleep(0.2)
            self.rpm = (self._pulses * 5) * 60 / self.ppr

    def get_rpm(self):
        return self.rpm if GPIO else 1500 # Dummy für Codespaces