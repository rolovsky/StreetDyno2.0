from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import sh1106
import RPi.GPIO as GPIO

class OLEDDisplay:
    def __init__(self):
        try:
            # Waveshare 1.3" OLED HAT nutzt standardmäßig:
            # SPI Port 0, Device 0
            # GPIO 24 für Data/Command (DC)
            # GPIO 25 für Reset (RST) 
            self.serial = spi(device=0, port=0, gpio_DC=24, gpio_RST=25)
            self.device = sh1106(self.serial)
            self.device.clear()
            print("✅ OLED SPI Modus aktiv (Waveshare HAT)")
        except Exception as e:
            print(f"❌ OLED Fehler: {e}")
            self.device = None

    def show_status(self, rpm, speed, afr, info, gps_fix):
        if not self.device: return
        with canvas(self.device) as draw:
            # Anzeige-Layout
            draw.text((5, 2),   f"RPM:   {int(rpm)}", fill="white")
            draw.text((5, 18),  f"SPEED: {speed} km/h", fill="white")
            draw.text((5, 34),  f"AFR:   {afr}", fill="white")
            status = "FIX" if gps_fix else "SEARCH..."
            draw.text((5, 50),  f"GPS: {status} | {info}", fill="white")

    def clear(self):
        if self.device: self.device.clear()