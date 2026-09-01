"""
StreetDyno 2.0 - Carburetor Jetting Advisor
Evaluates AFR air-fuel mixture across 4 operating regimes for Dell'Orto SI 24/24
carburetors and generates actionable mechanical jetting recommendations.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, List
import pandas as pd
from config import load_carb_setup


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


def analyze_carb_jetting(
    df: pd.DataFrame,
    carb_setup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Analyzes telemetry AFR across 4 carburetor operating regimes and
    outputs component recommendations for Dell'Orto SI 24/24.
    """
    if carb_setup is None:
        carb_setup = load_carb_setup()

    hd = carb_setup.get("main_jet_hd", 135)
    nd = carb_setup.get("idle_jet_nd", "60/160")
    hlkd = carb_setup.get("air_corrector_hlkd", 160)
    tube = carb_setup.get("emulsion_tube", "Lemarxon x234")
    slide = carb_setup.get("throttle_slide", "Lemarxon Low")
    venturi = carb_setup.get("intake_funnel", "Polini Venturi Trichter")

    rpm_col = "RPM_smoothed" if "RPM_smoothed" in df.columns else ("RPM" if "RPM" in df.columns else None)
    afr_col = "AFR" if "AFR" in df.columns else None
    egt_col = "EGT_cleaned" if "EGT_cleaned" in df.columns else ("EGT" if "EGT" in df.columns else None)

    if not rpm_col or not afr_col or df.empty:
        return {
            "valid": False,
            "error": "Unzureichende Telemetriedaten für Vergaseranalyse.",
            "carb_setup": carb_setup
        }

    valid_mask = (df[afr_col] >= 9.0) & (df[afr_col] <= 18.0) & (df[rpm_col] >= 1200)
    sub_df = df[valid_mask].copy()

    if len(sub_df) < 5:
        return {
            "valid": False,
            "error": "Zu wenige verwertbare AFR-Punkte im Pull.",
            "carb_setup": carb_setup
        }

    zones_def = [
        {
            "id": "zone1",
            "name": "Standgas & Teillast-Einstieg",
            "rpm_min": 1500,
            "rpm_max": 3200,
            "target_min": 12.8,
            "target_max": 13.3,
            "component": f"Nebendüse (ND {nd}) & Gemischschraube",
            "desc": "Leerlaufgemisch & unterer Schieberhub"
        },
        {
            "id": "zone2",
            "name": "Schieberhub & Übergang",
            "rpm_min": 3200,
            "rpm_max": 4800,
            "target_min": 12.6,
            "target_max": 13.0,
            "component": f"Gasschieber ({slide}) & Mischrohr-Bohrungen",
            "desc": "Teillast & Vor-Resonanz Übergang"
        },
        {
            "id": "zone3",
            "name": "Resonanz & Mischrohr",
            "rpm_min": 4800,
            "rpm_max": 6500,
            "target_min": 12.5,
            "target_max": 12.9,
            "component": f"Mischrohr ({tube}) & HLKD ({hlkd})",
            "desc": "Hauptdüseneinsatz & Auspuffresonanz"
        },
        {
            "id": "zone4",
            "name": "Volllast & Peak Power",
            "rpm_min": 6500,
            "rpm_max": 9500,
            "target_min": 12.4,
            "target_max": 12.8,
            "component": f"Hauptdüse (HD {hd})",
            "desc": "Vollgas & thermische EGT-Sicherheit"
        }
    ]

    evaluated_zones: List[Dict[str, Any]] = []
    has_critical_lean = False
    needs_tuning = False

    for z in zones_def:
        z_mask = (sub_df[rpm_col] >= z["rpm_min"]) & (sub_df[rpm_col] < z["rpm_max"])
        z_data = sub_df[z_mask]

        if len(z_data) < 2:
            evaluated_zones.append({
                "id": z["id"],
                "name": z["name"],
                "rpm_range": f"{z['rpm_min']}-{z['rpm_max']} U/min",
                "component": z["component"],
                "mean_afr": None,
                "target": f"{z['target_min']}-{z['target_max']}",
                "status": "NO_DATA",
                "status_text": "KEINE DATEN",
                "badge_class": "badge-secondary",
                "advice": "In diesem Drehzahlbereich lagen während des Pulls keine stabilen Messwerte vor.",
                "gauge_pct": 50
            })
            continue

        mean_afr = float(z_data[afr_col].mean())
        t_min = z["target_min"]
        t_max = z["target_max"]

        if mean_afr > t_max + 1.2:
            status = "CRITICAL_LEAN"
            status_text = "🚨 KRITISCH MAGER"
            badge_class = "badge-danger"
            has_critical_lean = True
        elif mean_afr > t_max + 0.3:
            status = "LEAN"
            status_text = "⚠️ LEICHT MAGER"
            badge_class = "badge-warning"
            needs_tuning = True
        elif mean_afr < t_min - 0.8:
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
                advice = f"Gemischschraube 0.5 Umdrehungen herausdrehen (fetter). Falls AFR weiterhin > {t_max}, ND von {nd} auf fettere ND wechseln."
            elif status == "RICH":
                advice = f"Gemischschraube 0.5 Umdrehungen hineindrehen. Falls AFR < {t_min}, magerere ND wählen."
            else:
                advice = f"Nebendüse {nd} & Gemischschraube arbeiten im optimalen Lambdafenster."

        elif zid == "zone2":
            if "LEAN" in status:
                advice = f"Magerer Schieber-Cutaway ({slide})! Gasschieber mit flacherem Cutaway verwenden oder Mischrohr mit tieferen Querbohrungen einsetzen."
            elif status == "RICH":
                advice = f"Überfettet bei 1/4 bis 1/2 Gas. Schieber mit größerem Cutaway verwenden."
            else:
                advice = f"Gasschieber {slide} sorgt für sauberen Übergang ohne Magerloch."

        elif zid == "zone3":
            if "LEAN" in status:
                advice = f"Magerlauf beim Eintritt in die Resonanz! HLKD von {hlkd} auf kleiner (z.B. 140/150) reduzieren oder fetteres Mischrohr ({tube}) verbauen."
            elif status == "RICH":
                advice = f"Viertaktet vor Resonanzeintritt. HLKD vergrößern oder magereres Mischrohr wählen."
            else:
                advice = f"Mischrohr {tube} & HLKD {hlkd} versorgen den Motor im Resonanzeinstieg perfekt."

        elif zid == "zone4":
            if status == "CRITICAL_LEAN":
                advice = f"🚨 AKUTE KLEMMGEFAHR BEI VOLLGAS! Hauptdüse HD {hd} sofort um mind. +4 bis +6 Nummern vergrößern (z.B. HD 140/142)!"
            elif status == "LEAN":
                advice = f"Hauptdüse HD {hd} etwas zu mager. Empfehlung: HD um +2 bis +3 Nummern vergrößern (z.B. HD 138)."
            elif status == "RICH":
                advice = f"Motor drosselt obenraus / überfettet. HD {hd} um 2 Nummern verkleinern (z.B. HD 132/134)."
            else:
                advice = f"Hauptdüse HD {hd} liefert maximale Leistung bei optimaler EGT-Innenkühlung."

        gauge_pct = int(max(0, min(100, ((mean_afr - 10.0) / 6.0) * 100)))

        evaluated_zones.append({
            "id": zid,
            "name": z["name"],
            "rpm_range": f"{z['rpm_min']}-{z['rpm_max']} U/min",
            "component": z["component"],
            "mean_afr": round(mean_afr, 2),
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
        overall_verdict = "🟢 OPTIMAL: Vergaser-Bedüsung perfekt abgestimmt über das gesamte Drehzahlband."

    max_egt = float(sub_df[egt_col].max()) if egt_col and not sub_df[egt_col].isna().all() else None
    avg_total_afr = float(sub_df[afr_col].mean())

    return {
        "valid": True,
        "overall_status": overall_status,
        "overall_verdict": overall_verdict,
        "avg_total_afr": round(avg_total_afr, 2),
        "max_egt": round(max_egt, 0) if max_egt else None,
        "carb_setup": carb_setup,
        "zones": evaluated_zones
    }
