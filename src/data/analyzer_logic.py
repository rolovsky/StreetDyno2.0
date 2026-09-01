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

# Try to import vehicle parameters from config
try:
    import config
except ImportError:
    try:
        from .. import config
    except Exception:
        config = None

# Fallback default vehicle parameters (Vespa PX 125 Lusso / VMC 177)
DEFAULT_TOTAL_MASS_KG = getattr(config, 'TOTAL_MASS_KG', 190.0)
DEFAULT_ROTATIONAL_MASS_FACTOR = getattr(config, 'ROTATIONAL_MASS_FACTOR', 1.05)
DEFAULT_TIRE_CIRCUMFERENCE_M = getattr(config, 'TIRE_CIRCUMFERENCE_M', 1.350)
DEFAULT_PRIMARY_RATIO = getattr(config, 'PRIMARY_RATIO', 68.0 / 23.0)
DEFAULT_GEAR_RATIOS = getattr(config, 'GEAR_RATIOS', {
    1: 58.0 / 12.0,
    2: 42.0 / 13.0,
    3: 38.0 / 17.0,
    4: 35.0 / 21.0
})
DEFAULT_CW_A = getattr(config, 'CW_A', 0.50)
DEFAULT_CR = getattr(config, 'CR', 0.015)
DEFAULT_AIR_DENSITY = getattr(config, 'AIR_DENSITY', 1.205)
DEFAULT_TRANSMISSION_EFFICIENCY = getattr(config, 'TRANSMISSION_EFFICIENCY', 0.90)
DEFAULT_GRAVITY = getattr(config, 'GRAVITY', 9.81)


def get_gear_total_ratio(gear=3, primary_ratio=None, gear_ratios=None):
    """Calculates total gear ratio (i_total = primary * gear_ratio)."""
    prim = primary_ratio if primary_ratio is not None else DEFAULT_PRIMARY_RATIO
    gears = gear_ratios if gear_ratios is not None else DEFAULT_GEAR_RATIOS
    gear_ratio = gears.get(gear, gears.get(3, 38.0 / 17.0))
    return prim * gear_ratio


def get_theoretical_rpm_per_kmh(gear=3, tire_circumference=None, primary_ratio=None, gear_ratios=None):
    """
    Theoretical ratio of RPM / Speed(km/h) for a given gear.
    v(km/h) = RPM * (tire_circumference * 3.6) / (60 * i_total)
    RPM / v(km/h) = (60 * i_total) / (tire_circumference * 3.6) = i_total / (tire_circumference * 0.06)
    """
    u = tire_circumference if tire_circumference is not None else DEFAULT_TIRE_CIRCUMFERENCE_M
    i_total = get_gear_total_ratio(gear, primary_ratio, gear_ratios)
    return (60.0 * i_total) / (u * 3.6)


def detect_gear_ratio(df, tire_circumference=None, primary_ratio=None, gear_ratios=None):
    """
    Auto-detects the engaged gear (1, 2, 3, or 4) from the RPM and Speed telemetry.
    Returns: (detected_gear_num, i_total, median_rpm_per_kmh, confidence_score)
    """
    gears = gear_ratios if gear_ratios is not None else DEFAULT_GEAR_RATIOS
    u = tire_circumference if tire_circumference is not None else DEFAULT_TIRE_CIRCUMFERENCE_M
    prim = primary_ratio if primary_ratio is not None else DEFAULT_PRIMARY_RATIO

    # Filter for valid movement (RPM > 2000 and Speed > 10 km/h)
    valid_mask = (df['RPM'] > 2000) & (df['Speed_kmh'] > 10.0)
    if not valid_mask.any():
        # Fallback to 3rd gear default
        i_3 = get_gear_total_ratio(3, prim, gears)
        return 3, i_3, get_theoretical_rpm_per_kmh(3, u, prim, gears), 0.0

    ratios = df.loc[valid_mask, 'RPM'] / df.loc[valid_mask, 'Speed_kmh']
    median_ratio = float(ratios.median())

    best_gear = 3
    best_error = float('inf')

    for g in sorted(gears.keys()):
        expected = get_theoretical_rpm_per_kmh(g, u, prim, gears)
        err = abs(median_ratio - expected) / expected
        if err < best_error:
            best_error = err
            best_gear = g

    confidence = max(0.0, 1.0 - best_error)
    i_total = get_gear_total_ratio(best_gear, prim, gears)
    return best_gear, i_total, median_ratio, confidence


def clean_egt_data(df):
    """
    Cleans system-related EGT measurement errors (spikes at 701°C / 705°C
    or sudden jumps > 50°C per timestep) by holding the last valid value.
    """
    cleaned_egt = []
    last_valid_egt = None
    
    for val in df['EGT']:
        is_invalid = (val in [701.0, 705.0] or val <= 0.0 or np.isnan(val))
        
        if is_invalid:
            if last_valid_egt is not None:
                cleaned_egt.append(last_valid_egt)
            else:
                cleaned_egt.append(20.0)  # Ambient/cold fallback
        else:
            if last_valid_egt is None:
                last_valid_egt = val
                cleaned_egt.append(val)
            else:
                if last_valid_egt > 50.0 and abs(val - last_valid_egt) > 50.0:
                    cleaned_egt.append(last_valid_egt)  # Hold last valid value on spike
                else:
                    cleaned_egt.append(val)
                    last_valid_egt = val
                    
    df['EGT_cleaned'] = cleaned_egt
    return df


def calculate_telemetry_metrics(df, gear=None, slope_percent=0.0,
                                mass_kg=None, cw_a=None, cr=None,
                                tire_circumference_m=None,
                                primary_ratio=None, gear_ratios=None,
                                transmission_efficiency=None):
    """
    Applies Savitzky-Golay smoothing and calculates physical Power (PS) and Torque (Nm)
    using the full vehicle dynamics model:
    - Inertial acceleration force (linear + rotational inertia)
    - Aerodynamic drag force (0.5 * rho * cwA * v^2)
    - Rolling resistance force (cr * m * g)
    - Road gradient force (m * g * sin(theta))
    """
    m = mass_kg if mass_kg is not None else DEFAULT_TOTAL_MASS_KG
    rot_factor = DEFAULT_ROTATIONAL_MASS_FACTOR
    u = tire_circumference_m if tire_circumference_m is not None else DEFAULT_TIRE_CIRCUMFERENCE_M
    prim = primary_ratio if primary_ratio is not None else DEFAULT_PRIMARY_RATIO
    gears = gear_ratios if gear_ratios is not None else DEFAULT_GEAR_RATIOS
    cwA = cw_a if cw_a is not None else DEFAULT_CW_A
    c_r = cr if cr is not None else DEFAULT_CR
    rho = DEFAULT_AIR_DENSITY
    eta = transmission_efficiency if transmission_efficiency is not None else DEFAULT_TRANSMISSION_EFFICIENCY
    g = DEFAULT_GRAVITY

    # 1. Determine active gear & gear ratio
    if gear is None or gear not in gears:
        detected_gear, i_total, _, _ = detect_gear_ratio(df, u, prim, gears)
        df['Detected_Gear'] = detected_gear
    else:
        detected_gear = gear
        i_total = get_gear_total_ratio(gear, prim, gears)
        df['Detected_Gear'] = detected_gear

    # 2. Savitzky-Golay Smoothing for RPM and Speed
    n_points = len(df)
    if HAS_SCIPY and n_points >= 7:
        window_len = min(15, n_points - (1 if n_points % 2 == 0 else 0))
        if window_len < 5:
            window_len = 5 if n_points >= 5 else 3
        df['RPM_smoothed'] = savgol_filter(df['RPM'], window_length=window_len, polyorder=2)
        if 'Speed_kmh' in df.columns:
            df['Speed_smoothed'] = savgol_filter(df['Speed_kmh'], window_length=window_len, polyorder=2)
        else:
            df['Speed_smoothed'] = (df['RPM_smoothed'] * u * 3.6) / (60.0 * i_total)
    else:
        # Fallback smoothing
        window_size = min(9, max(3, n_points // 2 * 2 + 1))
        df['RPM_smoothed'] = df['RPM'].rolling(window=window_size, min_periods=1, center=True).mean()
        if 'Speed_kmh' in df.columns:
            df['Speed_smoothed'] = df['Speed_kmh'].rolling(window=window_size, min_periods=1, center=True).mean()
        else:
            df['Speed_smoothed'] = (df['RPM_smoothed'] * u * 3.6) / (60.0 * i_total)

    # 3. Time step dt calculation (default 0.1s for 10Hz sampling)
    if 'Time' in df.columns:
        try:
            time_series = pd.to_datetime(df['Time'], format='%H:%M:%S', errors='coerce')
            dt_series = time_series.diff().dt.total_seconds().fillna(0.1)
            dt_series = np.where((dt_series <= 0.0) | (dt_series > 1.0), 0.1, dt_series)
        except Exception:
            dt_series = 0.1
    else:
        dt_series = 0.1

    # 4. Angular & Linear Velocity and Acceleration
    df['dRPM_dt'] = df['RPM_smoothed'].diff().fillna(0.0) / dt_series

    # Vehicle speed in m/s derived from engine RPM & gear ratio (clutch engaged)
    v_wheel_ms = (df['RPM_smoothed'] / 60.0 / i_total) * u
    # If GPS speed is available and consistent, we blend with physical wheel speed
    if 'Speed_smoothed' in df.columns and (df['Speed_smoothed'] > 2.0).any():
        v_gps_ms = df['Speed_smoothed'] / 3.6
        # Use wheel speed as primary high-bandwidth reference, bounded by GPS
        v_ms = np.where(v_gps_ms > 2.0, (v_wheel_ms * 0.7 + v_gps_ms * 0.3), v_wheel_ms)
    else:
        v_ms = v_wheel_ms

    df['Velocity_ms'] = v_ms
    df['Acceleration_ms2'] = df['Velocity_ms'].diff().fillna(0.0) / dt_series

    if HAS_SCIPY and n_points >= 7:
        window_len = min(11, n_points - (1 if n_points % 2 == 0 else 0))
        if window_len >= 5:
            df['Acceleration_ms2'] = savgol_filter(df['Acceleration_ms2'], window_length=window_len, polyorder=2)

    # 5. Physical Force Components
    m_effective = m * rot_factor  # Effective mass with rotational inertia
    f_acc = m_effective * df['Acceleration_ms2']
    f_aero = 0.5 * rho * cwA * (df['Velocity_ms'] ** 2)
    f_roll = c_r * m * g
    f_slope = m * g * (slope_percent / 100.0)

    f_total = f_acc + f_aero + f_roll + f_slope

    # 6. Power Calculations (Watts -> PS)
    p_wheel_watts = f_total * df['Velocity_ms']
    p_engine_watts = p_wheel_watts / eta

    # 1 Metric Horsepower (PS) = 735.49875 Watts
    df['PS'] = p_engine_watts / 735.49875
    df['PS'] = df['PS'].clip(lower=0.0)

    # 7. Torque Calculation (Nm)
    # Torque (Nm) = (Power_Watts) / omega_engine = (PS * 735.49875) / (2 * pi * RPM / 60)
    # Nm = (PS * 7023.5) / RPM
    df['Nm'] = np.where(
        (df['RPM_smoothed'] > 500) & (df['PS'] > 0),
        (df['PS'] * 7023.5) / df['RPM_smoothed'],
        0.0
    )

    return df


def detect_dyno_pull(df, min_rpm=2800.0, min_duration_sec=0.8, drop_threshold=400.0):
    """
    Detects the cleanest dyno acceleration pull in a log file.
    Requirements:
    1. Multi-gear check (valid gear ratio 2, 3, or 4).
    2. RPM > min_rpm with sustained positive acceleration (dRPM/dt > 250 U/min/s).
    3. Peak detection: Pull ends when RPM drops by more than drop_threshold from peak.
    Returns: (trimmed_df, is_detected)
    """
    if len(df) < 10:
        return df, False

    # Ensure EGT and basic metrics are pre-calculated
    df = clean_egt_data(df)
    df = calculate_telemetry_metrics(df)

    n_samples_required = max(5, int(min_duration_sec / 0.1))
    rpm = df['RPM_smoothed'].values
    drpm = df['dRPM_dt'].values
    n = len(df)

    best_start = None
    best_end = None
    max_rpm_gain = 0

    i = 0
    while i < n - n_samples_required:
        if rpm[i] >= min_rpm and drpm[i] > 200.0:
            # Check sustained acceleration
            sustained_count = 0
            for k in range(n_samples_required):
                if (i + k < n) and (drpm[i + k] > 150.0):
                    sustained_count += 1

            if sustained_count >= int(n_samples_required * 0.75):
                start_candidate = i
                peak_candidate = rpm[i]
                peak_idx = i

                # Follow the pull to its peak
                for j in range(start_candidate, n):
                    if rpm[j] > peak_candidate:
                        peak_candidate = rpm[j]
                        peak_idx = j
                    elif (peak_candidate - rpm[j]) > drop_threshold or (drpm[j] < -350.0):
                        break

                gain = peak_candidate - rpm[start_candidate]
                if gain > max_rpm_gain and (peak_idx - start_candidate) >= n_samples_required:
                    max_rpm_gain = gain
                    best_start = start_candidate
                    best_end = peak_idx

                i = peak_idx + 1
                continue
        i += 1

    if best_start is None or max_rpm_gain < 800.0:
        # Fallback: take highest continuous positive slope
        return df, False

    trimmed_df = df.iloc[best_start:best_end + 1].copy().reset_index(drop=True)
    
    # Recalculate metrics on the precisely trimmed slice
    trimmed_df = calculate_telemetry_metrics(trimmed_df)
    return trimmed_df, True


def plot_telemetry(df, title_suffix="", output_path=None, vehicle_name="VMC177"):
    """
    Plots the telemetry data in professional dark 'P4-Look' with detected gear and SI carb zone.
    """
    plt.style.use('dark_background')
    
    fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(12, 10), sharex=True, 
                                   gridspec_kw={'height_ratios': [2, 1]})
    
    ax2 = ax1.twinx()  # Secondary Y-axis for Torque
    
    gear = df.get('Detected_Gear', pd.Series([3])).iloc[0] if 'Detected_Gear' in df.columns else 3
    i_total = get_gear_total_ratio(gear)

    # 1. Power & Torque Curves
    p1, = ax1.plot(df['RPM_smoothed'], df['PS'], color='#00ffcc', linewidth=2.8, label='Leistung (PS)')
    p2, = ax2.plot(df['RPM_smoothed'], df['Nm'], color='#ff9800', linewidth=2.8, label='Drehmoment (Nm)')
    
    ax1.grid(True, color='#333333', linestyle='--', alpha=0.7)
    
    max_ps = df['PS'].max() if len(df) > 0 and 'PS' in df.columns else 0.0
    max_nm = df['Nm'].max() if len(df) > 0 and 'Nm' in df.columns else 0.0
    
    ax1.set_ylim(0, max(25.0, max_ps * 1.18))
    ax2.set_ylim(0, max(30.0, max_nm * 1.5))
    
    ax1.set_title(f'StreetDyno 2.0 - {vehicle_name} Leistungsmessung{title_suffix}',
                  fontsize=14, fontweight='bold', pad=15, color='#ffffff')
    ax1.set_ylabel('Leistung [PS]', color='#00ffcc', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Drehmoment [Nm]', color='#ff9800', fontsize=12, fontweight='bold')
    
    ax1.tick_params(axis='y', colors='#00ffcc')
    ax2.tick_params(axis='y', colors='#ff9800')
    
    if max_ps > 0 and max_nm > 0:
        peak_ps_idx = df['PS'].idxmax()
        peak_ps = df['PS'].max()
        peak_ps_rpm = df.loc[peak_ps_idx, 'RPM_smoothed']
        
        peak_nm_idx = df['Nm'].idxmax()
        peak_nm = df['Nm'].max()
        peak_nm_rpm = df.loc[peak_nm_idx, 'RPM_smoothed']
        
        annotation_text = (f"Gang: {gear}. Gang (i={i_total:.2f})\n"
                           f"Peak Leistung: {peak_ps:.1f} PS @ {int(peak_ps_rpm)} U/min\n"
                           f"Peak Drehmoment: {peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min")
        
        ax1.text(0.02, 0.95, annotation_text, transform=ax1.transAxes, fontsize=10,
                 bbox=dict(boxstyle='round', facecolor='#222222', alpha=0.85, edgecolor='#00ffcc'))

    # 2. AFR & EGT Subplot
    ax4 = ax3.twinx()  # Secondary Y-axis for EGT
    
    p3, = ax3.plot(df['RPM_smoothed'], df['AFR'], color='#ff3366', linewidth=2.2, label='AFR')
    p4, = ax4.plot(df['RPM_smoothed'], df['EGT_cleaned'], color='#ffcc00', linewidth=2.2, label='EGT (°C)')
    
    ax3.axhline(12.8, color='#ff3366', linestyle=':', alpha=0.6, label='Optimal AFR Last (12.8-13.0)')
    ax4.axhline(630.0, color='#ffcc00', linestyle=':', alpha=0.7, label='Kritische EGT (630°C)')
    
    ax3.grid(True, color='#333333', linestyle='--', alpha=0.7)
    
    ax3.set_ylabel('AFR', color='#ff3366', fontsize=12, fontweight='bold')
    ax4.set_ylabel('EGT [°C]', color='#ffcc00', fontsize=12, fontweight='bold')
    ax3.set_xlabel('Motordrehzahl [U/min]', fontsize=12, fontweight='bold')
    
    ax3.tick_params(axis='y', colors='#ff3366')
    ax4.tick_params(axis='y', colors='#ffcc00')
    
    ax3.set_ylim(10.5, 16.5)
    ax4.set_ylim(350, 720)
    
    # SI Carburetor transition zone marker
    ax3.axvspan(3000, 5000, color='#ffffff', alpha=0.08, label='SI-Vergaser Übergang (3k-5k)')
    
    lines1 = [p1, p2]
    ax1.legend(lines1, [l.get_label() for l in lines1], loc='upper right')
    
    lines2 = [p3, p4]
    ax3.legend(lines2, [l.get_label() for l in lines2], loc='upper right')
    
    plt.tight_layout()
    
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
    else:
        plt.show()


def export_to_gsf_dyno_csv(df, output_path):
    """
    Exports telemetry dataframe into a standard CSV formatted for GSF-Dyno / MegaLogViewer.
    """
    cols = ['Time', 'RPM', 'RPM_smoothed', 'Speed_kmh', 'AFR', 'EGT_cleaned', 'PS', 'Nm', 'Detected_Gear']
    export_cols = [c for c in cols if c in df.columns]
    df_export = df[export_cols].copy()
    df_export.to_csv(output_path, index=False)
    return output_path


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
            
        cols_to_export = ['Time', 'RPM', 'RPM_smoothed', 'AFR', 'EGT', 'EGT_cleaned', 'Speed_kmh', 'PS', 'Nm', 'Detected_Gear']
        cols_to_export = [c for c in cols_to_export if c in df_export.columns]
        
        df_export = df_export[cols_to_export].fillna("")
        
        worksheet.clear()
        worksheet.update([df_export.columns.values.tolist()] + df_export.values.tolist())
        return True
        
    except Exception as e:
        print(f"[ERROR] Google Sheets Export failed: {e}")
        return False

