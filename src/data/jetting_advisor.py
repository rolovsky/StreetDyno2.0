"""
StreetDyno 2.0 - Carburetor Jetting Advisor
Evaluates AFR air-fuel mixture across 4 operating regimes for Dell'Orto SI 24/24
carburetors and generates actionable mechanical jetting recommendations based on
fuel stoichiometry (E5, E10, E0) and specific component configurations.
"""

from __future__ import annotations
import math
from typing import Dict, Any, Optional, List, Union
import pandas as pd
from config import (
    load_carb_setup,
    FUEL_STOICHIOMETRY,
    SLIDE_TYPES,
    INTAKE_TYPES,
    AIRBOX_TYPES,
    EMULSION_TUBES,
    STANDARD_HLKD_VALUES
)


# Dell'Orto SI Idle Jet Database (Fuel / Air across 160er, 140er, and 120er scales)
# Quotient Q = Air / Fuel (Higher Q = more air per fuel = LEANER; Smaller Q = less air / more fuel = RICHER)
DELLORTO_SI_IDLE_JETS = [
    # 160er Skala (Standard Largeframe SI 24/24)
    {"name": "55/160", "fuel": 55, "air": 160, "scale": 160, "ratio": 160.0 / 55.0},  # 2.91 (Mager)
    {"name": "58/160", "fuel": 58, "air": 160, "scale": 160, "ratio": 160.0 / 58.0},  # 2.76
    {"name": "60/160", "fuel": 60, "air": 160, "scale": 160, "ratio": 160.0 / 60.0},  # 2.67 (Standard Referenz)
    {"name": "62/160", "fuel": 62, "air": 160, "scale": 160, "ratio": 160.0 / 62.0},  # 2.58 (+6.8% Benzin)
    {"name": "65/160", "fuel": 65, "air": 160, "scale": 160, "ratio": 160.0 / 65.0},  # 2.46 (+17.4% Benzin)
    {"name": "68/160", "fuel": 68, "air": 160, "scale": 160, "ratio": 160.0 / 68.0},  # 2.35 (+28.4% Benzin)

    # 140er Skala (Klassisch / Übergang)
    {"name": "48/140", "fuel": 48, "air": 140, "scale": 140, "ratio": 140.0 / 48.0},  # 2.92 (Mager)
    {"name": "50/140", "fuel": 50, "air": 140, "scale": 140, "ratio": 140.0 / 50.0},  # 2.80
    {"name": "52/140", "fuel": 52, "air": 140, "scale": 140, "ratio": 140.0 / 52.0},  # 2.69
    {"name": "55/140", "fuel": 55, "air": 140, "scale": 140, "ratio": 140.0 / 55.0},  # 2.55 (+10.5% Benzin vs 50/140)

    # 120er Skala (Alter SI-Standard / Fetter Grundaufbau)
    {"name": "42/120", "fuel": 42, "air": 120, "scale": 120, "ratio": 120.0 / 42.0},  # 2.86
    {"name": "45/120", "fuel": 45, "air": 120, "scale": 120, "ratio": 120.0 / 45.0},  # 2.67
    {"name": "48/120", "fuel": 48, "air": 120, "scale": 120, "ratio": 120.0 / 48.0},  # 2.50
    {"name": "50/120", "fuel": 50, "air": 120, "scale": 120, "ratio": 120.0 / 50.0},  # 2.40
    {"name": "55/120", "fuel": 55, "air": 120, "scale": 120, "ratio": 120.0 / 55.0},  # 2.18 (Sehr fett)

    # 100er Skala (T5 / Rally / Extrem fett)
    {"name": "50/100", "fuel": 50, "air": 100, "scale": 100, "ratio": 100.0 / 50.0},  # 2.00 (Extrem fett)
]


def parse_nd_ratio(nd_str: str) -> float:
    """
    Calculates idle jet ratio Q = Air / Fuel (e.g. 60/160 -> 160 / 60 = 2.67).
    Note on Dell'Orto SI:
    - Higher ratio (e.g. 55/160 = 2.91) means MORE AIR relative to fuel -> LEANER.
    - Smaller ratio (e.g. 65/160 = 2.46 or 68/160 = 2.35) means LESS AIR / MORE FUEL -> RICHER.
    """
    try:
        parts = str(nd_str).strip().split('/')
        if len(parts) == 2:
            fuel = float(parts[0])
            air = float(parts[1])
            return air / fuel if fuel > 0 else 2.67
    except Exception:
        pass
    return 2.67


def parse_nd_scale(nd_str: str) -> int:
    """Extracts air corrector scale from idle jet (e.g. '60/160' -> 160, '55/140' -> 140)."""
    try:
        parts = str(nd_str).strip().split('/')
        if len(parts) == 2:
            return int(float(parts[1]))
    except Exception:
        pass
    return 160


def is_richer_idle_jet(new_jet: str, current_jet: str) -> bool:
    """Returns True if new_jet provides a richer mixture (smaller Air/Fuel quotient) than current_jet."""
    return parse_nd_ratio(new_jet) < parse_nd_ratio(current_jet)


def is_leaner_idle_jet(new_jet: str, current_jet: str) -> bool:
    """Returns True if new_jet provides a leaner mixture (higher Air/Fuel quotient) than current_jet."""
    return parse_nd_ratio(new_jet) > parse_nd_ratio(current_jet)


def get_idle_jet_advice(current_nd: str, target_direction: str) -> str:
    """
    Generates physically correct mechanical recommendations for Dell'Orto SI idle jets
    with series preservation (160er) and multi-scale escalation (140er/120er).
    target_direction: 'RICHER' (anfetten) or 'LEANER' (abmagern).
    """
    current_q = parse_nd_ratio(current_nd)
    current_scale = parse_nd_scale(current_nd)

    if target_direction == "RICHER":
        # 1. Look for richer candidates in the SAME scale first
        same_scale_richer = [
            j for j in DELLORTO_SI_IDLE_JETS
            if j["scale"] == current_scale and j["ratio"] < current_q - 0.04
        ]
        same_scale_richer.sort(key=lambda j: j["ratio"], reverse=True)

        if same_scale_richer:
            if "60/160" in current_nd:
                return (
                    f"ND von 60/160 (Q={current_q:.2f}) auf ND 65/160 (Q=2.46, +17.4% Benzin) anfetten "
                    f"und LLG-Schraube von 3.5 auf ca. 1.75-2.0 Umdrehungen zurückstellen "
                    f"(ND 62/160 als Zwischenschritt oder ND 68/160 als fetter Fallback)."
                )
            examples = " oder ".join([f"{j['name']} (Q={j['ratio']:.2f})" for j in same_scale_richer[:2]])
            return (
                f"ND von {current_nd} (Q={current_q:.2f}) auf fettere ND mit kleinerem Quotienten wie {examples} "
                f"wechseln (LLG-Schraube auf ~1.75-2.0 Umdrehungen Grundstellung)."
            )

        # 2. Escalation if no richer jet in the same scale exists
        other_scale_richer = [
            j for j in DELLORTO_SI_IDLE_JETS
            if j["ratio"] < current_q - 0.04
        ]
        other_scale_richer.sort(key=lambda j: j["ratio"], reverse=True)
        if other_scale_richer:
            examples = " oder ".join([f"{j['name']} (Q={j['ratio']:.2f})" for j in other_scale_richer[:2]])
            return (
                f"ND {current_nd} (Q={current_q:.2f}) ist bereits die fetteste Düse der {current_scale}er Skala! "
                f"Eskalation erforderlich: Wechsel auf fettere Skala wie {examples} oder LLG-Schraube weiter herausdrehen."
            )
        return f"ND {current_nd} ist bereits die absolut fetteste verfügbare Nebendüse (Q={current_q:.2f})."

    elif target_direction == "LEANER":
        # 1. Look for leaner candidates in the SAME scale first
        same_scale_leaner = [
            j for j in DELLORTO_SI_IDLE_JETS
            if j["scale"] == current_scale and j["ratio"] > current_q + 0.04
        ]
        same_scale_leaner.sort(key=lambda j: j["ratio"])

        if same_scale_leaner:
            examples = " oder ".join([f"{j['name']} (Q={j['ratio']:.2f})" for j in same_scale_leaner[:2]])
            return f"ND von {current_nd} (Q={current_q:.2f}) auf magerere ND mit größerem Quotienten wie {examples} wechseln."

        # 2. Escalation if no leaner jet in the same scale exists
        other_scale_leaner = [
            j for j in DELLORTO_SI_IDLE_JETS
            if j["ratio"] > current_q + 0.04
        ]
        other_scale_leaner.sort(key=lambda j: j["ratio"])
        if other_scale_leaner:
            examples = " oder ".join([f"{j['name']} (Q={j['ratio']:.2f})" for j in other_scale_leaner[:2]])
            return (
                f"ND {current_nd} (Q={current_q:.2f}) ist bereits die magerste Düse der {current_scale}er Skala! "
                f"Eskalation: Wechsel auf magerere Skala wie {examples}."
            )
        return f"ND {current_nd} ist bereits sehr mager (Q={current_q:.2f})."

    return f"Nebendüse {current_nd} (Q={current_q:.2f}) ist optimal abgestimmt."


def get_zone3_tube_hlkd_advice(
    mean_afr: float,
    t_min: float,
    t_max: float,
    status: str,
    lambda_measured: float,
    tube: str,
    hlkd: int,
    hd: int
) -> str:
    """
    Tiered advice for Zone 3 (Pre-Resonance & Emulsion Tube):
    1st Tier: Adjust Air Corrector (HLKD 160 -> 150 -> 140 or vice-versa).
    2nd Tier: Change Emulsion Tube (BE4/BE5 -> BE3 -> BE2 / Lemarxon).
    """
    tube_clean = str(tube).strip().lower()

    if "LEAN" in status:
        if hlkd > 150:
            return (
                f"Magerlauf beim Eintritt in die Resonanz (AFR {mean_afr:.1f} > {t_max:.1f})! "
                f"Schritt 1 (Bremsluft drosseln): HLKD von {hlkd} auf 150 oder 140 reduzieren, "
                f"um das Gemisch vor dem Resonanzeinstieg anzufetten."
            )
        elif hlkd > 140:
            return (
                f"Magerlauf beim Eintritt in die Resonanz (AFR {mean_afr:.1f} > {t_max:.1f})! "
                f"Schritt 1: HLKD von {hlkd} auf 140 reduzieren. Falls weiterhin mager, "
                f"Schritt 2: Fetteres Mischrohr (BE2 oder Lemarxon x234) verbauen."
            )
        else:
            if any(k in tube_clean for k in ["be4", "be5", "be6"]):
                tube_rec = "BE3 oder das deutlich fettere BE2"
            elif "be3" in tube_clean:
                tube_rec = "BE2 (späte Vormischung) oder Lemarxon x234 (High-Flow)"
            else:
                tube_rec = "BE2 oder Lemarxon x234 (ggf. auch Hauptdüse HD vergrößern)"
            return (
                f"Magerlauf vor Resonanz (AFR {mean_afr:.1f} > {t_max:.1f})! "
                f"HLKD ist mit {hlkd} bereits klein. Schritt 2: Wechsel von {tube} auf fetteres Mischrohr "
                f"wie {tube_rec} erforderlich."
            )
    elif status == "RICH":
        if hlkd < 160:
            return (
                f"Viertaktet vor Resonanzeintritt (AFR {mean_afr:.1f} < {t_min:.1f}). "
                f"Schritt 1 (Mehr Bremsluft): HLKD von {hlkd} auf 160 (oder 190) vergrößern."
            )
        else:
            if "be2" in tube_clean or "lemarxon" in tube_clean:
                tube_rec = "BE3 (linear) oder BE4/BE5"
            else:
                tube_rec = "BE4 oder BE5 (frühere Vormischung / magerer)"
            return (
                f"Viertaktet vor Resonanzeintritt (AFR {mean_afr:.1f} < {t_min:.1f}). "
                f"HLKD ist bereits {hlkd}. Schritt 2: Wechsel von {tube} auf magereres Mischrohr ({tube_rec}) testen."
            )
    else:
        return f"Mischrohr {tube} & HLKD {hlkd} versorgen den Motor im Resonanzeinstieg perfekt (λ={lambda_measured:.2f})."


def calculate_relative_air_density(
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25
) -> float:
    """
    Calculates Relative Air Density (RAD) normalized to DIN 70020 standard (20°C, 1013.25 hPa).
    RAD = rho_actual / rho_ref = (p / p_0) * (T_0 / T)
    """
    t_kelvin = max(233.15, float(temp_c) + 273.15)
    t_ref = 293.15  # 20°C
    p_ref = 1013.25
    p_actual = max(700.0, min(1100.0, float(pressure_hpa)))
    return (p_actual / p_ref) * (t_ref / t_kelvin)


def calculate_weather_corrected_main_jet(
    base_hd: Union[int, float],
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25
) -> Dict[str, Any]:
    """
    Calculates weather-corrected Dell'Orto SI main jet (HD) based on Relative Air Density (RAD).
    Physics: Fuel flow area scales with air density:
    HD_corr = round(HD_base * sqrt(RAD))
    """
    base = float(base_hd)
    rad = calculate_relative_air_density(temp_c, pressure_hpa)
    sqrt_rad = math.sqrt(rad)
    recommended_hd = int(round(base * sqrt_rad))
    delta_hd = recommended_hd - int(round(base))

    return {
        "base_hd": int(round(base)),
        "recommended_hd": recommended_hd,
        "delta_hd": delta_hd,
        "rad": round(rad, 4),
        "rad_pct": round(rad * 100.0, 1),
        "temp_c": round(float(temp_c), 1),
        "pressure_hpa": round(float(pressure_hpa), 1),
        "sqrt_rad": round(sqrt_rad, 4)
    }


def get_stoichiometric_afr(fuel_type: str = "Super_E5") -> float:
    """Returns the theoretical stoichiometric AFR for the selected fuel type."""
    return FUEL_STOICHIOMETRY.get(fuel_type, 14.30)


def analyze_carb_jetting(
    df: pd.DataFrame,
    carb_setup: Optional[Dict[str, Any]] = None,
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25
) -> Dict[str, Any]:
    """
    Analyzes telemetry AFR across 4 carburetor operating regimes and
    outputs component-specific recommendations for Dell'Orto SI 24/24,
    including Relative Air Density (RAD) main jet weather compensation.
    """
    if carb_setup is None:
        carb_setup = load_carb_setup()

    fuel_type = carb_setup.get("fuel_type", "Super_E5")
    stoich_afr = get_stoichiometric_afr(fuel_type)

    hd = int(float(carb_setup.get("main_jet_hd", 135)))
    nd = str(carb_setup.get("idle_jet_nd", "60/160"))
    hlkd = int(float(carb_setup.get("air_corrector_hlkd", 160)))
    tube = str(carb_setup.get("emulsion_tube", "Lemarxon x234"))
    
    slide_key = carb_setup.get("slide_type", "lemarxon_low")
    slide_label = SLIDE_TYPES.get(slide_key, "Lemarxon Low Cutaway")

    intake_key = carb_setup.get("intake_type", "polini_venturi")
    intake_label = INTAKE_TYPES.get(intake_key, "Polini Venturi Trichter")

    airbox_key = carb_setup.get("airbox_type", "polini_airbox")
    airbox_label = AIRBOX_TYPES.get(airbox_key, "Polini Airbox")

    weather_info = calculate_weather_corrected_main_jet(hd, temp_c, pressure_hpa)

    rpm_col = "RPM_smoothed" if "RPM_smoothed" in df.columns else ("RPM" if "RPM" in df.columns else None)
    afr_col = "AFR" if "AFR" in df.columns else None
    egt_col = "EGT_cleaned" if "EGT_cleaned" in df.columns else ("EGT" if "EGT" in df.columns else None)

    if not rpm_col or not afr_col or df.empty:
        return {
            "valid": False,
            "error": "Unzureichende Telemetriedaten für Vergaseranalyse.",
            "carb_setup": carb_setup,
            "weather_compensation": weather_info
        }

    valid_mask = (df[afr_col] >= 9.0) & (df[afr_col] <= 18.5) & (df[rpm_col] >= 1200)
    sub_df = df[valid_mask].copy()

    if len(sub_df) < 5:
        return {
            "valid": False,
            "error": "Zu wenige verwertbare AFR-Punkte im Pull.",
            "carb_setup": carb_setup,
            "weather_compensation": weather_info
        }

    # Dynamic Lambda-Based Zone Boundaries for 2-Stroke Vespa Largeframe
    # Zone 1 (ND & Idle, 1.500-3.200 RPM): Lambda 0.880 - 0.940 (AFR 12.6 - 13.4 for E5)
    # Zone 2 (Slide Hub, 3.200-4.800 RPM): Lambda 0.874 - 0.920 (AFR 12.5 - 13.2 for E5)
    # Zone 3 (Emulsion Tube / Pre-Reso, 4.800-6.500 RPM): Lambda 0.840 - 0.885 (AFR 12.0 - 12.7 for E5)
    # Zone 4 (WOT Main Jet, 6.500-9.500 RPM): Lambda 0.811 - 0.881 (Target AFR 11.6 - 12.6, optimal ~12.0-12.4 for 2-stroke safety & power)
    zones_def = [
        {
            "id": "zone1",
            "name": "Standgas & Teillast-Einstieg",
            "rpm_min": 1500,
            "rpm_max": 3200,
            "lambda_min": 0.880,
            "lambda_max": 0.940,
            "component": f"Nebendüse (ND {nd}) & Gemischschraube",
            "desc": "Leerlaufgemisch & unterer Schieberhub"
        },
        {
            "id": "zone2",
            "name": "Schieberhub & Übergang",
            "rpm_min": 3200,
            "rpm_max": 4800,
            "lambda_min": 0.874,
            "lambda_max": 0.920,
            "component": f"Gasschieber ({slide_label})",
            "desc": "Teillast (1/4 - 1/2 Gas) & Cutaway"
        },
        {
            "id": "zone3",
            "name": "Resonanz & Mischrohr",
            "rpm_min": 4800,
            "rpm_max": 6500,
            "lambda_min": 0.840,
            "lambda_max": 0.885,
            "component": f"Mischrohr ({tube}) & HLKD ({hlkd})",
            "desc": "Vor-Resonanz & Gemisch-Voremulgierung"
        },
        {
            "id": "zone4",
            "name": "Volllast & Peak Power",
            "rpm_min": 6500,
            "rpm_max": 9500,
            "lambda_min": 0.811,
            "lambda_max": 0.881,
            "component": f"Hauptdüse (HD {hd}) & Ansaugung",
            "desc": "Vollgas, Venturi-Strömung & thermischer Klemmschutz (11.6-12.6 AFR)"
        }
    ]

    evaluated_zones: List[Dict[str, Any]] = []
    has_critical_lean = False
    needs_tuning = False

    for z in zones_def:
        t_min = round(z["lambda_min"] * stoich_afr, 2)
        t_max = round(z["lambda_max"] * stoich_afr, 2)

        z_mask = (sub_df[rpm_col] >= z["rpm_min"]) & (sub_df[rpm_col] < z["rpm_max"])
        z_data = sub_df[z_mask]

        if len(z_data) < 2:
            evaluated_zones.append({
                "id": z["id"],
                "name": z["name"],
                "rpm_range": f"{z['rpm_min']}-{z['rpm_max']} U/min",
                "component": z["component"],
                "mean_afr": None,
                "target": f"{t_min:.1f}-{t_max:.1f}",
                "status": "NO_DATA",
                "status_text": "KEINE DATEN",
                "badge_class": "badge-secondary",
                "advice": "In diesem Drehzahlbereich lagen während des Pulls keine stabilen Messwerte vor.",
                "gauge_pct": 50
            })
            continue

        mean_afr = float(z_data[afr_col].mean())
        lambda_measured = round(mean_afr / stoich_afr, 3)

        # Zone-specific threshold evaluation
        if z["id"] == "zone4":
            if mean_afr > 13.5:
                status = "CRITICAL_LEAN"
                status_text = "🚨 KRITISCH MAGER"
                badge_class = "badge-danger"
                has_critical_lean = True
            elif mean_afr > t_max + 0.2:
                status = "LEAN"
                status_text = "⚠️ LEICHT MAGER"
                badge_class = "badge-warning"
                needs_tuning = True
            elif mean_afr < t_min - 0.4:
                status = "RICH"
                status_text = "🔵 ZU FETT"
                badge_class = "badge-info"
                needs_tuning = True
            else:
                status = "PERFECT"
                status_text = "🟢 PERFEKT / FETT & SICHER"
                badge_class = "badge-success"
        else:
            if mean_afr > 14.8:
                status = "CRITICAL_LEAN"
                status_text = "🚨 MAGERLOCH"
                badge_class = "badge-danger"
                has_critical_lean = True
            elif mean_afr > t_max + 0.2:
                status = "LEAN"
                status_text = "⚠️ LEICHT MAGER"
                badge_class = "badge-warning"
                needs_tuning = True
            elif mean_afr < t_min - 0.5:
                status = "RICH"
                status_text = "🔵 ZU FETT"
                badge_class = "badge-info"
                needs_tuning = True
            else:
                status = "PERFECT"
                status_text = "🟢 PERFEKT"
                badge_class = "badge-success"

        advice = ""
        zid = z["id"]

        if zid == "zone1":
            cur_q = parse_nd_ratio(nd)
            if "LEAN" in status or status == "CRITICAL_LEAN":
                nd_advice = get_idle_jet_advice(nd, "RICHER")
                advice = f"🚨 Magerloch im unteren Teillastbereich (AFR {mean_afr:.1f} > 14.5)! Gemischschraube 0.5-1.0 Umdrehungen herausdrehen (fetter). Falls AFR weiterhin > {t_max:.1f}, {nd_advice}"
            elif status == "RICH":
                nd_advice = get_idle_jet_advice(nd, "LEANER")
                advice = f"Teillast überfettet (AFR {mean_afr:.1f} < {t_min:.1f}). Gemischschraube 0.5 Umdrehungen hineindrehen (magerer) bzw. {nd_advice}"
            else:
                advice = f"Nebendüse {nd} (Q={cur_q:.2f}) & Gemischschraube arbeiten im optimalen Lambda-Bereich (λ={lambda_measured:.2f})."

        elif zid == "zone2":
            if "LEAN" in status or status == "CRITICAL_LEAN":
                nd_advice = get_idle_jet_advice(nd, "RICHER")
                if slide_key == "bgm_std_cutout":
                    advice = f"🚨 Starkes Magerloch durch großen BGM Standard-Cutaway! Empfehlung: Wechsel auf 'Lemarxon Mid Cutaway' oder 'Lemarxon Low Cutaway' für sicheren Teillast-Übergang bzw. {nd_advice}"
                elif slide_key == "lemarxon_mid":
                    advice = f"🚨 Magerloch im Schieberübergang (AFR {mean_afr:.1f})! Empfehlung: Wechsel auf 'Lemarxon Low Cutaway' (fetter) oder {nd_advice}"
                else:
                    advice = f"🚨 Magerloch (AFR {mean_afr:.1f} > 14.5) trotz {slide_label}. Mischrohr ({tube}) prüfen oder {nd_advice}"
            elif status == "RICH":
                if slide_key == "lemarxon_low":
                    nd_advice = get_idle_jet_advice(nd, "LEANER")
                    advice = f"Überfettet bei 1/4 bis 1/2 Gas (AFR {mean_afr:.1f} < 11.8). Wechsel auf 'Lemarxon Mid Cutaway' (magerer) sorgt für agilere Gasannahme bzw. {nd_advice}"
                else:
                    nd_advice = get_idle_jet_advice(nd, "LEANER")
                    advice = f"Teillast überfettet leicht (AFR {mean_afr:.1f} < {t_min:.1f}). Schieber mit größerem Cutaway testen oder {nd_advice}"
            else:
                advice = f"Gasschieber ({slide_label}) sorgt für einen sauberen, stempelfreien Teillastübergang (λ={lambda_measured:.2f})."

        elif zid == "zone3":
            advice = get_zone3_tube_hlkd_advice(
                mean_afr=mean_afr,
                t_min=t_min,
                t_max=t_max,
                status=status,
                lambda_measured=lambda_measured,
                tube=tube,
                hlkd=hlkd,
                hd=hd
            )

        elif zid == "zone4":
            intake_note = ""
            if intake_key == "polini_venturi":
                intake_note = f" (Polini Venturi Trichter erfordert ca. +6 bis +10 HD-Größen gegenüber Serienfilter!)"
            elif intake_key == "lemarxon_22mm":
                intake_note = f" (22mm Lemarxon Hülse optimiert die Strömungsgeschwindigkeit)."

            weather_note = ""
            if abs(weather_info["delta_hd"]) >= 1:
                sign = "+" if weather_info["delta_hd"] > 0 else ""
                weather_note = (
                    f" [Wetterkorrektur bei {weather_info['temp_c']}°C / {weather_info['pressure_hpa']} hPa: "
                    f"RAD {weather_info['rad_pct']}% -> Empfohlene HD {weather_info['recommended_hd']} "
                    f"({sign}{weather_info['delta_hd']} zur 20°C Basis)]"
                )

            if status == "CRITICAL_LEAN":
                advice = f"🚨 AKUTE KLEMMGEFAHR BEI VOLLGAS (AFR {mean_afr:.1f} > 13.5)! Hauptdüse HD {hd} sofort um mind. +4 bis +6 Nummern vergrößern (z.B. HD {hd+4}/{hd+6})!{intake_note}{weather_note}"
            elif status == "LEAN":
                advice = f"Hauptdüse HD {hd} etwas zu mager (AFR {mean_afr:.1f} > 12.8). Empfehlung: HD um +2 bis +3 Nummern vergrößern (z.B. HD {hd+2}).{intake_note}{weather_note}"
            elif status == "RICH":
                advice = f"Motor drosselt obenraus / überfettet (AFR {mean_afr:.1f} < 11.2). HD {hd} um 2 Nummern verkleinern (z.B. HD {hd-2}).{weather_note}"
            else:
                advice = f"Hauptdüse HD {hd} mit {intake_label} liefert optimale Leistung bei maximaler Innenkühlung (AFR {mean_afr:.1f}, λ={lambda_measured:.2f} - FETT & SICHER).{weather_note}"

        gauge_pct = int(max(0, min(100, ((mean_afr - (stoich_afr * 0.70)) / (stoich_afr * 0.45)) * 100)))

        evaluated_zones.append({
            "id": zid,
            "name": z["name"],
            "rpm_range": f"{z['rpm_min']}-{z['rpm_max']} U/min",
            "component": z["component"],
            "mean_afr": round(mean_afr, 2),
            "lambda": lambda_measured,
            "target": f"{t_min:.1f}-{t_max:.1f}",
            "status": status,
            "status_text": status_text,
            "badge_class": badge_class,
            "advice": advice,
            "gauge_pct": gauge_pct
        })

    if has_critical_lean:
        overall_status = "CRITICAL"
        overall_verdict = "🚨 KRITISCH: Akuter Magerlauf unter Last! Bedüsung sofort anpassen, um Motorschäden zu vermeiden."
    elif needs_tuning:
        overall_status = "TUNE"
        overall_verdict = "⚠️ OPTIMIERUNGSBEDARF: Gemisch weicht in Teilbereichen vom Ideal ab. Siehe Zonen-Details."
    else:
        overall_status = "PERFECT"
        overall_verdict = f"🟢 OPTIMAL: Vergaser-Bedüsung perfekt auf {fuel_type} (Stöchiometrie {stoich_afr:.2f}) abgestimmt."

    # Schub-Magerlauf check (Gaswegnahme)
    if "dRPM_dt" in df.columns:
        schub_mask = (df["dRPM_dt"] < -400.0) & (df[afr_col] > 17.0)
        if schub_mask.any():
            overall_verdict += " (Hinweis: Schub-Magerlauf bei Gaswegnahme erkannt -> typische Ursache für Krümmer-Patschen)."

    max_egt = float(sub_df[egt_col].max()) if egt_col and not sub_df[egt_col].isna().all() else None
    avg_total_afr = float(sub_df[afr_col].mean())

    return {
        "valid": True,
        "overall_status": overall_status,
        "overall_verdict": overall_verdict,
        "avg_total_afr": round(avg_total_afr, 2),
        "avg_total_lambda": round(avg_total_afr / stoich_afr, 3),
        "fuel_type": fuel_type,
        "stoich_afr": stoich_afr,
        "max_egt": round(max_egt, 0) if max_egt else None,
        "carb_setup": carb_setup,
        "weather_compensation": weather_info,
        "zones": evaluated_zones
    }

