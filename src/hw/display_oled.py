from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont

class OLEDDisplay:
    def __init__(self):
        self.device = None
        self.mode = "RPM"
        try:
            self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
            self.device = sh1106(self.serial)
            f_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            self.font_big = ImageFont.truetype(f_path, 34)
            self.font_small = ImageFont.truetype(f_path, 10)
            print("[OK] OLED geladen.")
        except Exception as e: print(f"OLED Fehler: {e}")

    def set_mode(self, new_mode): self.mode = new_mode
    def clear(self): 
        if self.device: self.device.clear()

    def show_status(self, rpm, speed, afr, egt, info, gps_fix, is_logging=False):
        if not self.device: return
        with canvas(self.device) as draw:
            if self.mode == "RPM": label, val = "RPM", f"{int(rpm)}"
            elif self.mode == "SPEED": label, val = "KM/H", f"{speed:.1f}"
            elif self.mode == "EGT": label, val = "EGT", f"{int(egt)}C"
            else: label, val = "AFR", f"{afr:.2f}"

            draw.text((0, 0), label, font=self.font_small, fill="white")
            if is_logging: draw.text((80, 0), "[REC]", font=self.font_small, fill="white")
            draw.text((15, 8), val, font=self.font_big, fill="white")
            draw.line((0, 48, 128, 48), fill="white")
            fix = "GPS: OK" if gps_fix else "GPS: NO"
            draw.text((0, 52), f"{fix} | {info}", font=self.font_small, fill="white")