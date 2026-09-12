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
TRIP_LOG_DIR = os.path.join(LOG_DIR, "trips")
PLOT_DIR = os.path.join(BASE_DIR, "plots")
USER_SETUP_FILE = os.path.join(BASE_DIR, "user_setup.json")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TRIP_LOG_DIR, exist_ok=True)
os.makedirs(PLOT_DIR, exist_ok=True)

# --- Hardware & Serial Configuration ---
SERIAL_PORT: str = "/dev/ttyUSB0"
SERIAL_BAUD: int = 115200

# Exponential Moving Average (EMA) smoothing factors for HUD
ALPHA_RPM: float = 0.20
ALPHA_AFR: float = 0.15

# --- Vehicle Baseline Parameters (VMC Super G 177 / 187cc Langhub / Vespa PX 125 Lusso) ---
VEHICLE_NAME: str = "VMC177"
VEHICLE_DESCRIPTION: str = "Vespa PX 125 Lusso (VMC Super G 187cc / 60mm Langhub / SI 24)"
PULSES_PER_REV: int = 3  # 3 pulses per crankshaft revolution (SIP / Ducati CDI)

# Engine Geometry & Port Timing
DISPLACEMENT_CC: float = 187.0        # 63mm bore x 60mm stroke
STROKE_MM: float = 60.0               # 60mm Langhubwelle
BORE_MM: float = 63.0                 # 63mm VMC Super G Grauguss
CONROD_MM: float = 105.0              # 105mm standard conrod
SQUISH_MM: float = 1.4                # 1.4mm Quetschkante
IGNITION_DEG_BTDC: float = 18.0       # 18° v.OT statisch
TIMING_EXHAUST_DEG: float = 176.5     # 176.46° Auslasszeit
TIMING_TRANSFER_DEG: float = 121.1    # 121.11° Überströmzeit
TIMING_BLOWDOWN_DEG: float = 27.7     # 27.67° Vorauslass
TIMING_INTAKE_VOT: float = 107.5      # 107.51° v.OT Einlass öffnet
TIMING_INTAKE_NOT: float = 54.0       # 53.98° n.OT Einlass schließt

# Physical Vehicle Dynamics Parameters
TOTAL_MASS_KG: float = 190.0          # 112 kg Vespa PX + 78 kg Rider
ROTATIONAL_MASS_FACTOR: float = 1.05  # Rotational inertia multiplier (wheels, flywheel, crank)
J_WHEELS_KG_M2: float = 0.12          # Massenträgheitsmoment beider Räder inkl. Trommeln/Reifen
J_ENGINE_KG_M2: float = 0.0120        # Massenträgheitsmoment Kurbelwelle, Kupplung und Polrad
                                       # SIP Touren 2.0 (1800g, ø197mm): J_Polrad = ½·1.8·0.0985² ≈ 0.0087 kg·m²
                                       # + BGM 60mm Welle + Pleuel + Kupplungskorb ≈ 0.0033 kg·m²
TIRE_NAME: str = "Heidenau K80 SR 100/90-10"
TIRE_CIRCUMFERENCE_M: float = 1.365   # Heidenau K80 SR 100/90-10 rolling circumference in meters
TIRE_RADIUS_DYN_M: float = 0.2118    # F-03: Dynamic loaded radius for J/r² back-calculation (m).
                                      # = U/(2π) × 0.975 = 1.365/(2π) × 0.975 ≈ 0.2118 m.

PRIMARY_TEETH: str = "23/68"
PRIMARY_RATIO: float = 68.0 / 23.0    # 23/68 teeth = 2.9565217...

GEAR_TEETH: Dict[int, str] = {
    1: "12/57",
    2: "13/42",
    3: "17/38",
    4: "21/36"
}
GEAR_RATIOS: Dict[int, float] = {
    1: 57.0 / 12.0,                   # 1st Gear (12/57 = 4.7500 -> i_total = 14.0435)
    2: 42.0 / 13.0,                   # 2nd Gear (13/42 = 3.2308 -> i_total = 9.5518)
    3: 38.0 / 17.0,                   # 3rd Gear (17/38 = 2.2353 -> i_total = 6.6087)
    4: 36.0 / 21.0                    # 4th Gear (21/36 = 1.7143 -> i_total = 5.0683)
}

CW_A: float = 0.55                    # Drag coefficient * frontal area (m²)
                                       # Vespa PX 125 Baseline (A ≈ 0.70 m², cw ≈ 0.78, ohne Windschild)
CR: float = 0.015                      # Rolling resistance coefficient (Heidenau K80 SR auf Asphalt)
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
    "lemarxon_22mm": "22mm Reduzierhülse Lemarxon",
    "polini_venturi": "Polini Venturi Trichter",
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
    "intake_type": "lemarxon_22mm",
    "airbox_type": "polini_airbox",
    "main_jet_hd": 125,
    "idle_jet_nd": "60/160",
    "air_corrector_hlkd": 160,
    "emulsion_tube": "Lemarxon x234",
    "exhaust": "Polini Box",
    "ignition_deg": 18.0,
    "displacement_cc": 187.0,
    "stroke_mm": 60.0,
    "notes": "VMC Super G 187cc Langhub / HD 125 / ND 60/160 / 18° Zdg"
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


def get_full_setup_metadata(custom_notes: str = "") -> Dict[str, Any]:
    """Builds a comprehensive standardized engine, carburetor, and vehicle metadata dictionary."""
    carb = load_carb_setup()
    v_cfg = carb.get("vehicle", {}) if isinstance(carb.get("vehicle"), dict) else {}
    return {
        "displacement_cc": DISPLACEMENT_CC,
        "stroke_mm": STROKE_MM,
        "bore_mm": BORE_MM,
        "squish_mm": SQUISH_MM,
        "ignition_deg": IGNITION_DEG_BTDC,
        "timing": {
            "exhaust_deg": TIMING_EXHAUST_DEG,
            "transfer_deg": TIMING_TRANSFER_DEG,
            "blowdown_deg": TIMING_BLOWDOWN_DEG,
            "intake_vot_deg": TIMING_INTAKE_VOT,
            "intake_not_deg": TIMING_INTAKE_NOT,
        },
        "carb": carb,
        "vehicle": {
            "name": v_cfg.get("name", VEHICLE_NAME),
            "mass_kg": float(v_cfg.get("mass_kg", TOTAL_MASS_KG)),
            "tire_name": v_cfg.get("tire_name", TIRE_NAME),
            "tire_circumference_m": float(v_cfg.get("tire_circumference_m", TIRE_CIRCUMFERENCE_M)),
            "primary_ratio": float(v_cfg.get("primary_ratio", PRIMARY_RATIO)),
            "primary_teeth": v_cfg.get("primary_teeth", PRIMARY_TEETH),
            "gear_ratios": v_cfg.get("gear_ratios", GEAR_RATIOS),
            "gear_teeth": v_cfg.get("gear_teeth", GEAR_TEETH),
        },
        "notes": custom_notes or carb.get("notes", "")
    }


CARB_SETUP: Dict[str, Any] = load_carb_setup()

