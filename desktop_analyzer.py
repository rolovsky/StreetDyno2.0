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

# Try to import scipy for Savitzky-Golay filtering, fallback to rolling window if missing
try:
    from scipy.signal import savgol_filter
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# Try to import gspread for cloud integration
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False


def clean_egt_data(df):
    """
    Cleans system-related EGT measurement errors (spikes at exactly 701°C or 705°C
    or sudden jumps > 50°C per timestep) by holding the last valid value.
    """
    cleaned_egt = []
    
    # Find the first valid value that isn't a spike
    last_valid_egt = None
    for val in df['EGT']:
        if val not in [701.0, 705.0] and val > 0:
            last_valid_egt = val
            break
            
    if last_valid_egt is None:
        last_valid_egt = 0.0  # Fallback if all values are spikes or empty

    for val in df['EGT']:
        # Plausibility check: spikes or difference > 50°C from last valid value
        if val in [701.0, 705.0] or abs(val - last_valid_egt) > 50.0:
            cleaned_egt.append(last_valid_egt)
        else:
            cleaned_egt.append(val)
            last_valid_egt = val
            
    df['EGT_cleaned'] = cleaned_egt
    return df


def calculate_telemetry_metrics(df):
    """
    Applies smoothing and calculates Power (PS) and Torque (Nm).
    """
    # 1. Smoothing RPM
    if HAS_SCIPY:
        # Savitzky-Golay: window size must be odd. At 10Hz, window length of 15 is 1.5s.
        # We ensure window_length doesn't exceed dataframe size.
        window_len = min(15, len(df) - (1 if len(df) % 2 == 0 else 0))
        if window_len < 3:
            window_len = 3
        df['RPM_smoothed'] = savgol_filter(df['RPM'], window_length=window_len, polyorder=2)
    else:
        # Fallback to Hanning window convolution
        window_size = 11
        if len(df) >= window_size:
            window = np.hanning(window_size)
            window = window / window.sum()
            padded = np.pad(df['RPM'], pad_width=window_size//2, mode='edge')
            df['RPM_smoothed'] = np.convolve(padded, window, mode='valid')[:len(df)]
        else:
            df['RPM_smoothed'] = df['RPM'].rolling(window=3, min_periods=1, center=True).mean()

    # 2. Derive dRPM/dt (dt = 0.1s at 10Hz)
    df['dRPM_dt'] = df['RPM_smoothed'].diff() / 0.1
    df['dRPM_dt'] = df['dRPM_dt'].fillna(0.0)

    # 3. Calculate Power (PS)
    # PS = (RPM * (dRPM / 0.1)) / 175000.0
    df['PS'] = (df['RPM_smoothed'] * df['dRPM_dt']) / 175000.0
    df['PS'] = df['PS'].clip(lower=0.0)

    # 4. Calculate Torque (Nm)
    # Nm = (PS * 7023.5) / RPM
    df['Nm'] = np.where(
        (df['RPM_smoothed'] > 500) & (df['PS'] > 0),
        (df['PS'] * 7023.5) / df['RPM_smoothed'],
        0.0
    )

    return df


def detect_dyno_pull(df, min_rpm, min_duration_sec, drop_threshold):
    """
    Detects the start and end of a dyno pull.
    Start: RPM > min_rpm AND dRPM/dt > 500 U/min/s sustained for at least min_duration_sec.
    End: RPM drops by more than drop_threshold from the peak of the pull.
    Returns: (trimmed_df, is_detected)
    """
    n_samples_required = int(min_duration_sec / 0.1)  # 10Hz sampling
    if len(df) < n_samples_required:
        return df, False

    rpm = df['RPM_smoothed'].values
    drpm = df['dRPM_dt'].values
    
    start_idx = None
    end_idx = None
    
    # 1. Search for start of the pull
    for i in range(len(df) - n_samples_required):
        if rpm[i] >= min_rpm:
            sustained = True
            for j in range(n_samples_required):
                if drpm[i + j] <= 500.0: # Minimum acceleration threshold to filter out cruising
                    sustained = False
                    break
            if sustained:
                start_idx = i
                break
                
    if start_idx is None:
        return df, False
        
    # 2. Search for end of the pull
    peak_rpm = rpm[start_idx]
    
    for i in range(start_idx, len(df)):
        if rpm[i] > peak_rpm:
            peak_rpm = rpm[i]
        
        if peak_rpm - rpm[i] > drop_threshold:
            end_idx = i
            break
            
    if end_idx is None:
        end_idx = len(df) - 1
        
    trimmed_df = df.iloc[start_idx:end_idx + 1].copy()
    return trimmed_df, True


def plot_telemetry(df, title_suffix="", output_path=None):
    """
    Plots the telemetry data in professional dark 'P4-Look'.
    """
    plt.style.use('dark_background')
    
    fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(12, 10), sharex=True, 
                                   gridspec_kw={'height_ratios': [2, 1]})
    
    ax2 = ax1.twinx()  # Secondary Y-axis for Torque
    
    # Plot curves
    p1, = ax1.plot(df['RPM_smoothed'], df['PS'], color='#00ffcc', linewidth=2.5, label='Leistung (PS)')
    p2, = ax2.plot(df['RPM_smoothed'], df['Nm'], color='#ff9800', linewidth=2.5, label='Drehmoment (Nm)')
    
    ax1.grid(True, color='#333333', linestyle='--', alpha=0.7)
    
    max_ps = df['PS'].max()
    max_nm = df['Nm'].max()
    
    ax1.set_ylim(0, max_ps * 1.15 if max_ps > 0 else 30)
    ax2.set_ylim(0, max_nm * 1.6 if max_nm > 0 else 40)
    
    ax1.set_title(f'StreetDyno 2.0 - Leistungsmessung{title_suffix}', fontsize=14, fontweight='bold', pad=15, color='#ffffff')
    ax1.set_ylabel('Leistung [PS]', color='#00ffcc', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Drehmoment [Nm]', color='#ff9800', fontsize=12, fontweight='bold')
    
    ax1.tick_params(axis='y', colors='#00ffcc')
    ax2.tick_params(axis='y', colors='#ff9800')
    
    peak_ps_idx = df['PS'].idxmax()
    peak_ps = df['PS'].max()
    peak_ps_rpm = df.loc[peak_ps_idx, 'RPM_smoothed']
    
    peak_nm_idx = df['Nm'].idxmax()
    peak_nm = df['Nm'].max()
    peak_nm_rpm = df.loc[peak_nm_idx, 'RPM_smoothed']
    
    annotation_text = (f"Peak Leistung: {peak_ps:.1f} PS @ {int(peak_ps_rpm)} U/min\n"
                       f"Peak Drehmoment: {peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min")
    
    ax1.text(0.02, 0.95, annotation_text, transform=ax1.transAxes, fontsize=10,
             bbox=dict(boxstyle='round', facecolor='#222222', alpha=0.8, edgecolor='#444444'))

    ax4 = ax3.twinx()  # Secondary Y-axis for EGT
    
    p3, = ax3.plot(df['RPM_smoothed'], df['AFR'], color='#ff3366', linewidth=2.0, label='AFR')
    p4, = ax4.plot(df['RPM_smoothed'], df['EGT_cleaned'], color='#ffcc00', linewidth=2.0, label='EGT (°C)')
    
    ax3.axhline(13.0, color='#ff3366', linestyle=':', alpha=0.5, label='Optimal AFR Last (13.0)')
    ax4.axhline(630.0, color='#ffcc00', linestyle=':', alpha=0.5, label='Kritische EGT (630°C)')
    
    ax3.grid(True, color='#333333', linestyle='--', alpha=0.7)
    
    ax3.set_ylabel('AFR', color='#ff3366', fontsize=12, fontweight='bold')
    ax4.set_ylabel('EGT [°C]', color='#ffcc00', fontsize=12, fontweight='bold')
    ax3.set_xlabel('Motordrehzahl [U/min]', fontsize=12, fontweight='bold')
    
    ax3.tick_params(axis='y', colors='#ff3366')
    ax4.tick_params(axis='y', colors='#ffcc00')
    
    ax3.set_ylim(10, 17)
    ax4.set_ylim(400, 750)
    
    ax3.axvspan(3000, 5000, color='#ffffff', alpha=0.07, label='Critical SI-Carb Range (3k-5k)')
    
    lines1 = [p1, p2]
    ax1.legend(lines1, [l.get_label() for l in lines1], loc='upper right')
    
    lines2 = [p3, p4]
    ax3.legend(lines2, [l.get_label() for l in lines2], loc='upper right')
    
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"[OK] Diagramm gespeichert unter: {output_path}")
    else:
        plt.show()


def export_to_google_sheets(df, spreadsheet_name="Vespa_Dyno_Cloud", worksheet_name="RawData"):
    """
    Pushes the cleaned and calculated DataFrame directly to Google Sheets.
    """
    if not HAS_GSPREAD:
        print("[WARNING] 'gspread' oder 'google-auth' nicht installiert. Sheets-Export wird übersprungen.")
        print("Installation mit: pip install gspread google-auth")
        return False
        
    try:
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
        
        if not os.path.exists(creds_path):
            print(f"[ERROR] Google Service Account Credentials nicht gefunden unter '{creds_path}'.")
            return False
            
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        
        try:
            sh = client.open(spreadsheet_name)
        except gspread.exceptions.SpreadsheetNotFound:
            print(f"[INFO] Spreadsheet '{spreadsheet_name}' nicht gefunden. Erstelle neues Spreadsheet...")
            sh = client.create(spreadsheet_name)
            
        try:
            worksheet = sh.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=worksheet_name, rows="100", cols="20")
            
        df_export = df.copy()
        if 'Time' not in df_export.columns:
            df_export.reset_index(inplace=True)
            
        cols_to_export = ['Time', 'RPM', 'RPM_smoothed', 'AFR', 'EGT', 'EGT_cleaned', 'Speed_kmh', 'PS', 'Nm']
        cols_to_export = [c for c in cols_to_export if c in df_export.columns]
        
        df_export = df_export[cols_to_export].fillna("")
        
        worksheet.clear()
        worksheet.update([df_export.columns.values.tolist()] + df_export.values.tolist())
        print(f"[OK] {len(df_export)} Datensätze erfolgreich in Google Sheet '{spreadsheet_name}' -> '{worksheet_name}' exportiert!")
        return True
        
    except Exception as e:
        print(f"[ERROR] Google Sheets Export failed: {e}")
        return False


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
