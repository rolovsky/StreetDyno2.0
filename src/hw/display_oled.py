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
                
                # --- SCHRIFTEN LADEN ---
                # Wir laden eine große Schrift für die Zahlen und eine kleine für Labels
                try:
                    # Pfad zu einer Standard-Schrift auf dem Pi
                    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    self.font_big = ImageFont.truetype(font_path, 34)   # RIESIGE Zahlen
                    self.font_medium = ImageFont.truetype(font_path, 18) # Mittel für Labels
                    self.font_small = ImageFont.truetype(font_path, 10)  # Statuszeile
                except:
                    print("[!] Wunsch-Schrift nicht gefunden, nutze Standard.")
                    self.font_big = ImageFont.load_default()
                    self.font_medium = ImageFont.load_default()
                    self.font_small = ImageFont.load_default()

                print("[OK] OLED Hardware & Fonts geladen.")
            except Exception as e:
                print(f"[X] OLED Fehler: {e}")

    def set_mode(self, new_mode):
        self.mode = new_mode

    def show_status(self, rpm, speed, afr, info, gps_fix):
        if not self.device: return
            
        with canvas(self.device) as draw:
            # --- Fokus Bereich ---
            if self.mode == "RPM":
                draw.text((0, 0), "RPM", font=self.font_small, fill="white")
                # Große Zahl mittig platzieren
                draw.text((10, 8), f"{int(rpm)}", font=self.font_big, fill="white")
            
            elif self.mode == "SPEED":
                draw.text((0, 0), "KM/H", font=self.font_small, fill="white")
                draw.text((10, 8), f"{speed:.1f}", font=self.font_big, fill="white")
            
            elif self.mode == "AFR":
                draw.text((0, 0), "AFR", font=self.font_small, fill="white")
                draw.text((10, 8), f"{afr:.2f}", font=self.font_big, fill="white")

            # --- Untere Statusleiste ---
            draw.line((0, 48, 128, 48), fill="white")
            fix_icon = "GPS: OK" if gps_fix else "NO GPS"
            draw.text((0, 52), f"{fix_icon} | {info}", font=self.font_small, fill="white")