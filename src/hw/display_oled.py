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
        self.mode = "RPM"  # Standard-Startmodus
        
        if IS_PI:
            try:
                # SPI Setup: DC an Pin 18 (GPIO 24), RST an Pin 22 (GPIO 25)
                self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
                self.device = sh1106(self.serial)
                
                # Fonts laden
                try:
                    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    self.font_big = ImageFont.truetype(font_path, 34)
                    self.font_small = ImageFont.truetype(font_path, 10)
                except:
                    print("[!] Fonts nicht gefunden, nutze Default.")
                    self.font_big = ImageFont.load_default()
                    self.font_small = ImageFont.load_default()

                print("[OK] OLED Hardware & Fonts geladen.")
            except Exception as e:
                print(f"[X] OLED Initialisierungs-Fehler: {e}")

    def set_mode(self, new_mode):
        """Wechselt zwischen RPM, SPEED, AFR und EGT"""
        self.mode = new_mode

    def clear(self):
        if self.device:
            self.device.clear()

    def show_status(self, rpm, speed, afr, egt, info, gps_fix, is_logging=False):
        """Hauptmethode zum Zeichnen des Displays"""
        if not self.device:
            return
            
        with canvas(self.device) as draw:
            # --- 1. DATEN-AUFBEREITUNG ---
            # Wir casten alles sicherheitshalber auf float, bevor wir formatieren
            try:
                rpm_val = int(float(rpm))
                speed_val = float(speed)
                afr_val = float(afr)
                egt_val = int(float(egt))
            except (ValueError, TypeError):
                rpm_val, speed_val, afr_val, egt_val = 0, 0.0, 0.0, 0

            # --- 2. MODUS-LOGIK ---
            if self.mode == "RPM":
                label = "RPM"
                val_str = f"{rpm_val}"
            elif self.mode == "SPEED":
                label = "KM/H"
                val_str = f"{speed_val:.1f}"
            elif self.mode == "EGT":
                label = "EGT"
                val_str = f"{egt_val}C"
            else: # AFR Modus
                label = "AFR"
                val_str = f"{afr_val:.2f}"

            # --- 3. ZEICHNEN ---
            # Label oben links
            draw.text((0, 0), label, font=self.font_small, fill="white")
            
            # Recording-Indikator oben rechts
            if is_logging:
                draw.text((85, 0), "[REC]", font=self.font_small, fill="white")

            # Große Zahl (X-Position leicht angepasst für 5-stellige RPM)
            # Bei sehr hohen Werten schieben wir den Text weiter nach links
            x_pos = 15 if len(val_str) < 5 else 5
            draw.text((x_pos, 8), val_str, font=self.font_big, fill="white")

            # Untere Statusleiste
            draw.line((0, 48, 128, 48), fill="white")
            fix_status = "GPS: OK" if gps_fix else "GPS: NO"
            # Kombinierte Info-Zeile: GPS-Status + Note (z.B. Fahrzeugname)
            bottom_text = f"{fix_status} | {info}"
            draw.text((0, 52), bottom_text, font=self.font_small, fill="white")