import time
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont

try:
    import RPi.GPIO as GPIO
    IS_PI = True
except (ImportError, RuntimeError):
    IS_PI = False

class OLEDDisplay:
    def __init__(self):
        self.device = None
        self.mode = "RPM"  # Startmodus
        if IS_PI:
            try:
                # SPI Setup für Waveshare HAT
                self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
                self.device = sh1106(self.serial)
                # Standard-Fonts laden
                self.font_main = ImageFont.load_default() 
                print("[OK] OLED SPI Hardware gestartet.")
            except Exception as e:
                print(f"[X] OLED Hardware Fehler: {e}")
        else:
            print("🛠 [MOCK] OLED Simulation aktiv.")

    def set_mode(self, new_mode):
        """Wechselt den Fokus-Modus der Anzeige."""
        self.mode = new_mode

    def show_status(self, rpm, speed, afr, info, gps_fix):
        if not self.device:
            if int(time.time() * 10) % 20 == 0:
                print(f"[DISPLAY-MOCK] Mode: {self.mode} | RPM: {rpm} | Speed: {speed}")
            return
            
        with canvas(self.device) as draw:
            # --- Fokus Bereich (Oben) ---
            if self.mode == "RPM":
                draw.text((0, 0), "DREHZAHL", fill="white")
                draw.text((10, 12), f"{int(rpm)}", fill="white") # Hier ggf. Font-Größe anpassen
                draw.text((90, 25), "U/min", fill="white")
            elif self.mode == "SPEED":
                draw.text((0, 0), "SPEED", fill="white")
                draw.text((10, 12), f"{speed:.1f}", fill="white")
                draw.text((90, 25), "km/h", fill="white")
            elif self.mode == "AFR":
                draw.text((0, 0), "AIR/FUEL", fill="white")
                draw.text((10, 12), f"{afr:.2f}", fill="white")
                draw.text((90, 25), "Ratio", fill="white")

            # --- Minimalistische Trennlinie ---
            draw.line((0, 48, 128, 48), fill="white")

            # --- Status Bereich (Unten klein) ---
            fix_icon = "GPS: OK" if gps_fix else "GPS: SEARCH"
            draw.text((0, 52), f"{fix_icon} | {info}", fill="white")

    def clear(self):
        if self.device:
            self.device.clear()