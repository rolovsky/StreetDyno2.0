"""
StreetDyno 2.0 - Blackbox Trip Analyzer & 2D AFR Heatmap Engine
Analyzes continuous background riding sessions, calculates 2D RPM/Speed AFR matrices,
EGT profiles, trip statistics, and whole-ride carburetor jetting diagnostics.
"""

from __future__ import annotations
import math
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from config import load_carb_setup, FUEL_STOICHIOMETRY
from data.jetting_advisor import analyze_carb_jetting


def calculate_gps_distance_km(df: pd.DataFrame) -> float:
    """Calculates total trip distance in kilometers using GPS Haversine and fallback speed integration."""
    if 'Lat' in df.columns and 'Lon' in df.columns:
        valid_gps = df[(df['Lat'] != 0.0) & (df['Lon'] != 0.0) & (df.get('GPS_Fix', True) == True)]
        if len(valid_gps) >= 2:
            lats = np.radians(valid_gps['Lat'].values)
            lons = np.radians(valid_gps['Lon'].values)
            dlat = lats[1:] - lats[:-1]
            dlon = lons[1:] - lons[:-1]
            a = np.sin(dlat / 2.0)**2 + np.cos(lats[:-1]) * np.cos(lats[1:]) * np.sin(dlon / 2.0)**2
            c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
            dist_km = float(np.sum(6371.0 * c))
            if dist_km > 0.01:
                return round(dist_km, 2)

    # Fallback to speed integration: sum(Speed_kmh * dt)
    if 'Speed_kmh' in df.columns and len(df) > 1:
        avg_speed = df['Speed_kmh'].mean()
        duration_hours = (len(df) * 0.1) / 3600.0
        return round(float(avg_speed * duration_hours), 2)

    return 0.0


def generate_afr_heatmap_matrix(df: pd.DataFrame, stoich_afr: float = 14.30) -> Dict[str, Any]:
    """
    Computes a 2D RPM x Speed AFR Heatmap Matrix across the entire trip.
    Bins:
      - RPM Bins: 1000-2500, 2500-3500, 3500-4500, 4500-5500, 5500-6500, >6500
      - Speed Bins: 0-15, 15-30, 30-45, 45-60, 60-75, >75 km/h
    """
    rpm_bins = [
        {"id": "rpm_1000_2500", "label": "1000-2500 U/min", "min": 1000.0, "max": 2500.0, "desc": "Leerlauf / Anfahren"},
        {"id": "rpm_2500_3500", "label": "2500-3500 U/min", "min": 2500.0, "max": 3500.0, "desc": "Teillast 1 (ND & Schraube)"},
        {"id": "rpm_3500_4500", "label": "3500-4500 U/min", "min": 3500.0, "max": 4500.0, "desc": "Teillast 2 (Schieberhub)"},
        {"id": "rpm_4500_5500", "label": "4500-5500 U/min", "min": 4500.0, "max": 5500.0, "desc": "Vor-Resonanz (Mischrohr)"},
        {"id": "rpm_5500_6500", "label": "5500-6500 U/min", "min": 5500.0, "max": 6500.0, "desc": "Resonanzeinstieg"},
        {"id": "rpm_6500_plus", "label": ">6500 U/min", "min": 6500.0, "max": 12000.0, "desc": "Volllast / WOT (HD)"}
    ]

    spd_bins = [
        {"id": "spd_0_15", "label": "0-15 km/h", "min": 0.0, "max": 15.0, "desc": "Stand / Stop&Go"},
        {"id": "spd_15_30", "label": "15-30 km/h", "min": 15.0, "max": 30.0, "desc": "Stadt langsam"},
        {"id": "spd_30_45", "label": "30-45 km/h", "min": 30.0, "max": 45.0, "desc": "Teillast Cruising"},
        {"id": "spd_45_60", "label": "45-60 km/h", "min": 45.0, "max": 60.0, "desc": "Landstraße"},
        {"id": "spd_60_75", "label": "60-75 km/h", "min": 60.0, "max": 75.0, "desc": "Zügig"},
        {"id": "spd_75_plus", "label": ">75 km/h", "min": 75.0, "max": 180.0, "desc": "Highspeed / WOT"}
    ]

    # Filter valid telemetry
    valid_df = df[(df['RPM'] >= 800) & (df['AFR'] >= 9.0) & (df['AFR'] <= 20.0)].copy()

    matrix_rows: List[Dict[str, Any]] = []

    for r_bin in rpm_bins:
        cells: List[Dict[str, Any]] = []
        r_mask = (valid_df['RPM'] >= r_bin['min']) & (valid_df['RPM'] < r_bin['max'])
        r_df = valid_df[r_mask]

        for s_bin in spd_bins:
            s_mask = (r_df['Speed_kmh'] >= s_bin['min']) & (r_df['Speed_kmh'] < s_bin['max'])
            cell_df = r_df[s_mask]

            count = len(cell_df)
            if count > 0:
                avg_afr = float(cell_df['AFR'].mean())
                avg_egt = float(cell_df['EGT'].mean()) if 'EGT' in cell_df.columns else 0.0
                min_afr = float(cell_df['AFR'].min())
                max_afr = float(cell_df['AFR'].max())

                # Color classification
                if avg_afr > 15.0:
                    status_class = "cell-critical"  # Red 🚨
                    bg_color = "rgba(255, 23, 68, 0.85)"
                    text_color = "#ffffff"
                    status_label = "Magerloch"
                elif avg_afr > 14.2:
                    status_class = "cell-warning"   # Orange ⚠️
                    bg_color = "rgba(255, 152, 0, 0.85)"
                    text_color = "#000000"
                    status_label = "Mager"
                elif avg_afr < 12.0:
                    status_class = "cell-rich"      # Blue 🔵
                    bg_color = "rgba(41, 182, 246, 0.85)"
                    text_color = "#000000"
                    status_label = "Fett"
                else:
                    status_class = "cell-optimal"   # Green 🟢
                    bg_color = "rgba(0, 230, 118, 0.85)"
                    text_color = "#000000"
                    status_label = "Optimal"

                cell_data = {
                    "count": count,
                    "avg_afr": round(avg_afr, 2),
                    "avg_egt": round(avg_egt, 0),
                    "min_afr": round(min_afr, 1),
                    "max_afr": round(max_afr, 1),
                    "bg_color": bg_color,
                    "text_color": text_color,
                    "status_class": status_class,
                    "status_label": status_label
                }
            else:
                cell_data = {
                    "count": 0,
                    "avg_afr": None,
                    "avg_egt": None,
                    "bg_color": "rgba(25, 25, 30, 0.5)",
                    "text_color": "#555566",
                    "status_class": "cell-empty",
                    "status_label": "Keine Daten"
                }

            cells.append(cell_data)

        matrix_rows.append({
            "rpm_label": r_bin["label"],
            "rpm_desc": r_bin["desc"],
            "cells": cells
        })

    return {
        "speed_columns": spd_bins,
        "rows": matrix_rows
    }


def analyze_trip_session(
    df: pd.DataFrame,
    carb_setup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Performs full analytics on a continuous background trip session.
    """
    if carb_setup is None:
        carb_setup = load_carb_setup()

    fuel_type = carb_setup.get("fuel_type", "Super_E5")
    stoich_afr = FUEL_STOICHIOMETRY.get(fuel_type, 14.30)

    if df.empty or 'RPM' not in df.columns or 'AFR' not in df.columns:
        return {
            "valid": False,
            "error": "Ungültige oder leere Telemetriedaten."
        }

    total_samples = len(df)
    duration_sec = round(total_samples * 0.1, 1)
    duration_min = round(duration_sec / 60.0, 1)

    distance_km = calculate_gps_distance_km(df)

    valid_mask = (df['RPM'] >= 500) & (df['AFR'] >= 9.0) & (df['AFR'] <= 20.0)
    vdf = df[valid_mask]

    if len(vdf) < 10:
        return {
            "valid": False,
            "error": "Zu wenige aktive Fahrdaten im Trip."
        }

    max_rpm = float(vdf['RPM'].max())
    avg_rpm = float(vdf['RPM'].mean())
    max_speed = float(vdf['Speed_kmh'].max()) if 'Speed_kmh' in vdf.columns else 0.0
    avg_speed = float(vdf['Speed_kmh'].mean()) if 'Speed_kmh' in vdf.columns else 0.0

    avg_afr = float(vdf['AFR'].mean())
    min_afr = float(vdf['AFR'].min())
    max_afr = float(vdf['AFR'].max())

    max_egt = float(vdf['EGT'].max()) if 'EGT' in vdf.columns and not vdf['EGT'].isna().all() else 0.0
    avg_egt = float(vdf['EGT'].mean()) if 'EGT' in vdf.columns and not vdf['EGT'].isna().all() else 0.0

    max_cht = float(vdf['CHT'].max()) if 'CHT' in vdf.columns and not vdf['CHT'].isna().all() else 0.0
    avg_cht = float(vdf['CHT'].mean()) if 'CHT' in vdf.columns and not vdf['CHT'].isna().all() else 0.0

    # 2D Heatmap Matrix
    heatmap = generate_afr_heatmap_matrix(df, stoich_afr=stoich_afr)

    # Whole-Trip Carburetor Diagnostics
    carb_diag = analyze_carb_jetting(df, carb_setup)

    # Time series downsampling for smooth web Chart.js rendering (max 800 points)
    step = max(1, len(df) // 800)
    sub = df.iloc[::step].copy()

    chart_timeline = {
        "time": sub['Time'].tolist() if 'Time' in sub.columns else [f"{i*0.1:.1f}s" for i in range(len(sub))],
        "rpm": sub['RPM'].round(0).tolist(),
        "speed": sub['Speed_kmh'].round(1).tolist() if 'Speed_kmh' in sub.columns else [],
        "afr": sub['AFR'].round(2).tolist(),
        "egt": sub['EGT'].round(1).tolist() if 'EGT' in sub.columns else [],
        "cht": sub['CHT'].round(1).tolist() if 'CHT' in sub.columns else []
    }

    return {
        "valid": True,
        "total_samples": total_samples,
        "duration_sec": duration_sec,
        "duration_min": duration_min,
        "distance_km": distance_km,
        "max_rpm": round(max_rpm, 0),
        "avg_rpm": round(avg_rpm, 0),
        "max_speed": round(max_speed, 1),
        "avg_speed": round(avg_speed, 1),
        "avg_afr": round(avg_afr, 2),
        "min_afr": round(min_afr, 2),
        "max_afr": round(max_afr, 2),
        "max_egt": round(max_egt, 1),
        "avg_egt": round(avg_egt, 1),
        "max_cht": round(max_cht, 1),
        "avg_cht": round(avg_cht, 1),
        "fuel_type": fuel_type,
        "stoich_afr": stoich_afr,
        "carb_setup": carb_setup,
        "carb_diag": carb_diag,
        "heatmap": heatmap,
        "chart_timeline": chart_timeline
    }
