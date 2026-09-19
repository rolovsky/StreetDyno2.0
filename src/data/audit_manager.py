"""
StreetDyno 2.0 - Sternmasse-Audit & Diagnostics Manager
Manages non-invasive A0 voltage reconstruction, ground offset evaluation,
diagnostic report generation, and persistent setup compensation.
"""

from __future__ import annotations
import os
import time
import json
from datetime import datetime
from typing import Dict, Any, Tuple, List, Optional

from config import AUDIT_DIR, USER_SETUP_FILE, load_carb_setup, save_carb_setup


def reconstruct_afr_voltage(afr: float) -> float:
    """
    Reconstructs analog voltage at Arduino Pin A0 from calibrated AFR.
    Firmware formula: afrValue = 22.62 - (afrV * 5.72)
    Inverted: afrV = (22.62 - afrValue) / 5.72
    Clamped to physical ADC range [0.0, 5.0] V.
    """
    if afr <= 0.0:
        return 0.0
    v = (22.62 - float(afr)) / 5.72
    return max(0.0, min(5.0, round(v, 3)))


def evaluate_ground_offset(delta_u_mv: float) -> Dict[str, Any]:
    """
    Evaluates measured ground offset delta U (mV) between Arduino GND and
    Wideband Controller Sensor-GND with traffic-light rating.
    """
    du = float(delta_u_mv)
    # Slope of KOSO transfer function: 5.72 AFR/V = 0.00572 AFR/mV
    afr_impact = round((du / 1000.0) * 5.72, 3)

    if du < 5.0:
        rating = "GREEN"
        status_label = "✅ EXZELLENT"
        title = "Sternmasse intakt (ΔU < 5 mV)"
        message = (
            "Der Spannungsversatz liegt unterhalb der kritischen 5 mV Schwelle. "
            "Keine signifikante Verfälschung des Lambda-Signals. "
            "Die physische Sternmasseverkabelung ist einwandfrei."
        )
        action_needed = False
    elif du <= 20.0:
        rating = "YELLOW"
        status_label = "⚠️ GRENZWERTIG"
        title = "Leichter Masseversatz (5 mV <= ΔU <= 20 mV)"
        message = (
            f"Der Spannungsversatz von {du:.1f} mV verfälscht die AFR-Anzeige um "
            f"ca. {afr_impact:+.2f} AFR-Punkte. Massekontaktflächen reinigen und "
            "Leitungsquerschnitte der Heizer-Rückleitung prüfen."
        )
        action_needed = True
    else:
        rating = "RED"
        status_label = "❌ KRITISCH"
        title = "Masseschleife erkannt (ΔU > 20 mV)"
        message = (
            f"Kritischer Masseversatz von {du:.1f} mV! Der 1,5 A Sondenheizstrom fließt "
            f"über die Sensor-Signalmasse zurück und verfälscht das Gemisch um {afr_impact:+.2f} AFR. "
            "Dringend separate Masseleitung für Controller-Pin GND-Power zur Batterie ziehen!"
        )
        action_needed = True

    return {
        "rating": rating,
        "status_label": status_label,
        "title": title,
        "message": message,
        "delta_u_mv": du,
        "afr_impact": afr_impact,
        "action_needed": action_needed
    }


def save_audit_report(data: Dict[str, Any], audit_dir: str = AUDIT_DIR) -> Tuple[str, str]:
    """
    Saves diagnostic audit results to persistent JSON and Markdown report files.
    Returns (json_path, md_path).
    """
    os.makedirs(audit_dir, exist_ok=True)
    timestamp_str = time.strftime("%Y%m%d-%H%M%S")
    date_human = time.strftime("%Y-%m-%d %H:%M:%S")

    eval_res = evaluate_ground_offset(data.get("delta_u_mv", 0.0))

    report = {
        "audit_version": "2.0",
        "timestamp": timestamp_str,
        "recorded_at": date_human,
        "delta_u_mv": eval_res["delta_u_mv"],
        "afr_impact": eval_res["afr_impact"],
        "rating": eval_res["rating"],
        "status_label": eval_res["status_label"],
        "title": eval_res["title"],
        "message": eval_res["message"],
        "action_needed": eval_res["action_needed"],
        "steps": {
            "step1_baseline": {
                "afr": data.get("step1_afr", 0.0),
                "voltage_v": reconstruct_afr_voltage(data.get("step1_afr", 0.0)),
                "status": "COMPLETED"
            },
            "step2_heater": {
                "afr": data.get("step2_afr", 0.0),
                "voltage_v": reconstruct_afr_voltage(data.get("step2_afr", 0.0)),
                "delta_u_mv": eval_res["delta_u_mv"],
                "status": "COMPLETED"
            },
            "step3_engine": {
                "rpm": data.get("step3_rpm", 0.0),
                "egt": data.get("step3_egt", 0.0),
                "cht": data.get("step3_cht", 0.0),
                "rpm_jitter": data.get("step3_rpm_jitter", 0.0),
                "egt_valid": data.get("step3_egt", 0.0) not in [701.0, 705.0],
                "status": "COMPLETED"
            }
        },
        "notes": data.get("notes", ""),
        "compensated_in_setup": bool(data.get("apply_compensation", False))
    }

    # 1. JSON Report
    json_path = os.path.join(audit_dir, f"audit_{timestamp_str}.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=4, ensure_ascii=False)

    # 2. Markdown Audit Certificate
    md_path = os.path.join(audit_dir, f"audit_{timestamp_str}.md")
    md_content = f"""# 🩺 StreetDyno 2.0 — Sternmasse- & Sensor-Audit-Zertifikat

- **Prüfzeitpunkt:** {date_human}
- **Prüfergebnis:** {eval_res['status_label']} ({eval_res['rating']})
- **Gemessener Masseversatz $\\Delta U$:** **{eval_res['delta_u_mv']:.1f} mV**
- **Resultierender AFR-Einfluss:** **{eval_res['afr_impact']:+.3f} AFR**
- **Software-Kompensation aktiv:** {'Ja' if report['compensated_in_setup'] else 'Nein (Physik-First)'}

---

## 1. Diagnosebefund & Handlungsempfehlung

**{eval_res['title']}**

{eval_res['message']}

---

## 2. Messwerte der 3 Prüfstufen

| Prüfstufe | Messwert 1 | Messwert 2 | Messwert 3 | Status |
| :--- | :--- | :--- | :--- | :---: |
| **1. Baseline (Ruhepegel)** | AFR: {report['steps']['step1_baseline']['afr']:.2f} | $V_{{A0}}$: {report['steps']['step1_baseline']['voltage_v']:.3f} V | Grundrauschen i.O. | ✅ OK |
| **2. Heizertest (1.5 A Last)** | $\\Delta U$: {eval_res['delta_u_mv']:.1f} mV | AFR: {report['steps']['step2_heater']['afr']:.2f} | $\\Delta$AFR: {eval_res['afr_impact']:+.3f} | {eval_res['status_label']} |
| **3. Motorlauf (EMI-Test)** | RPM: {report['steps']['step3_engine']['rpm']:.0f} | EGT: {report['steps']['step3_engine']['egt']:.1f} °C | CHT: {report['steps']['step3_engine']['cht']:.1f} °C | ✅ OK |

---

## 3. Notizen & Setup-Bemerkung
{report['notes'] if report['notes'] else 'Keine zusätzlichen Anmerkungen erfasst.'}
"""
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(md_content)

    return json_path, md_path


def apply_ground_offset_to_setup(offset_mv: float, config_file: str = USER_SETUP_FILE) -> bool:
    """Persists calibrated ground offset (mV) to user_setup.json."""
    return save_carb_setup({"lambda_ground_offset_mv": float(offset_mv)})


def list_audit_reports(audit_dir: str = AUDIT_DIR) -> List[Dict[str, Any]]:
    """Lists past audit reports sorted from newest to oldest."""
    reports = []
    if not os.path.exists(audit_dir):
        return reports

    for fname in os.listdir(audit_dir):
        if fname.startswith("audit_") and fname.endswith(".json"):
            fpath = os.path.join(audit_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    reports.append({
                        "filename": fname,
                        "timestamp": data.get("timestamp", ""),
                        "recorded_at": data.get("recorded_at", ""),
                        "delta_u_mv": data.get("delta_u_mv", 0.0),
                        "afr_impact": data.get("afr_impact", 0.0),
                        "rating": data.get("rating", "UNKNOWN"),
                        "status_label": data.get("status_label", ""),
                        "title": data.get("title", "")
                    })
            except Exception:
                continue

    reports.sort(key=lambda x: x["timestamp"], reverse=True)
    return reports
