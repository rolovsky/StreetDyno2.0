"""
StreetDyno 2.0 - Carburetor Jetting Advisor
Evaluates AFR air-fuel mixture across 4 operating regimes for Dell'Orto SI 24/24
carburetors and generates actionable mechanical jetting recommendations based on
fuel stoichiometry (E5, E10, E0) and specific component configurations.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, List
import pandas as pd
from config import (
    load_carb_setup,
    FUEL_STOICHIOMETRY,
    SLIDE_TYPES,
    INTAKE_TYPES,
    AIRBOX_TYPES
)


def parse_nd_ratio(nd_str: str) -> float:
    """Calculates idle jet ratio (e.g. 60/160 -> 160 / 60 = 2.67)."""
    try:
        parts = str(nd_str).split('/')
        if len(parts) == 2:
            fuel = float(parts[0])
            air = float(parts[1])
            return air / fuel if fuel > 0 else 2.67
    except Exception:
        pass
    return 2.67


def get_stoichiometric_afr(fuel_type: str = "Super_E5") -> float:
    """Returns the theoretical stoichiometric AFR for the selected fuel type."""
    return FUEL_STOICHIOMETRY.get(fuel_type, 14.30)


def analyze_carb_jetting(
    df: pd.DataFrame,
    carb_setup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Analyzes telemetry AFR across 4 carburetor operating regimes and
    outputs component-specific recommendations for Dell'Orto SI 24/24.
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

    rpm_col = "RPM_smoothed" if "RPM_smoothed" in df.columns else ("RPM" if "RPM" in df.columns else None)
    afr_col = "AFR" if "AFR" in df.columns else None
    egt_col = "EGT_cleaned" if "EGT_cleaned" in df.columns else ("EGT" if "EGT" in df.columns else None)

    if not rpm_col or not afr_col or df.empty:
        return {
            "valid": False,
            "error": "Unzureichende Telemetriedaten für Vergaseranalyse.",
            "carb_setup": carb_setup
        }

    valid_mask = (df[afr_col] >= 9.0) & (df[afr_col] <= 18.5) & (df[rpm_col] >= 1200)
    sub_df = df[valid_mask].copy()

    if len(sub_df) < 5:
        return {
            "valid": False,
            "error": "Zu wenige verwertbare AFR-Punkte im Pull.",
            "carb_setup": carb_setup
        }

    # Dynamic Lambda-Based Zone Boundaries
    # Zone 1 (ND & Idle): Lambda 0.895 - 0.930
    # Zone 2 (Slide Hub): Lambda 0.881 - 0.909
    # Zone 3 (Emulsion Tube / Pre-Reso): Lambda 0.874 - 0.902
    # Zone 4 (WOT Main Jet): Lambda 0.867 - 0.895 (Target lambda ~ 0.86 for max power & cooling)
    zones_def = [
        {
            "id": "zone1",
            "name": "Standgas & Teillast-Einstieg",
            "rpm_min": 1500,
            "rpm_max": 3200,
            "lambda_min": 0.895,
            "lambda_max": 0.930,
            "component": f"Nebendüse (ND {nd}) & Gemischschraube",
            "desc": "Leerlaufgemisch & unterer Schieberhub"
        },
        {
            "id": "zone2",
            "name": "Schieberhub & Übergang",
            "rpm_min": 3200,
            "rpm_max": 4800,
            "lambda_min": 0.881,
            "lambda_max": 0.909,
            "component": f"Gasschieber ({slide_label})",
            "desc": "Teillast (1/4 - 1/2 Gas) & Cutaway"
        },
        {
            "id": "zone3",
            "name": "Resonanz & Mischrohr",
            "rpm_min": 4800,
            "rpm_max": 6500,
            "lambda_min": 0.874,
            "lambda_max": 0.902,
            "component": f"Mischrohr ({tube}) & HLKD ({hlkd})",
            "desc": "Vor-Resonanz & Gemisch-Voremulgierung"
        },
        {
            "id": "zone4",
            "name": "Volllast & Peak Power",
            "rpm_min": 6500,
            "rpm_max": 9500,
            "lambda_min": 0.867,
            "lambda_max": 0.895,
            "component": f"Hauptdüse (HD {hd}) & Ansaugung",
            "desc": "Vollgas, Venturi-Strömung & thermischer Klemmschutz"
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

        if mean_afr > t_max + 1.2:
            status = "CRITICAL_LEAN"
            status_text = "🚨 KRITISCH MAGER"
            badge_class = "badge-danger"
            has_critical_lean = True
        elif mean_afr > t_max + 0.25:
            status = "LEAN"
            status_text = "⚠️ LEICHT MAGER"
            badge_class = "badge-warning"
            needs_tuning = True
        elif mean_afr < t_min - 0.7:
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
            if "LEAN" in status:
                advice = f"Gemischschraube 0.5 Umdrehungen herausdrehen (fetter). Falls AFR weiterhin > {t_max:.1f}, ND von {nd} auf fettere ND wechseln."
            elif status == "RICH":
                advice = f"Gemischschraube 0.5 Umdrehungen hineindrehen. Falls AFR < {t_min:.1f}, magerere ND wählen."
            else:
                advice = f"Nebendüse {nd} & Gemischschraube arbeiten im optimalen Lambda-Bereich (λ={lambda_measured:.2f})."

        elif zid == "zone2":
            if "LEAN" in status:
                if slide_key == "bgm_std_cutout":
                    advice = f"🚨 Magerloch durch großen BGM Standard-Cutaway! Empfehlung: Wechsel auf 'Lemarxon Mid Cutaway' oder 'Lemarxon Low Cutaway' für sicheren Teillast-Übergang."
                elif slide_key == "lemarxon_mid":
                    advice = f"Teillast leicht mager. Empfehlung: Wechsel auf 'Lemarxon Low Cutaway' (fetter) oder Gemischschraube weiter herausdrehen."
                else:
                    advice = f"Magerlauf trotz {slide_label}. Mischrohr ({tube}) und ND ({nd}) auf ausreichende Voremulgierung prüfen."
            elif status == "RICH":
                if slide_key == "lemarxon_low":
                    advice = f"Überfettet bei 1/4 bis 1/2 Gas. Wechsel auf 'Lemarxon Mid Cutaway' (etwas magerer) sorgt für agilere Gasannahme."
                else:
                    advice = f"Teillast überfettet leicht. Schieber mit größerem Cutaway (z.B. BGM Standard) oder magerere ND testen."
            else:
                advice = f"Gasschieber ({slide_label}) sorgt für einen sauberen, stempelfreien Teillastübergang (λ={lambda_measured:.2f})."

        elif zid == "zone3":
            if "LEAN" in status:
                advice = f"Magerlauf beim Eintritt in die Resonanz! HLKD von {hlkd} auf kleiner (140/150) reduzieren oder fetteres Mischrohr ({tube}) verbauen."
            elif status == "RICH":
                advice = f"Viertaktet vor Resonanzeintritt. HLKD vergrößern oder magereres Mischrohr wählen."
            else:
                advice = f"Mischrohr {tube} & HLKD {hlkd} versorgen den Motor im Resonanzeinstieg perfekt (λ={lambda_measured:.2f})."

        elif zid == "zone4":
            intake_note = ""
            if intake_key == "polini_venturi":
                intake_note = f" (Polini Venturi Trichter erfordert ca. +6 bis +10 HD-Größen gegenüber Serienfilter!)"
            elif intake_key == "lemarxon_22mm":
                intake_note = f" (22mm Lemarxon Hülse optimiert die Strömungsgeschwindigkeit)."

            if status == "CRITICAL_LEAN":
                advice = f"🚨 AKUTE KLEMMGEFAHR BEI VOLLGAS! Hauptdüse HD {hd} sofort um mind. +4 bis +6 Nummern vergrößern (z.B. HD {hd+4}/{hd+6})!{intake_note}"
            elif status == "LEAN":
                advice = f"Hauptdüse HD {hd} etwas zu mager. Empfehlung: HD um +2 bis +3 Nummern vergrößern (z.B. HD {hd+2}).{intake_note}"
            elif status == "RICH":
                advice = f"Motor drosselt obenraus / überfettet. HD {hd} um 2 Nummern verkleinern (z.B. HD {hd-2})."
            else:
                advice = f"Hauptdüse HD {hd} mit {intake_label} liefert maximale Leistung bei optimaler EGT-Kühlung (λ={lambda_measured:.2f})."

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
        "zones": evaluated_zones
    }

