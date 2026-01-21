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
        self.mode = "RPM"
        
        if IS_PI:
            try:
                self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
                self.device = sh1106(self.serial)
                
                # Schriften laden
                try:
                    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    self.font_big = ImageFont.truetype(font_path, 34)
                    self.font_small = ImageFont.truetype(font_path, 10)
                except:
                    self.font_big = ImageFont.load_default()
                    self.font_small = ImageFont.load_default()

                print("[OK] OLED Hardware & Fonts geladen.")
            except Exception as e:
                print(f"[X] OLED Fehler: {e}")

    def set_mode(self, new_mode):
        self.mode = new_mode

    def clear(self):
        """Löscht das Display beim Beenden."""
        if self.device:
            self.device.clear()

    def show_status(self, rpm, speed, afr, info, gps_fix, is_logging=False):
        if not self.device: return
            
        with canvas(self.device) as draw:
            # Modus-Label und Wert bestimmen
            if self.mode == "RPM":
                label, val_str = "RPM", f"{int(rpm)}"
            elif self.mode == "SPEED":
                label, val_str = "KM/H", f"{speed:.1f}"
            else:
                label, val_str = "AFR", f"{afr:.2f}"

            # Label oben links
            draw.text((0, 0), label, font=self.font_small, fill="white")
            
            # Recording-Indikator oben rechts (Punkt + Text)
            if is_logging:
                draw.ellipse((115, 2, 125, 12), fill="white")
                draw.text((90, 2), "REC", font=self.font_small, fill="white")

            # Große Zahl (X=20 für etwas Einrückung)
            draw.text((20, 8), val_str, font=self.font_big, fill="white")

            # Untere Statusleiste
            draw.line((0, 48, 128, 48), fill="white")
            fix_icon = "GPS: OK" if gps_fix else "NO GPS"
            draw.text((0, 52), f"{fix_icon} | {info}", font=self.font_small, fill="white")