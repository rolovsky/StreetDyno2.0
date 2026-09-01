# StreetDyno 2.0 Config
import os

# --- Serial Setup für Arduino ---
SERIAL_PORT = "/dev/ttyUSB0"  # Falls der Nano nicht erkannt wird, ttyACM0 prüfen
SERIAL_BAUD = 115200          # 115200 Baud für Arduino Nano

# --- GPIO Pins für Joystick (BCM Nummern) ---
JS_UP = 6         # Modus wechseln
JS_DOWN = 19      # Modus wechseln
JS_PRESS = 13     # Logging Start/Stop

# --- Motor & Fahrzeug Setup (Vespa PX 125 Lusso / VMC 177) ---
VEHICLE_NAME = "VMC177"
VEHICLE_DESCRIPTION = "Vespa PX 125 Lusso (VMC 177 / 60mm / SI 24)"
PULSES_PER_REV = 3            # 3 Impulse pro Umdrehung (Vespa Ducati / SIP Zündung)

# --- Physikalische Fahrzeugparameter für Dyno-Berechnung ---
TOTAL_MASS_KG = 190.0         # 112 kg Roller + 78 kg Fahrer
ROTATIONAL_MASS_FACTOR = 1.05 # Zuschlag für rotierende Massen (Räder, Polrad, Getriebe)
TIRE_CIRCUMFERENCE_M = 1.350  # 100/90-10 Abrollumfang in Metern

PRIMARY_RATIO = 68.0 / 23.0   # Primärübersetzung (23/68 = 2.9565)
GEAR_RATIOS = {
    1: 58.0 / 12.0,           # 1. Gang (12/58 = 4.8333 -> i_total = 14.29)
    2: 42.0 / 13.0,           # 2. Gang (13/42 = 3.2308 -> i_total = 9.55)
    3: 38.0 / 17.0,           # 3. Gang (17/38 = 2.2353 -> i_total = 6.61)
    4: 35.0 / 21.0            # 4. Gang (21/35 = 1.6667 -> i_total = 4.93)
}

CW_A = 0.50                   # Luftwiderstandsbeiwert * Stirnfläche (m²)
CR = 0.015                    # Rollwiderstandskoeffizient
AIR_DENSITY = 1.205           # Luftdichte rho in kg/m³
TRANSMISSION_EFFICIENCY = 0.90 # Wirkungsgrad Antriebsstrang (Getriebe/Kette/Reifen)
GRAVITY = 9.81                # Erdbeschleunigung m/s²

# --- Logging Verzeichnis ---
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# --- Vergaser & Bedüsung Setup (BGM 24/24 Fastflow Baseline) ---
import json

USER_SETUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "user_setup.json")

DEFAULT_CARB_SETUP = {
    "carburetor_type": "BGM 24/24 Fastflow",
    "main_jet_hd": 135,
    "idle_jet_nd": "60/160",
    "air_corrector_hlkd": 160,
    "emulsion_tube": "Lemarxon x234",
    "throttle_slide": "Lemarxon Low",
    "intake_funnel": "Polini Venturi Trichter",
    "exhaust": "Polini Box",
    "notes": "VMC 177 / 60mm Welle / Baseline Setup"
}

def load_carb_setup():
    if os.path.exists(USER_SETUP_FILE):
        try:
            with open(USER_SETUP_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                res = DEFAULT_CARB_SETUP.copy()
                res.update(data)
                return res
        except Exception:
            pass
    return DEFAULT_CARB_SETUP.copy()

def save_carb_setup(setup_dict):
    try:
        current = load_carb_setup()
        current.update(setup_dict)
        with open(USER_SETUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(current, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[CONFIG ERROR] Failed to save carb setup: {e}")
        return False

CARB_SETUP = load_carb_setup()