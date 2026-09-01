"""
StreetDyno 2.0 - Central Configuration Module
Defines vehicle physical specifications, sensor calibration factors,
paths, and persistent carburetor setup management.
"""

from __future__ import annotations
import os
import json
from typing import Dict, Any

# --- Base Directories ---
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SRC_DIR, ".."))

LOG_DIR = os.path.join(BASE_DIR, "logs")
PLOT_DIR = os.path.join(BASE_DIR, "plots")
USER_SETUP_FILE = os.path.join(BASE_DIR, "user_setup.json")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PLOT_DIR, exist_ok=True)

# --- Hardware & Serial Configuration ---
SERIAL_PORT: str = "/dev/ttyUSB0"
SERIAL_BAUD: int = 115200

# Exponential Moving Average (EMA) smoothing factors for HUD
ALPHA_RPM: float = 0.20
ALPHA_AFR: float = 0.15

# --- Vehicle Baseline Parameters (VMC 177 / Vespa PX 125 Lusso) ---
VEHICLE_NAME: str = "VMC177"
VEHICLE_DESCRIPTION: str = "Vespa PX 125 Lusso (VMC 177 / 60mm Welle / SI 24)"
PULSES_PER_REV: int = 3  # 3 pulses per crankshaft revolution (SIP / Ducati CDI)

# Physical Vehicle Dynamics Parameters
TOTAL_MASS_KG: float = 190.0          # 112 kg Vespa PX + 78 kg Rider
ROTATIONAL_MASS_FACTOR: float = 1.05  # Rotational inertia multiplier (wheels, flywheel, crank)
TIRE_CIRCUMFERENCE_M: float = 1.350   # 100/90-10 tire rolling circumference in meters

PRIMARY_RATIO: float = 68.0 / 23.0    # 23/68 teeth = 2.9565
GEAR_RATIOS: Dict[int, float] = {
    1: 58.0 / 12.0,                   # 1st Gear (12/58 = 4.8333 -> i_total = 14.29)
    2: 42.0 / 13.0,                   # 2nd Gear (13/42 = 3.2308 -> i_total = 9.55)
    3: 38.0 / 17.0,                   # 3rd Gear (17/38 = 2.2353 -> i_total = 6.61)
    4: 35.0 / 21.0                    # 4th Gear (21/35 = 1.6667 -> i_total = 4.93)
}

CW_A: float = 0.50                    # Drag coefficient * frontal area (m²)
CR: float = 0.015                     # Rolling resistance coefficient
AIR_DENSITY: float = 1.205            # Ambient air density rho (kg/m³)
TRANSMISSION_EFFICIENCY: float = 0.90 # Powertrain mechanical efficiency
GRAVITY: float = 9.81                 # Gravitational acceleration (m/s²)

# --- Dell'Orto SI 24/24 Carburetor Component & Fuel Mappings ---
FUEL_STOICHIOMETRY: Dict[str, float] = {
    "Super_E5": 14.30,
    "Super_E10": 14.10,
    "SuperPlus_E0": 14.70
}

SLIDE_TYPES: Dict[str, str] = {
    "lemarxon_low": "Lemarxon Low Cutaway (aktuell aktiv / fett)",
    "lemarxon_mid": "Lemarxon Mid Cutaway (mittel)",
    "bgm_std_cutout": "BGM FastFlow 24/24 Standard mit Cutaway (mager)"
}

INTAKE_TYPES: Dict[str, str] = {
    "polini_venturi": "Polini Venturi Trichter",
    "lemarxon_22mm": "22mm Reduzierhülse Lemarxon",
    "orig_drilled": "Originalfilter mit Bohrungen (5mm/8mm)",
    "open_no_filter": "Ohne Filter / Trichter"
}

AIRBOX_TYPES: Dict[str, str] = {
    "polini_airbox": "Polini Airbox (Großer Deckel)",
    "orig_box_cover": "Original Vergaserdeckel",
    "no_cover": "Ohne Deckel (Offene Wanne)"
}

DEFAULT_CARB_SETUP: Dict[str, Any] = {
    "carburetor_type": "BGM 24/24 Fastflow",
    "fuel_type": "Super_E5",
    "slide_type": "lemarxon_low",
    "intake_type": "polini_venturi",
    "airbox_type": "polini_airbox",
    "main_jet_hd": 135,
    "idle_jet_nd": "60/160",
    "air_corrector_hlkd": 160,
    "emulsion_tube": "Lemarxon x234",
    "exhaust": "Polini Box",
    "notes": "VMC 177 / 60mm Welle / Baseline Setup"
}


def load_carb_setup() -> Dict[str, Any]:
    """Loads carburetor setup from user_setup.json with fallback to defaults."""
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


def save_carb_setup(setup_dict: Dict[str, Any]) -> bool:
    """Persists updated carburetor setup to user_setup.json."""
    try:
        current = load_carb_setup()
        current.update(setup_dict)
        with open(USER_SETUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(current, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[CONFIG ERROR] Failed to save carb setup: {e}")
        return False


CARB_SETUP: Dict[str, Any] = load_carb_setup()

