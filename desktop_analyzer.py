#!/usr/bin/env python3
"""
StreetDyno 2.0 - Desktop Analyzer
Post-processing, cleanup, power & torque computation, auto-trimming, and P4-style visual reporting.
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    plot_telemetry,
    export_to_google_sheets
)


def main():
    parser = argparse.ArgumentParser(description="StreetDyno 2.0 Post-Processing Tool")
    parser.add_argument('csv_file', help="Pfad zur StreetDyno Log-CSV-Datei")
    parser.add_argument('--plot-out', help="Pfad zum Speichern des Plots (z.B. plot.png). Ohne Angabe wird das interaktive Fenster geöffnet.")
    parser.add_argument('--export-sheets', action='store_true', help="Exportiert die berechneten Daten direkt nach Google Sheets")
    parser.add_argument('--sheet-name', default="Vespa_Dyno_Cloud", help="Name des Google Spreadsheets (Standard: Vespa_Dyno_Cloud)")
    
    # Auto-detection parameters
    parser.add_argument('--min-rpm', type=float, default=3000.0, help="Minimale Drehzahl fuer Pull-Start (Standard: 3000.0 U/min)")
    parser.add_argument('--min-duration', type=float, default=1.0, help="Mindestdauer an Beschleunigung in Sek. fuer Pull-Start (Standard: 1.0s)")
    parser.add_argument('--drop-threshold', type=float, default=500.0, help="Drehzahlabfall von Peak zur Pull-Ende Erkennung (Standard: 500.0 U/min)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.csv_file):
        print(f"[ERROR] Datei nicht gefunden: {args.csv_file}")
        sys.exit(1)
        
    print(f"Lese Daten aus: {args.csv_file}...")
    try:
        df = pd.read_csv(args.csv_file, sep=None, engine='python')
    except Exception as e:
        print(f"[ERROR] Fehler beim Lesen der CSV: {e}")
        sys.exit(1)
        
    col_mapping = {col: col.strip() for col in df.columns}
    df = df.rename(columns=col_mapping)
    
    required_cols = ['Time', 'RPM', 'AFR', 'EGT', 'Speed_kmh']
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        print(f"[ERROR] Fehlende Spalten in CSV: {missing_cols}")
        print(f"Vorhanden: {list(df.columns)}")
        sys.exit(1)
        
    print("Starte EGT-Datenbereinigung und Filter...")
    df = clean_egt_data(df)
    
    print("Berechne geglaettete Drehzahl und Ableitung...")
    df = calculate_telemetry_metrics(df)
    
    print("Suche nach gueltigem Dyno-Pull (dRPM/dt > 500 U/min/s)...")
    trimmed_df, detected = detect_dyno_pull(df, args.min_rpm, args.min_duration, args.drop_threshold)
    
    if detected:
        print(f"[OK] Dyno-Pull erfolgreich erkannt! Analysiere eingegrenzten Bereich ({len(trimmed_df)} Zeilen).")
        title_suffix = " (Automatisch getrimmt)"
    else:
        print("[WARNUNG] Kein gueltiger Dyno-Pull erkannt. Verwende gesamte Log-Datei.")
        trimmed_df = df
        title_suffix = " (Gesamtes Log)"
        
    # Print peak telemetry stats from the analyzed range (trimmed or full)
    print("-" * 40)
    print(f"Max RPM: {trimmed_df['RPM'].max():.0f} U/min")
    print(f"Max Leistung: {trimmed_df['PS'].max():.1f} PS")
    print(f"Max Drehmoment: {trimmed_df['Nm'].max():.1f} Nm")
    print(f"Max EGT (Gefiltert): {trimmed_df['EGT_cleaned'].max():.1f}°C")
    print("-" * 40)
    
    # Plotting
    plot_telemetry(trimmed_df, title_suffix, args.plot_out)
    
    # Export
    if args.export_sheets:
        export_to_google_sheets(trimmed_df, spreadsheet_name=args.sheet_name)


if __name__ == '__main__':
    main()
