"""
StreetDyno 2.0 - Physics & Telemetry Analyzer Logic
Computes vehicle power (PS), torque (Nm), Savitzky-Golay filtering,
road gradient slope compensation, DIN 70020 / SAE J1349 weather normalization,
gear detection, and P4-style visual plotting.
"""

from __future__ import annotations
import os
import math
from typing import Optional, Dict, Tuple, Any, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    from scipy.signal import savgol_filter
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

from config import (
    TOTAL_MASS_KG,
    ROTATIONAL_MASS_FACTOR,
    TIRE_CIRCUMFERENCE_M,
    PRIMARY_RATIO,
    GEAR_RATIOS,
    CW_A,
    CR,
    AIR_DENSITY,
    TRANSMISSION_EFFICIENCY,
    GRAVITY
)


def get_gear_total_ratio(
    gear: int = 3,
    primary_ratio: Optional[float] = None,
    gear_ratios: Optional[Dict[int, float]] = None
) -> float:
    """Calculates total gear reduction ratio (i_total = primary * gear_ratio)."""
    prim = primary_ratio if primary_ratio is not None else PRIMARY_RATIO
    gears = gear_ratios if gear_ratios is not None else GEAR_RATIOS
    gear_ratio = gears.get(gear, gears.get(3, 38.0 / 17.0))
    return prim * gear_ratio


def get_theoretical_rpm_per_kmh(
    gear: int = 3,
    tire_circumference: Optional[float] = None,
    primary_ratio: Optional[float] = None,
    gear_ratios: Optional[Dict[int, float]] = None
) -> float:
    """Calculates theoretical engine RPM per km/h vehicle speed for a given gear."""
    u = tire_circumference if tire_circumference is not None else TIRE_CIRCUMFERENCE_M
    i_total = get_gear_total_ratio(gear, primary_ratio, gear_ratios)
    return (60.0 * i_total) / (u * 3.6)


def detect_gear_ratio(
    df: pd.DataFrame,
    tire_circumference: Optional[float] = None,
    primary_ratio: Optional[float] = None,
    gear_ratios: Optional[Dict[int, float]] = None
) -> Tuple[int, float, float, float]:
    """
    Auto-detects the engaged transmission gear (1, 2, 3, or 4) from RPM and speed.
    Returns: (detected_gear_num, i_total, median_rpm_per_kmh, confidence_score)
    """
    gears = gear_ratios if gear_ratios is not None else GEAR_RATIOS
    u = tire_circumference if tire_circumference is not None else TIRE_CIRCUMFERENCE_M
    prim = primary_ratio if primary_ratio is not None else PRIMARY_RATIO

    valid_mask = (df['RPM'] > 2000) & (df['Speed_kmh'] > 10.0)
    if not valid_mask.any():
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


def calculate_weather_correction_factor(
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25,
    standard: str = "DIN70020"
) -> float:
    """
    Calculates atmospheric weather normalization factor according to DIN 70020 or SAE J1349.
    
    DIN 70020 (Reference: 20°C / 293.15 K, 1013.25 hPa):
    k_DIN = (1013.25 / p) * sqrt((T + 273.15) / 293.15)
    
    SAE J1349 (Reference: 25°C / 298.15 K, 990.0 hPa):
    k_SAE = (990.0 / p) * ((T + 273.15) / 298.15)^0.6
    """
    try:
        t = float(temp_c) if temp_c is not None else 20.0
        p = float(pressure_hpa) if pressure_hpa is not None else 1013.25
        if p <= 500.0 or p >= 1200.0:
            p = 1013.25
        if t <= -40.0 or t >= 60.0:
            t = 20.0

        std = str(standard).upper() if standard else "DIN70020"

        if "SAE" in std:
            k = (990.0 / p) * (((t + 273.15) / 298.15) ** 0.6)
        elif "RAW" in std or "NONE" in std:
            k = 1.0
        else:  # Standard: DIN 70020
            k = (1013.25 / p) * math.sqrt((t + 273.15) / 293.15)

        return float(max(0.75, min(1.30, k)))
    except Exception:
        return 1.0


def calculate_road_slope_percent(
    df: pd.DataFrame,
    manual_slope_pct: Optional[Union[float, str]] = None
) -> float:
    """
    Calculates road gradient percentage (Slope %) either automatically from GPS
    altitude delta with Savitzky-Golay filtering, or uses a manual preset value.
    Automatic GPS slope compensation is bounded to max ±2.5% to prevent altitude jitter artifacts.
    """
    if manual_slope_pct is not None and manual_slope_pct != "auto":
        try:
            return float(max(-15.0, min(15.0, float(manual_slope_pct))))
        except (ValueError, TypeError):
            pass

    if 'Alt' in df.columns and len(df) >= 6:
        try:
            valid_alt = pd.to_numeric(df['Alt'], errors='coerce')
            if not valid_alt.isna().all() and (valid_alt.max() - valid_alt.min()) >= 0.1:
                if HAS_SCIPY and len(valid_alt.dropna()) >= 7:
                    w = min(11, len(valid_alt.dropna()) - (1 if len(valid_alt.dropna()) % 2 == 0 else 0))
                    if w >= 5:
                        alt_smoothed = savgol_filter(valid_alt.interpolate().bfill().ffill(), window_length=w, polyorder=1)
                    else:
                        alt_smoothed = valid_alt.rolling(5, min_periods=1, center=True).median()
                else:
                    alt_smoothed = valid_alt.rolling(5, min_periods=1, center=True).median()

                if 'Speed_kmh' in df.columns:
                    v = df['Speed_kmh'].values / 3.6
                elif 'RPM' in df.columns:
                    v = (df['RPM'].values / 60.0 / 6.61) * TIRE_CIRCUMFERENCE_M
                else:
                    v = np.full(len(df), 15.0)

                dt = 0.1
                dist_m = float(np.sum(v * dt))
                delta_alt = float(alt_smoothed.iloc[-1] - alt_smoothed.iloc[0]) if hasattr(alt_smoothed, 'iloc') else float(alt_smoothed[-1] - alt_smoothed[0])

                if dist_m > 15.0:
                    slope_pct = (delta_alt / dist_m) * 100.0
                    # Auto-slope bounded to max ±2.5% to eliminate spurious altitude spikes
                    return float(max(-2.5, min(2.5, slope_pct)))
        except Exception:
            pass

    return 0.0


def clean_egt_data(df: pd.DataFrame) -> pd.DataFrame:
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
                cleaned_egt.append(20.0)
        else:
            if last_valid_egt is None:
                last_valid_egt = val
                cleaned_egt.append(val)
            else:
                if last_valid_egt > 50.0 and abs(val - last_valid_egt) > 50.0:
                    cleaned_egt.append(last_valid_egt)
                else:
                    cleaned_egt.append(val)
                    last_valid_egt = val

    df['EGT_cleaned'] = cleaned_egt
    return df


def calculate_telemetry_metrics(
    df: pd.DataFrame,
    gear: Optional[int] = None,
    slope_percent: Optional[Union[float, str]] = None,
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25,
    norm_standard: str = "DIN70020",
    mass_kg: Optional[float] = None,
    cw_a: Optional[float] = None,
    cr: Optional[float] = None,
    tire_circumference_m: Optional[float] = None,
    primary_ratio: Optional[float] = None,
    gear_ratios: Optional[Dict[int, float]] = None,
    transmission_efficiency: Optional[float] = None
) -> pd.DataFrame:
    """
    Applies Savitzky-Golay smoothing and calculates physical Power (PS) and Torque (Nm)
    using the full vehicle dynamics model:
    - Inertial acceleration force (linear + rotational inertia)
    - Aerodynamic drag force (0.5 * rho * cwA * v^2)
    - Rolling resistance force (cr * m * g)
    - Road gradient force (m * g * sin(theta))
    - Atmospheric weather normalization factor (DIN 70020 / SAE J1349)
    """
    m = mass_kg if mass_kg is not None else TOTAL_MASS_KG
    rot_factor = ROTATIONAL_MASS_FACTOR
    u = tire_circumference_m if tire_circumference_m is not None else TIRE_CIRCUMFERENCE_M
    prim = primary_ratio if primary_ratio is not None else PRIMARY_RATIO
    gears = gear_ratios if gear_ratios is not None else GEAR_RATIOS
    cwA = cw_a if cw_a is not None else CW_A
    c_r = cr if cr is not None else CR
    rho = AIR_DENSITY
    eta = transmission_efficiency if transmission_efficiency is not None else TRANSMISSION_EFFICIENCY
    g = GRAVITY

    # 1. Determine active gear & gear ratio
    if gear is None or gear not in gears:
        detected_gear, i_total, _, _ = detect_gear_ratio(df, u, prim, gears)
        df['Detected_Gear'] = detected_gear
    else:
        detected_gear = gear
        i_total = get_gear_total_ratio(gear, prim, gears)
        df['Detected_Gear'] = detected_gear

    # 2. Adaptive Two-Stage Smoothing for RPM and Speed
    n_points = len(df)
    if HAS_SCIPY and n_points >= 7:
        if n_points < 50:
            # Stage 1: Fast local moving average to suppress four-stroke engine sputter / micro-jitter
            rpm_pre = df['RPM'].rolling(3, min_periods=1, center=True).mean()
            w_rpm = min(17, n_points - (1 if n_points % 2 == 0 else 0))
            if w_rpm < 5:
                w_rpm = 5 if n_points >= 5 else 3
            df['RPM_smoothed'] = savgol_filter(rpm_pre, window_length=w_rpm, polyorder=2)

            if 'Speed_kmh' in df.columns:
                spd_pre = df['Speed_kmh'].rolling(3, min_periods=1, center=True).mean()
                df['Speed_smoothed'] = savgol_filter(spd_pre, window_length=w_rpm, polyorder=2)
            else:
                df['Speed_smoothed'] = (df['RPM_smoothed'] * u * 3.6) / (60.0 * i_total)
        else:
            w_rpm = min(21, n_points - (1 if n_points % 2 == 0 else 0))
            df['RPM_smoothed'] = savgol_filter(df['RPM'], window_length=w_rpm, polyorder=2)
            if 'Speed_kmh' in df.columns:
                df['Speed_smoothed'] = savgol_filter(df['Speed_kmh'], window_length=w_rpm, polyorder=2)
            else:
                df['Speed_smoothed'] = (df['RPM_smoothed'] * u * 3.6) / (60.0 * i_total)
    else:
        window_size = min(15, max(3, (n_points // 2) * 2 + 1))
        df['RPM_smoothed'] = df['RPM'].rolling(window=window_size, min_periods=1, center=True).mean()
        if 'Speed_kmh' in df.columns:
            df['Speed_smoothed'] = df['Speed_kmh'].rolling(window=window_size, min_periods=1, center=True).mean()
        else:
            df['Speed_smoothed'] = (df['RPM_smoothed'] * u * 3.6) / (60.0 * i_total)

    # 3. Time step dt calculation
    if 'Time' in df.columns:
        try:
            time_series = pd.to_datetime(df['Time'], format='%H:%M:%S', errors='coerce')
            dt_series = time_series.diff().dt.total_seconds().fillna(0.1)
            dt_series = np.where((dt_series <= 0.0) | (dt_series > 1.0), 0.1, dt_series)
        except Exception:
            dt_series = 0.1
    else:
        dt_series = 0.1

    # 4. Angular & Linear Velocity and Plausible Acceleration Clamping
    raw_drpm_dt = df['RPM_smoothed'].diff().fillna(0.0) / dt_series
    if HAS_SCIPY and n_points >= 7:
        w_d = min(11, n_points - (1 if n_points % 2 == 0 else 0))
        if w_d >= 5:
            raw_drpm_dt = savgol_filter(raw_drpm_dt, window_length=w_d, polyorder=2)
    # Bound max rotational acceleration in 3rd gear to max 1800.0 RPM/s to eliminate clutch slip & bump spikes
    df['dRPM_dt'] = np.clip(raw_drpm_dt, -1500.0, 1800.0)

    v_wheel_ms = (df['RPM_smoothed'] / 60.0 / i_total) * u
    if 'Speed_smoothed' in df.columns and (df['Speed_smoothed'] > 2.0).any():
        v_gps_ms = df['Speed_smoothed'] / 3.6
        v_ms = np.where(v_gps_ms > 2.0, (v_wheel_ms * 0.7 + v_gps_ms * 0.3), v_wheel_ms)
    else:
        v_ms = v_wheel_ms

    df['Velocity_ms'] = v_ms
    raw_accel = df['Velocity_ms'].diff().fillna(0.0) / dt_series

    if HAS_SCIPY and n_points >= 7:
        window_len_a = min(11, n_points - (1 if n_points % 2 == 0 else 0))
        if window_len_a >= 5:
            raw_accel = savgol_filter(raw_accel, window_length=window_len_a, polyorder=2)

    # Clamping linear acceleration to physical limits (max 4.2 m/s² ≈ 0.43g for Vespa Largeframe)
    df['Acceleration_ms2'] = np.clip(raw_accel, -6.0, 4.2)

    # 5. Physical Force Components
    m_effective = m * rot_factor
    f_acc = m_effective * df['Acceleration_ms2']
    f_aero = 0.5 * rho * cwA * (df['Velocity_ms'] ** 2)
    f_roll = c_r * m * g

    active_slope_pct = calculate_road_slope_percent(df, slope_percent)
    df['Slope_Pct'] = active_slope_pct
    f_slope = m * g * (active_slope_pct / 100.0)
    df['Slope_Force_N'] = f_slope
    df['Slope_Power_PS'] = ((f_slope * df['Velocity_ms']) / eta) / 735.49875

    f_total = f_acc + f_aero + f_roll + f_slope

    # 6. Power Calculations & DIN 70020 Weather Normalization
    p_wheel_watts = f_total * df['Velocity_ms']
    p_engine_watts = p_wheel_watts / eta

    k_norm = calculate_weather_correction_factor(temp_c, pressure_hpa, norm_standard)
    df['Weather_K_Norm'] = k_norm
    df['Ambient_Temp_C'] = float(temp_c) if temp_c is not None else 20.0
    df['Ambient_Pressure_hPa'] = float(pressure_hpa) if pressure_hpa is not None else 1013.25
    df['Norm_Standard'] = str(norm_standard)

    raw_ps_calc = (p_engine_watts / 735.49875).clip(lower=0.0)
    norm_ps_calc = (raw_ps_calc * k_norm).clip(lower=0.0)

    # P4 Resonanzbogen & Dip Harmonization (< 2.5 PS sag over 400 RPM)
    if HAS_SCIPY and n_points >= 9:
        w_p4 = min(15, n_points - (1 if n_points % 2 == 0 else 0))
        if w_p4 >= 5:
            ps_trend = savgol_filter(norm_ps_calc, window_length=w_p4, polyorder=2)
            norm_ps_calc = savgol_filter(np.maximum(norm_ps_calc, ps_trend), window_length=w_p4, polyorder=2).clip(lower=0.0)
            raw_ps_trend = savgol_filter(raw_ps_calc, window_length=w_p4, polyorder=2)
            raw_ps_calc = savgol_filter(np.maximum(raw_ps_calc, raw_ps_trend), window_length=w_p4, polyorder=2).clip(lower=0.0)

    df['PS_Raw'] = raw_ps_calc
    df['PS'] = norm_ps_calc

    # 7. Torque Calculation (Nm) - Consistent with Nm = (PS * 7023.5) / RPM
    df['Nm_Raw'] = np.where(
        (df['RPM_smoothed'] > 500) & (df['PS_Raw'] > 0),
        (df['PS_Raw'] * 7023.5) / df['RPM_smoothed'],
        0.0
    )
    df['Nm'] = np.where(
        (df['RPM_smoothed'] > 500) & (df['PS'] > 0),
        (df['PS'] * 7023.5) / df['RPM_smoothed'],
        0.0
    )

    return df


def detect_dyno_pull(
    df: pd.DataFrame,
    min_rpm: float = 2800.0,
    min_duration_sec: float = 0.8,
    drop_threshold: float = 400.0,
    slope_percent: Optional[Union[float, str]] = None,
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25,
    norm_standard: str = "DIN70020"
) -> Tuple[pd.DataFrame, bool]:
    """
    Detects the cleanest dyno acceleration pull in a log file.
    Returns: (trimmed_df, is_detected)
    """
    if len(df) < 10:
        return df, False

    df = clean_egt_data(df)
    df = calculate_telemetry_metrics(
        df,
        slope_percent=slope_percent,
        temp_c=temp_c,
        pressure_hpa=pressure_hpa,
        norm_standard=norm_standard
    )

    n_samples_required = max(5, int(min_duration_sec / 0.1))
    rpm = df['RPM_smoothed'].values
    drpm = df['dRPM_dt'].values
    n = len(df)

    best_start = None
    best_end = None
    max_rpm_gain = 0

    i = 0
    while i < n - n_samples_required:
        if rpm[i] >= min_rpm and drpm[i] > 150.0:
            start_idx = i
            peak_idx = start_idx
            peak_rpm = rpm[start_idx]

            j = start_idx + 1
            while j < n:
                curr_rpm = rpm[j]
                if curr_rpm > peak_rpm:
                    peak_rpm = curr_rpm
                    peak_idx = j
                elif (peak_rpm - curr_rpm) > drop_threshold:
                    break
                j += 1

            pull_end = peak_idx
            pull_duration_samples = pull_end - start_idx
            rpm_gain = peak_rpm - rpm[start_idx]

            if pull_duration_samples >= n_samples_required and rpm_gain > 1000.0:
                if rpm_gain > max_rpm_gain:
                    max_rpm_gain = rpm_gain
                    best_start = start_idx
                    best_end = pull_end

            i = peak_idx + 1
            continue
        i += 1

    if best_start is None or max_rpm_gain < 800.0:
        return df, False

    trimmed_df = df.iloc[best_start:best_end + 1].copy().reset_index(drop=True)
    trimmed_df = calculate_telemetry_metrics(
        trimmed_df,
        slope_percent=slope_percent,
        temp_c=temp_c,
        pressure_hpa=pressure_hpa,
        norm_standard=norm_standard
    )
    return trimmed_df, True


def plot_telemetry(
    df: pd.DataFrame,
    title_suffix: str = "",
    output_path: Optional[str] = None,
    vehicle_name: str = "VMC177"
) -> None:
    """Plots telemetry data in professional dark P4-Look with power, torque, AFR, and EGT."""
    plt.style.use('dark_background')

    fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(12, 10), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    ax2 = ax1.twinx()

    gear = df.get('Detected_Gear', pd.Series([3])).iloc[0] if 'Detected_Gear' in df.columns else 3
    i_total = get_gear_total_ratio(gear)

    # 1. Power & Torque Curves
    ax1.plot(df['RPM_smoothed'], df['PS'], color='#00ffcc', linewidth=2.8, label='Leistung (PS)')
    ax2.plot(df['RPM_smoothed'], df['Nm'], color='#ff9800', linewidth=2.8, label='Drehmoment (Nm)')

    ax1.grid(True, color='#333333', linestyle='--', alpha=0.7)

    max_ps = float(df['PS'].max()) if len(df) > 0 and 'PS' in df.columns else 20.0
    max_nm = float(df['Nm'].max()) if len(df) > 0 and 'Nm' in df.columns else 0.0
    ps_top = max(20.0, math.ceil((max_ps * 1.15) / 5.0) * 5.0)

    # Ammerschläger-P4 Layout: Torque axis is dynamically scaled to 2.5x the power axis
    ax1.set_ylim(0, ps_top)
    ax2.set_ylim(0, ps_top * 2.5)

    ax1.set_title(f'StreetDyno 2.0 - {vehicle_name} Leistungsmessung{title_suffix}', fontsize=14, fontweight='bold', pad=15, color='#ffffff')
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

        slope_val = df.get('Slope_Pct', pd.Series([0.0])).iloc[0] if 'Slope_Pct' in df.columns else 0.0
        p_slope_avg = df.get('Slope_Power_PS', pd.Series([0.0])).mean() if 'Slope_Power_PS' in df.columns else 0.0
        slope_tag = f"\nSteigung: {slope_val:+.1f}% ({p_slope_avg:+.1f} PS)" if abs(slope_val) >= 0.1 else ""

        annotation_text = (
            f"Gang: {gear}. Gang (i={i_total:.2f}){slope_tag}\n"
            f"Peak Leistung: {peak_ps:.1f} PS @ {int(peak_ps_rpm)} U/min\n"
            f"Peak Drehmoment: {peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min"
        )
        ax1.text(0.02, 0.95, annotation_text, transform=ax1.transAxes, fontsize=10, bbox=dict(boxstyle='round', facecolor='#222222', alpha=0.85, edgecolor='#00ffcc'))

    # 2. AFR & EGT Subplot
    ax4 = ax3.twinx()
    ax3.plot(df['RPM_smoothed'], df['AFR'], color='#ff3366', linewidth=2.2, label='AFR')
    ax4.plot(df['RPM_smoothed'], df['EGT_cleaned'], color='#ffcc00', linewidth=2.2, label='EGT (°C)')

    ax3.axhline(12.8, color='#ff3366', linestyle=':', alpha=0.6, label='Optimal AFR Last (12.8-13.0)')
    ax4.axhline(630.0, color='#ffcc00', linestyle=':', alpha=0.7, label='Kritische EGT (630°C)')

    ax3.grid(True, color='#333333', linestyle='--', alpha=0.7)
    ax3.set_ylabel('AFR', color='#ff3366', fontsize=12, fontweight='bold')
    ax4.set_ylabel('EGT [°C]', color='#ffcc00', fontsize=12, fontweight='bold')
    ax3.set_xlabel('Motordrehzahl [U/min]', fontsize=12, fontweight='bold')
    ax3.tick_params(axis='y', colors='#ff3366')
    ax4.tick_params(axis='y', colors='#ffcc00')

    plt.tight_layout()
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=130, facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
    else:
        plt.show()


def export_to_google_sheets(
    df: pd.DataFrame,
    credentials_json: str = "service_account.json",
    spreadsheet_name: str = "Vespa_Dyno_Cloud"
) -> bool:
    """Exports processed dyno metrics to Google Sheets."""
    if not HAS_GSPREAD:
        print("[!] gspread nicht installiert. Export uebersprungen.")
        return False

    if not os.path.exists(credentials_json):
        print(f"[!] Google Credentials '{credentials_json}' nicht gefunden.")
        return False

    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(credentials_json, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open(spreadsheet_name).sheet1

        export_cols = ['Time', 'RPM_smoothed', 'PS', 'Nm', 'AFR', 'EGT_cleaned', 'Speed_smoothed']
        available = [c for c in export_cols if c in df.columns]
        data_to_export = [available] + df[available].fillna(0).values.tolist()

        sheet.clear()
        sheet.update('A1', data_to_export)
        print(f"[OK] {len(df)} Datenpunkte erfolgreich nach Google Sheets '{spreadsheet_name}' exportiert.")
        return True
    except Exception as e:
        print(f"[ERROR] Fehler beim Google Sheets Export: {e}")
        return False
