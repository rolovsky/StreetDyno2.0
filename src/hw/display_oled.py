class OLEDDisplay:
    def __init__(self):
        try:
            # Hier käme die echte luma.oled Initialisierung
            print("📺 OLED Hardware erkannt.")
        except:
            print("📺 OLED Simulation (keine Hardware gefunden).")

    def clear(self):
        pass

    def show_status(self, rpm, speed, afr, info, gps_fix):
        # Im Codespace geben wir den Status einfach nur kurz im Terminal aus
        pass