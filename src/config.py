# StreetDyno 2.0 Config
import os

# GPIO Pins
RPM_PIN = 24            # Dein Signal-Pin für die Drehzahl
JOYSTICK_PRESS_PIN = 13 # Button zum Starten/Stoppen des Logs

# Motor Setup
PULSES_PER_REV = 1      # 1 Impuls pro Umdrehung (Vespa Standard)
RPM_AVG_WINDOW_S = 0.2  # Glättung über 0.2 Sekunden

# Logging
LOG_FILE = "logs/dyno_log.csv"
DEBUG = False

# Verzeichnis für Logs erstellen falls nicht vorhanden
if not os.path.exists('logs'):
    os.makedirs('logs')