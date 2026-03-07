# StreetDyno 2.0 Config
import os

# Serial Setup für Arduino (Neu)
SERIAL_PORT = "/dev/ttyUSB0"  # Falls der Nano nicht erkannt wird, ttyACM0 prüfen
SERIAL_BAUD = 500000

# GPIO Pins für Joystick
JS_UP = 6         #
JS_DOWN = 19      #
JS_PRESS = 13     #

# Motor Setup (Wird nun primär im Arduino berechnet)
PULSES_PER_REV = 3    # 3 Impulse pro Umdrehung (Vespa Ducati Zündung)

# Logging
LOG_FILE = "logs/dyno_log.csv" #

# Verzeichnis für Logs erstellen falls nicht vorhanden
if not os.path.exists('logs'): #
    os.makedirs('logs')        #