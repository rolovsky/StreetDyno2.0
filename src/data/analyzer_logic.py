import os
import sys
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


def detect_dyno_pull(df, min_rpm=3000.0, min_duration_sec=1.0, drop_threshold=500.0):
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
                if drpm[i + j] <= 500.0:  # Minimum acceleration threshold
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
        plt.close()
    else:
        plt.show()


def export_to_google_sheets(df, creds_path="service_account.json", spreadsheet_name="Vespa_Dyno_Cloud", worksheet_name="RawData"):
    """
    Pushes the cleaned and calculated DataFrame directly to Google Sheets.
    """
    if not HAS_GSPREAD:
        print("[WARNING] 'gspread' oder 'google-auth' nicht installiert. Sheets-Export wird übersprungen.")
        return False
        
    try:
        if not os.path.exists(creds_path):
            print(f"[ERROR] Google Credentials nicht gefunden unter '{creds_path}'.")
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
        return True
        
    except Exception as e:
        print(f"[ERROR] Google Sheets Export failed: {e}")
        return False
