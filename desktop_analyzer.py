#!/usr/bin/env python3
"""
StreetDyno 2.0 - Desktop Analyzer CLI
Post-processing, cleanup, power & torque computation, auto-trimming,
and P4-style visual reporting for macOS and Desktop systems.
"""

from __future__ import annotations
import os
import sys
import argparse
import pandas as pd

# Add src to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from src.data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    plot_telemetry,
    export_to_google_sheets
)
from src.data.jetting_advisor import analyze_carb_jetting
from src.config import load_carb_setup


def main() -> None:
    parser = argparse.ArgumentParser(description="StreetDyno 2.0 Desktop Telemetry Post-Processor")
    parser.add_argument('csv_file', help="Pfad zur StreetDyno Log-CSV-Datei")
    parser.add_argument('--plot-out', help="Pfad zum Speichern des Plots (z.B. plot.png)")
    parser.add_argument('--export-sheets', action='store_true', help="Exportiert die Daten nach Google Sheets")
    parser.add_argument('--sheet-name', default="Vespa_Dyno_Cloud", help="Name des Google Spreadsheets")
    parser.add_argument('--slope', default="auto", help="Strassensteigung in %% oder 'auto' (Standard: auto)")
    parser.add_argument('--temp', type=float, default=20.0, help="Umgebungstemperatur in °C (Standard: 20.0)")
    parser.add_argument('--pressure', type=float, default=1013.25, help="Luftdruck in hPa (Standard: 1013.25)")
    parser.add_argument('--norm', default="DIN70020", choices=["DIN70020", "SAE_J1349", "RAW"], help="Normierungsstandard")

    args = parser.parse_args()

    if not os.path.exists(args.csv_file):
        print(f"[ERROR] Datei nicht gefunden: {args.csv_file}")
        sys.exit(1)

    print(f"Lese Daten aus: {args.csv_file}...")
    try:
        df = pd.read_csv(args.csv_file)
    except Exception as e:
        print(f"[ERROR] Fehler beim Lesen der CSV: {e}")
        sys.exit(1)

    col_mapping = {col: col.strip() for col in df.columns}
    df = df.rename(columns=col_mapping)

    required_cols = ['Time', 'RPM', 'AFR', 'EGT', 'Speed_kmh']
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        print(f"[ERROR] Fehlende Spalten in CSV: {missing_cols}")
        sys.exit(1)

    print("Bereinige EGT-Sensordaten und berechne Fahrphysik...")
    df = clean_egt_data(df)
    df = calculate_telemetry_metrics(
        df,
        slope_percent=args.slope,
        temp_c=args.temp,
        pressure_hpa=args.pressure,
        norm_standard=args.norm
    )

    print("Erkenne Dyno-Beschleunigungszug...")
    trimmed_df, detected = detect_dyno_pull(
        df,
        slope_percent=args.slope,
        temp_c=args.temp,
        pressure_hpa=args.pressure,
        norm_standard=args.norm
    )

    if detected:
        gear = int(trimmed_df.get('Detected_Gear', pd.Series([3])).iloc[0]) if 'Detected_Gear' in trimmed_df.columns else 3
        print(f"[OK] Dyno-Pull erkannt ({gear}. Gang, {len(trimmed_df)} Datenpunkte).")
        title_suffix = f" (🎯 {gear}. Gang - Automatisch getrimmt)"
    else:
        print("[WARNUNG] Kein isolierter Pull erkannt. Verwende gesamtes Log.")
        trimmed_df = df
        title_suffix = " (Gesamtes Log)"

    # Print Summary Table
    max_ps = float(trimmed_df['PS'].max()) if 'PS' in trimmed_df.columns else 0.0
    max_ps_raw = float(trimmed_df['PS_Raw'].max()) if 'PS_Raw' in trimmed_df.columns else max_ps
    max_nm = float(trimmed_df['Nm'].max()) if 'Nm' in trimmed_df.columns else 0.0
    k_norm = float(trimmed_df['Weather_K_Norm'].iloc[0]) if 'Weather_K_Norm' in trimmed_df.columns else 1.0

    print("=" * 50)
    print(f"  Pmax ({args.norm}):    {max_ps:.1f} PS  (Unkorrigiert: {max_ps_raw:.1f} PS)")
    print(f"  Mmax ({args.norm}):    {max_nm:.1f} Nm")
    print(f"  Korrekturfaktor k:  {k_norm:.3f} ({((k_norm-1.0)*100):+.1f}%)")
    print(f"  Mittlerer AFR:      {trimmed_df['AFR'].mean():.2f}")
    print(f"  Peak EGT:           {trimmed_df['EGT_cleaned'].max():.0f}°C")
    print("=" * 50)

    # Run Carburetor Jetting Diagnosis
    carb = load_carb_setup()
    carb_diag = analyze_carb_jetting(trimmed_df, carb)
    if carb_diag.get("valid"):
        print(f"\n🔬 VERGASER-DIAGNOSE: {carb_diag.get('overall_verdict')}")
        for z in carb_diag.get("zones", []):
            print(f"  [{z['status_text']}] {z['name']}: AFR {z['mean_afr']} -> {z['advice']}")

    # Plotting
    plot_telemetry(trimmed_df, title_suffix, args.plot_out)

    # Sheets Export
    if args.export_sheets:
        export_to_google_sheets(trimmed_df, spreadsheet_name=args.sheet_name)


if __name__ == '__main__':
    main()
