# StreetDyno 2.0 Config
import os

# --- Serial Setup für Arduino ---
SERIAL_PORT = "/dev/ttyUSB0"  # Falls der Nano nicht erkannt wird, ttyACM0 prüfen
SERIAL_BAUD = 500000

# --- GPIO Pins für Joystick (BCM Nummern) ---
JS_UP = 6         # Modus wechseln
JS_DOWN = 19      # Modus wechseln
JS_PRESS = 13     # Logging Start/Stop

# --- Motor & Fahrzeug Setup ---
VEHICLE_NAME = "VMC177"
PULSES_PER_REV = 3    # 3 Impulse pro Umdrehung (Vespa Ducati Zündung)

# --- Logging Verzeichnis ---
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)