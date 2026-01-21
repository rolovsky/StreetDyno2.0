import time  # <--- Diese Zeile hat gefehlt!
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import sh1106

try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

class OLEDDisplay:
    def __init__(self):
        self.device = None
        if IS_PI:
            try:
                # SPI Setup für Waveshare HAT
                self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
                self.device = sh1106(self.serial)
                print("[OK] OLED SPI Hardware gestartet.")
            except Exception as e:
                print(f"[X] OLED Hardware Fehler: {e}")
        else:
            print("[MOCK] OLED Simulation aktiv.")

    def show_status(self, rpm, speed, afr, info, gps_fix):
        if not self.device:
            # Im Mock-Modus geben wir die Info einfach in die Konsole
            if int(time.time() * 10) % 20 == 0: # Alle 2 Sek
                print(f"[DISPLAY-MOCK] RPM: {rpm} | Speed: {speed}")
            return
            
        with canvas(self.device) as draw:
            draw.text((5, 2),   f"RPM:   {int(rpm)}", fill="white")
            draw.text((5, 18),  f"SPEED: {speed} km/h", fill="white")
            draw.text((5, 34),  f"AFR:   {afr}", fill="white")
            status = "FIX" if gps_fix else "SEARCH..."
            draw.text((5, 50),  f"GPS: {status} | {info}", fill="white")

    def clear(self):
        if self.device:
            self.device.clear()