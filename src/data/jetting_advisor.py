import os
import json
import numpy as np
import pandas as pd

try:
    from config import load_carb_setup
except ModuleNotFoundError:
    try:
        from src.config import load_carb_setup
    except ModuleNotFoundError:
        from ..config import load_carb_setup

def parse_nd_ratio(nd_str):
    """ Berechnet das Verhältnis der Nebendüse (z.B. 60/160 -> 160/60 = 2.67) """
    try:
        parts = str(nd_str).split('/')
        if len(parts) == 2:
            fuel = float(parts[0])
            air = float(parts[1])
            return air / fuel if fuel > 0 else 2.67
    except:
        pass
    return 2.67

def analyze_carb_jetting(df, carb_setup=None):
    """
    Analysiert das AFR-Kennfeld eines Dyno-Pulls oder Logs und gibt
    konkrete Bedüsungsempfehlungen für SI 24/24 Vergaser.
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
            "desc": "Vollgas, Maximalleistung & Hitzeschutz"
        }
    ]

    analyzed_zones = []
    total_warnings = 0
    danger_detected = False

    for z in zones_def:
        mask = (sub_df[rpm_col] >= z["rpm_min"]) & (sub_df[rpm_col] < z["rpm_max"])
        z_data = sub_df[mask]

        if len(z_data) >= 3:
            mean_afr = float(z_data[afr_col].mean())
            min_afr = float(z_data[afr_col].min())
            max_afr = float(z_data[afr_col].max())
            points = len(z_data)
        else:
            mean_afr = None
            min_afr = None
            max_afr = None
            points = 0

        if mean_afr is None:
            status = "NO_DATA"
            status_text = "Keine Daten"
            badge_class = "badge-neutral"
            advice = "In diesem Drehzahlbereich lagen keine Messpunkte vor."
            gauge_pct = 50
        else:
            gauge_pct = max(0, min(100, (mean_afr - 10.0) / 6.0 * 100))

            if mean_afr > z["target_max"] + 0.8:
                status = "CRITICAL_LEAN"
                status_text = "🚨 KRITISCH MAGER"
                badge_class = "badge-critical"
                total_warnings += 2
                danger_detected = True
                if z["id"] == "zone4":
                    advice = f"🚨 Klemmgefahr bei Vollgas! Hauptdüse HD von {hd} umgehend um +4 bis +6 Nummern vergrößern (z.B. HD {int(hd)+5})."
                elif z["id"] == "zone3":
                    advice = f"🚨 Starkes Magerloch vor Resonanz! HLKD von {hlkd} auf 140/150 verkleinern oder fetteres Mischrohr testen."
                elif z["id"] == "zone2":
                    advice = f"Magerer Übergang! Schieber mit kleinerem Cutaway wählen oder Gemischschraube weiter raus."
                else:
                    advice = f"Nebendüse {nd} deutlich zu mager! Fetteres ND-Verhältnis montieren (z.B. 58/140 oder 55/140)."

            elif mean_afr > z["target_max"]:
                status = "LEAN"
                status_text = "⚠️ LEICHT MAGER"
                badge_class = "badge-warn"
                total_warnings += 1
                if z["id"] == "zone4":
                    advice = f"Vollgas etwas zu mager. HD von {hd} um +2 bis +3 Nummern anheben (z.B. HD {int(hd)+3})."
                elif z["id"] == "zone3":
                    advice = f"HLKD von {hlkd} auf 140 verkleinern oder Mischrohr {tube} fetter abstimmen."
                elif z["id"] == "zone2":
                    advice = f"Schieber {slide} läuft leicht mager. Gemischschraube 1/2 Umdrehung rausdrehen."
                else:
                    advice = f"ND {nd} leicht mager. Gemischschraube 1/2 Umdrehung herausdrehen."

            elif mean_afr < z["target_min"] - 0.9:
                status = "TOO_RICH"
                status_text = "🔵 ZU FETT"
                badge_class = "badge-rich"
                total_warnings += 1
                if z["id"] == "zone4":
                    advice = f"Vollgas zu fett (Leistungsverlust & Stottern). HD von {hd} um -3 Nummern reduzieren (z.B. HD {int(hd)-3})."
                elif z["id"] == "zone3":
                    advice = f"Resonanzbereich überfettet. HLKD von {hlkd} auf 180 vergrößern."
                elif z["id"] == "zone2":
                    advice = f"Viertakten im Teillastbereich. Schieber mit größerem Cutaway verwenden."
                else:
                    advice = f"ND {nd} zu fett (unruhiges Standgas). Magerere ND montieren (z.B. 55/160) oder Gemischschraube 1/2 Umdrehung rein."

            elif mean_afr < z["target_min"]:
                status = "SLIGHTLY_RICH"
                status_text = "🔵 LEICHT FETT"
                badge_class = "badge-info"
                if z["id"] == "zone4":
                    advice = f"HD {hd} ist thermisch sehr sicher, bietet aber noch etwas Potenzial nach oben."
                else:
                    advice = "Gemisch liegt auf der sicheren, leicht fetten Seite."

            else:
                status = "OPTIMAL"
                status_text = "🟢 PERFEKT"
                badge_class = "badge-ok"
                advice = f"{z['component']} arbeitet optimal im Zielfenster ({z['target_min']:.1f} - {z['target_max']:.1f} AFR)."

        analyzed_zones.append({
            "id": z["id"],
            "name": z["name"],
            "rpm_range": f"{z['rpm_min']} - {z['rpm_max']} U/min",
            "component": z["component"],
            "desc": z["desc"],
            "target": f"{z['target_min']:.1f} - {z['target_max']:.1f}",
            "mean_afr": round(mean_afr, 2) if mean_afr is not None else None,
            "min_afr": round(min_afr, 2) if min_afr is not None else None,
            "max_afr": round(max_afr, 2) if max_afr is not None else None,
            "points": points,
            "status": status,
            "status_text": status_text,
            "badge_class": badge_class,
            "advice": advice,
            "gauge_pct": round(gauge_pct, 1)
        })

    avg_total_afr = float(sub_df[afr_col].mean())
    max_egt_val = float(sub_df[egt_col].max()) if egt_col and not sub_df[egt_col].isna().all() else None

    if danger_detected:
        overall_verdict = "🚨 ACHTUNG: Kritisches Gemisch in einem oder mehreren Lastbereichen. Bitte Bedüsung vor dem nächsten Vollgaslauf anpassen!"
        overall_status = "CRITICAL"
    elif total_warnings > 0:
        overall_verdict = "⚠️ Solide Basis mit Feintuning-Potenzial. Siehe detaillierte Empfehlungen unten."
        overall_status = "TUNE"
    else:
        overall_verdict = "🏆 Hervorragende Vergaserabstimmung! Das Gemisch liegt über das gesamte Drehzahlband im idealen Leistungsfenster."
        overall_status = "PERFECT"

    return {
        "valid": True,
        "carb_setup": carb_setup,
        "overall_status": overall_status,
        "overall_verdict": overall_verdict,
        "avg_total_afr": round(avg_total_afr, 2),
        "max_egt": round(max_egt_val, 1) if max_egt_val is not None else None,
        "zones": analyzed_zones
    }
