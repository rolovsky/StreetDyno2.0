"""
StreetDyno 2.0 - Physics & Telemetry Analyzer Logic (V5.3 Numerical Hardening)
High-precision WOT Dyno-Pull detection, multi-condition filtering,
Savitzky-Golay noise suppression, Pmax/Mmax peak plausibility verification,
road gradient slope compensation, and DIN 70020 / SAE J1349 weather normalization.
"""

from __future__ import annotations
import os
import math
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Any, Union, List

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
    J_WHEELS_KG_M2,
    J_ENGINE_KG_M2,
    TIRE_CIRCUMFERENCE_M,
    TIRE_RADIUS_DYN_M,
    PRIMARY_RATIO,
    GEAR_RATIOS,
    CW_A,
    CR,
    AIR_DENSITY,
    TRANSMISSION_EFFICIENCY,
    GRAVITY
)

# Configure module logger
logger = logging.getLogger("StreetDyno.DynoEngine")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class PullFilterConfig:
    """Parameters for multi-condition WOT dyno pull validation."""
    min_rpm: float = 2600.0                # Minimum starting RPM
    min_speed_kmh: float = 15.0            # Minimum vehicle velocity (km/h)
    min_accel_ms2: float = 0.8             # Minimum positive linear acceleration (m/s²)
    min_drpm_dt: float = 120.0             # Minimum positive rotational rate (RPM/s)
    afr_load_min: float = 9.5              # Minimum plausible combustion AFR under load
    afr_load_max: float = 14.2             # Maximum allowable load AFR for WOT
    afr_cutoff_threshold: float = 15.0     # Hard abort threshold (throttle closed / coasting)
    transient_lean_max_samples: int = 3    # Allow up to 0.3s transient lean spike on quick throttle snap
    min_duration_sec: float = 1.8          # Minimum duration for valid power run (s)
    min_rpm_gain: float = 1500.0           # Minimum RPM band swept during pull (RPM)
    drop_threshold_rpm: float = 350.0      # RPM drop to mark end of pull (RPM)
    clutch_pull_drpm_threshold: float = 800.0   # Unloaded dRPM/dt spike (RPM/s) signaling clutch-pull /
                                                 # load-dump at end of pull. Requires rpm_gain > 1200 already
                                                 # achieved (not a cold-start rev). Validated against
                                                 # 2026-09-12 Vespa coast-down data: spike reaches ~1157 RPM/s
                                                 # (smoothed); genuine WOT stays < 250 RPM/s.


DEFAULT_FILTER_CONFIG = PullFilterConfig()


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

    spd_col = "Speed_smoothed" if ("Speed_smoothed" in df.columns and (df["Speed_smoothed"] > 10.0).any()) else "Speed_kmh"
    if spd_col not in df.columns:
        i_3 = get_gear_total_ratio(3, prim, gears)
        return 3, i_3, get_theoretical_rpm_per_kmh(3, u, prim, gears), 0.0

    valid_mask = (df["RPM"] > 2000) & (df[spd_col] > 10.0)
    if not valid_mask.any():
        i_3 = get_gear_total_ratio(3, prim, gears)
        return 3, i_3, get_theoretical_rpm_per_kmh(3, u, prim, gears), 0.0

    ratios = df.loc[valid_mask, "RPM"] / df.loc[valid_mask, spd_col]

    # Guard against GPS lag during rapid acceleration:
    # Under high acceleration, GPS speed lags, making RPM / Speed artificially high.
    # Check if there are initial cruise / low dRPM samples (diff < 25 RPM/sample = 250 RPM/s)
    rpm_diff = df.loc[valid_mask, "RPM"].diff().abs().fillna(0.0)
    steady_mask = rpm_diff < 25.0
    if steady_mask.sum() >= 5:
        r_steady = ratios[steady_mask]
        median_ratio = float(r_steady.iloc[:15].median()) if len(r_steady) >= 15 else float(r_steady.median())
    else:
        median_ratio = float(ratios.median())

    best_gear = 3
    best_error = float("inf")

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
    """Calculates atmospheric weather normalization factor according to DIN 70020 or SAE J1349."""
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
    """Calculates road gradient percentage (Slope %). Auto bounded to max ±2.5%."""
    if manual_slope_pct is not None and manual_slope_pct != "auto":
        try:
            return float(max(-15.0, min(15.0, float(manual_slope_pct))))
        except (ValueError, TypeError):
            pass

    # Noisy GPS altitude differentiation is deactivated by default to prevent ghost power artifacts.
    return 0.0


def clean_egt_data(df: pd.DataFrame) -> pd.DataFrame:
    """Cleans system-related EGT measurement errors (spikes at 701°C / 705°C)."""
    cleaned_egt = []
    last_valid_egt = None

    if "EGT" not in df.columns:
        df["EGT_cleaned"] = 20.0
        return df

    for val in df["EGT"]:
        is_invalid = (val in [701.0, 705.0] or val <= 0.0 or np.isnan(val))
        if is_invalid:
            cleaned_egt.append(last_valid_egt if last_valid_egt is not None else 20.0)
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

    df["EGT_cleaned"] = cleaned_egt
    return df


def smooth_signal(
    series: Union[pd.Series, np.ndarray, List[float]],
    window_length: int = 11,
    polyorder: int = 2
) -> np.ndarray:
    """
    Applies Savitzky-Golay filtering if available, with graceful fallback to
    weighted rolling average for noise suppression prior to differentiation.
    """
    arr = np.asarray(series, dtype=float)
    n = len(arr)
    if n < 3:
        return arr

    if HAS_SCIPY and n >= 5:
        w = min(window_length, n if n % 2 == 1 else n - 1)
        if w % 2 == 0:
            w -= 1
        if w >= 5 and w > polyorder:
            try:
                return savgol_filter(arr, window_length=w, polyorder=polyorder)
            except Exception:
                pass

    win = min(max(3, window_length), n)
    weights = np.bartlett(win)
    weights /= weights.sum()
    smoothed = np.convolve(arr, weights, mode="same")
    smoothed[0] = arr[0]
    smoothed[-1] = arr[-1]
    return smoothed


def validate_and_sanitize_dyno_peaks(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Plausibility check for peak power (Pmax) and peak torque (Mmax).
    In internal combustion engines (especially 2-stroke with expansion exhaust),
    Mmax occurs strictly before Pmax (RPM_Mmax < RPM_Pmax).
    If RPM_Pmax == RPM_Mmax or they coincide at a single sharp spike, it indicates
    a differentiation artifact / clutch shudder. Sanitize and log warning.
    """
    meta = {"is_plausible": True, "warning": None}
    if len(df) < 5 or "PS" not in df.columns or "Nm" not in df.columns:
        return df, meta

    ps_vals = df["PS"].values
    nm_vals = df["Nm"].values
    rpm_vals = df["RPM_smoothed"].values

    idx_p = int(np.argmax(ps_vals))
    idx_m = int(np.argmax(nm_vals))

    peak_ps = float(ps_vals[idx_p])
    peak_ps_rpm = float(rpm_vals[idx_p])
    peak_nm = float(nm_vals[idx_m])
    peak_nm_rpm = float(rpm_vals[idx_m])

    rpm_diff = abs(peak_ps_rpm - peak_nm_rpm)
    is_torque_unphysical = peak_nm > 25.0
    is_peak_coincident = (rpm_diff < 80.0 or peak_ps_rpm < peak_nm_rpm)

    if (is_peak_coincident or is_torque_unphysical) and peak_ps > 5.0:
        if is_torque_unphysical:
            msg = (
                f"⚠️ [PLAUSIBILITÄTS-WARNUNG] Drehmomentspitze ({peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min) "
                f"übersteigt thermodynamisches 2-Takt-Limit für 187ccm (pe > 8.5 bar, max ~25 Nm). "
                f"Spike-Artefakt erkannt und bereinigt."
            )
        else:
            msg = (
                f"⚠️ [PLAUSIBILITÄTS-WARNUNG] Pmax ({peak_ps:.1f} PS @ {int(peak_ps_rpm)} U/min) und "
                f"Mmax ({peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min) fallen unphysikalisch zusammen "
                f"(RPM-Delta: {rpm_diff:.0f} U/min). Spike-Artefakt erkannt."
            )
        logger.warning(msg)
        meta["is_plausible"] = False
        meta["warning"] = msg

        ps_sanitized = smooth_signal(ps_vals, window_length=15, polyorder=2)
        df["PS"] = ps_sanitized
        if "PS_Raw" in df.columns:
            df["PS_Raw"] = smooth_signal(df["PS_Raw"].values, window_length=15, polyorder=2)
        df["Nm"] = np.where(
            (df["RPM_smoothed"] > 500) & (df["PS"] > 0),
            (df["PS"] * 7023.5) / df["RPM_smoothed"],
            0.0
        )
        if "Nm_Raw" in df.columns:
            df["Nm_Raw"] = np.where(
                (df["RPM_smoothed"] > 500) & (df["PS_Raw"] > 0),
                (df["PS_Raw"] * 7023.5) / df["RPM_smoothed"],
                0.0
            )

        # Enforce hard thermodynamic ceiling on torque (25.0 Nm) and power
        over_nm = df["Nm"] > 25.0
        if over_nm.any():
            df.loc[over_nm, "Nm"] = 25.0
            df.loc[over_nm, "PS"] = (25.0 * df.loc[over_nm, "RPM_smoothed"]) / 7023.5
        if "Nm_Raw" in df.columns and (df["Nm_Raw"] > 25.0).any():
            over_nm_raw = df["Nm_Raw"] > 25.0
            df.loc[over_nm_raw, "Nm_Raw"] = 25.0
            df.loc[over_nm_raw, "PS_Raw"] = (25.0 * df.loc[over_nm_raw, "RPM_smoothed"]) / 7023.5

    return df, meta


def calculate_telemetry_metrics(
    df: pd.DataFrame,
    gear: Optional[Union[int, str]] = None,
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
    Applies Savitzky-Golay / weighted smoothing and calculates physical Power (PS) and Torque (Nm)
    using the full vehicle dynamics model with Hybrid Wheel/Loss power, DIN 70020 normalization,
    and peak plausibility validation.
    """
    m = mass_kg if mass_kg is not None else TOTAL_MASS_KG
    u = tire_circumference_m if tire_circumference_m is not None else TIRE_CIRCUMFERENCE_M
    prim = primary_ratio if primary_ratio is not None else PRIMARY_RATIO
    gears = gear_ratios if gear_ratios is not None else GEAR_RATIOS
    cwA = cw_a if cw_a is not None else CW_A
    c_r = cr if cr is not None else CR
    rho = AIR_DENSITY
    eta = transmission_efficiency if transmission_efficiency is not None else TRANSMISSION_EFFICIENCY
    g = GRAVITY

    # 1. Determine active gear & gear ratio
    selected_gear = None
    if gear is not None:
        try:
            g_int = int(gear)
            if g_int in gears:
                selected_gear = g_int
        except (ValueError, TypeError):
            selected_gear = None

    if selected_gear is None:
        detected_gear, i_total, _, _ = detect_gear_ratio(df, u, prim, gears)
        df["Detected_Gear"] = detected_gear
    else:
        detected_gear = selected_gear
        i_total = get_gear_total_ratio(selected_gear, prim, gears)
        df["Detected_Gear"] = detected_gear

    # 2. Time step dt calculation — always produce a 1-D ndarray (F-02)
    # Scalar or 0-dim fallback breaks dt_arr[dt_arr > 0] boolean indexing when
    # np.where(scalar, ...) returns a 0-dim array that np.isscalar() mis-identifies.
    n_points = len(df)
    if "Time" in df.columns:
        try:
            t_num = pd.to_numeric(df["Time"], errors="coerce")
            if not t_num.isna().all():
                dt_arr_raw = t_num.diff().fillna(0.1).values
            else:
                time_series = pd.to_datetime(df["Time"], format="%H:%M:%S", errors="coerce")
                dt_arr_raw = time_series.diff().dt.total_seconds().fillna(0.1).values
            dt_arr = np.where(
                (dt_arr_raw <= 0.0) | (dt_arr_raw > 1.0), 0.1, dt_arr_raw
            ).astype(float)
        except Exception:
            dt_arr = np.full(n_points, 0.1, dtype=float)
    else:
        dt_arr = np.full(n_points, 0.1, dtype=float)

    # 3. Adaptive Savitzky-Golay filtering & analytical derivative
    target_w = 21  # 2.1s window at 10Hz eliminates 2-stroke cyclic jitter and CDI fluctuations
    w = min(target_w, n_points - (1 if n_points % 2 == 0 else 0))
    if w % 2 == 0:
        w -= 1
    if w < 5:
        w = 5 if n_points >= 5 else 3

    # Derive real median sample interval from log timestamps (S-03 / F-02).
    # Packet jitter and EMI burst batching can shift effective dt by ±3×,
    # scaling dRPM/dt — and thus power — proportionally.
    t_diffs = dt_arr[dt_arr > 0]
    dt_median = float(np.median(t_diffs)) if len(t_diffs) > 0 else 0.1
    dt_safe = max(0.05, min(0.25, dt_median))

    if HAS_SCIPY and n_points >= 5:
        df["RPM_smoothed"] = savgol_filter(df["RPM"], window_length=w, polyorder=2)
        raw_drpm_dt = savgol_filter(df["RPM"], window_length=w, polyorder=2, deriv=1, delta=dt_safe)
    else:
        df["RPM_smoothed"] = smooth_signal(df["RPM"], window_length=w, polyorder=2)
        raw_drpm_dt = pd.Series(df["RPM_smoothed"]).diff().fillna(0.0) / dt_arr

    df["dRPM_dt"] = np.clip(raw_drpm_dt, -1500.0, 1800.0)

    # Strictly kinematic velocity from RPM and tire rolling circumference
    df["Velocity_ms"] = (df["RPM_smoothed"] / 60.0 / i_total) * u
    df["Speed_smoothed"] = df["Velocity_ms"] * 3.6

    # Strictly kinematic acceleration from dRPM/dt with physical gear sanity envelope
    raw_accel = (df["dRPM_dt"] / 60.0 / i_total) * u
    max_accel_clamp = 2.30 if detected_gear == 3 else (3.20 if detected_gear == 2 else 4.20)
    df["Acceleration_ms2"] = np.clip(raw_accel, -6.0, max_accel_clamp)

    # 4. Physical Force Components
    # F-03: Use calibrated dynamic loaded radius for J/r² back-calculation.
    # r_geom = u/(2π) = 0.2149m overestimates the loaded radius by ~2.5%,
    # causing a ~4.7% error in equivalent inertia mass. Kinematic velocity
    # (line below) still uses u (rolling circumference) — that is correct.
    r_dyn = TIRE_RADIUS_DYN_M
    m_effective = m + (J_WHEELS_KG_M2 / (r_dyn**2)) + (J_ENGINE_KG_M2 * (i_total**2) / (r_dyn**2))
    f_acc = m_effective * df["Acceleration_ms2"]
    f_aero = 0.5 * rho * cwA * (df["Velocity_ms"] ** 2)
    f_roll = c_r * m * g

    active_slope_pct = calculate_road_slope_percent(df, slope_percent)
    df["Slope_Pct"] = active_slope_pct
    f_slope = m * g * (active_slope_pct / 100.0)
    df["Slope_Force_N"] = f_slope
    # F-07: Hangabtrieb wirkt direkt am Rad, nicht durch den Antriebsstrang.
    # eta darf nur im Nenner stehen, wenn der Motor Hangkraft überwinden muss (bergauf).
    eta_slope = eta if active_slope_pct > 0 else 1.0
    df["Slope_Power_PS"] = ((f_slope * df["Velocity_ms"]) / eta_slope) / 735.49875

    f_total = f_acc + f_aero + f_roll + f_slope

    # 5. Power Calculations & DIN 70020 Weather Normalization
    p_wheel_watts = f_total * df["Velocity_ms"]
    p_engine_watts = p_wheel_watts / eta

    k_norm = calculate_weather_correction_factor(temp_c, pressure_hpa, norm_standard)
    df["Weather_K_Norm"] = k_norm
    df["Ambient_Temp_C"] = float(temp_c) if temp_c is not None else 20.0
    df["Ambient_Pressure_hPa"] = float(pressure_hpa) if pressure_hpa is not None else 1013.25
    df["Norm_Standard"] = str(norm_standard)

    raw_ps_calc = (p_engine_watts / 735.49875).clip(lower=0.0)
    norm_ps_calc = (raw_ps_calc * k_norm).clip(lower=0.0)

    df["PS_Raw"] = np.clip(raw_ps_calc, 0.0, None)
    df["PS"] = np.clip(norm_ps_calc, 0.0, None)

    # 6. Torque Calculation (Nm)
    df["Nm_Raw"] = np.where(
        (df["RPM_smoothed"] > 500) & (df["PS_Raw"] > 0),
        (df["PS_Raw"] * 7023.5) / df["RPM_smoothed"],
        0.0
    )
    df["Nm"] = np.where(
        (df["RPM_smoothed"] > 500) & (df["PS"] > 0),
        (df["PS"] * 7023.5) / df["RPM_smoothed"],
        0.0
    )

    # 7. Peak Plausibility Verification
    df, _ = validate_and_sanitize_dyno_peaks(df)

    # 8. Wheel & Loss Power derivation (guaranteed identity: P_Motor = P_Wheel + P_Loss)
    df["P_Wheel_PS"] = np.clip(df["PS"] * eta, 0.0, None)
    df["P_Loss_PS"] = np.clip(df["PS"] * (1.0 - eta), 0.0, None)
    rpm_wheel = df["RPM_smoothed"] / i_total
    df["Nm_Wheel"] = np.where(
        (rpm_wheel > 30.0) & (df["P_Wheel_PS"] > 0.0),
        (df["P_Wheel_PS"] * 7023.5) / rpm_wheel,
        0.0
    )

    return df


def detect_dyno_pull(
    df: pd.DataFrame,
    cfg_or_min_rpm: Optional[Union[PullFilterConfig, float]] = None,
    min_duration_sec: Optional[float] = None,
    drop_threshold: Optional[float] = None,
    cfg: Optional[PullFilterConfig] = None,
    slope_percent: Optional[Union[float, str]] = None,
    temp_c: float = 20.0,
    pressure_hpa: float = 1013.25,
    norm_standard: str = "DIN70020",
    min_rpm: Optional[float] = None,
    gear: Optional[Union[int, str]] = None,
    **kwargs
) -> Tuple[pd.DataFrame, bool]:
    """
    Strict multi-condition WOT Dyno-Pull detection:
    - Monotonic velocity increase (dv/dt >= a_min)
    - Monotonic engine revving (dRPM/dt > 0)
    - Strict load-AFR validation (9.5 <= AFR <= 14.2, dynamic transient filter)
    - Minimum duration (>= 1.8s) or RPM span (>= 1500 RPM)
    Returns: (trimmed_df, is_valid_pull)
    """
    if len(df) < 10:
        logger.warning("Segment zu kurz (<10 Datenpunkte). Pull verworfen.")
        return df, False

    # Resolve config object vs keyword arguments for backward compatibility
    if isinstance(cfg_or_min_rpm, PullFilterConfig):
        cfg = cfg_or_min_rpm
    elif isinstance(cfg_or_min_rpm, (int, float)) and min_rpm is None:
        min_rpm = float(cfg_or_min_rpm)

    if cfg is None:
        cfg = DEFAULT_FILTER_CONFIG

    overrides = {}
    if min_rpm is not None:
        overrides["min_rpm"] = float(min_rpm)
    if min_duration_sec is not None:
        overrides["min_duration_sec"] = float(min_duration_sec)
    if drop_threshold is not None:
        overrides["drop_threshold_rpm"] = float(drop_threshold)
    elif "drop_threshold_rpm" in kwargs:
        overrides["drop_threshold_rpm"] = float(kwargs["drop_threshold_rpm"])

    for field_name in PullFilterConfig.__dataclass_fields__:
        if field_name in kwargs and field_name not in overrides:
            overrides[field_name] = kwargs[field_name]

    if overrides:
        current_dict = {f: getattr(cfg, f) for f in PullFilterConfig.__dataclass_fields__}
        current_dict.update(overrides)
        cfg = PullFilterConfig(**current_dict)

    df = clean_egt_data(df)

    n = len(df)
    rpm_raw = df["RPM"].values
    if "Speed_smoothed" in df.columns and (df["Speed_smoothed"] > 5.0).any():
        speed_raw = df["Speed_smoothed"].values
    elif "Speed_kmh" in df.columns:
        speed_raw = df["Speed_kmh"].values
    else:
        speed_raw = np.zeros(n)
    afr_raw = df["AFR"].values if "AFR" in df.columns else np.full(n, 12.5)

    rpm_s = smooth_signal(rpm_raw, window_length=9, polyorder=2)
    spd_s = smooth_signal(speed_raw, window_length=9, polyorder=2)
    afr_rolling = pd.Series(afr_raw).rolling(5, min_periods=1, center=True).mean().values

    # S-03: Derive real median sample interval from log timestamps instead of
    # assuming fixed dt=0.1s (10 Hz). UART jitter + Pi scheduling cause real
    # packet spacing to vary; a wrong dt scales pull_duration and avg_accel directly.
    dt = 0.1
    if "Time" in df.columns:
        try:
            t_num = pd.to_numeric(df["Time"], errors="coerce").dropna()
            if len(t_num) > 2:
                _diffs = np.diff(t_num.values)
                _diffs = _diffs[_diffs > 0]
                if len(_diffs) > 0:
                    dt = float(np.median(_diffs))
        except Exception:
            pass
    dt = max(0.05, min(0.30, dt))  # Sanity clamp: reject <50ms or >300ms

    # Compute per-sample RPM derivative for clutch-pull spike detection
    drpm_dt_arr = np.gradient(rpm_s, dt)

    best_start = None
    best_end = None
    best_rpm_gain = 0.0

    i = 0
    while i < n - 8:
        curr_rpm = rpm_s[i]
        curr_spd = spd_s[i]
        curr_afr = afr_rolling[i]
        
        # Immediate skip for coasting/Schiebebetrieb
        if curr_afr >= cfg.afr_cutoff_threshold:
            i += 1
            continue

        # Look for start of acceleration
        if curr_rpm >= cfg.min_rpm and curr_spd >= cfg.min_speed_kmh:
            start_idx = i
            peak_idx = start_idx
            peak_rpm = curr_rpm
            consecutive_lean_count = 0

            j = start_idx + 1
            while j < n:
                r_j = rpm_s[j]
                v_j = spd_s[j]
                afr_j = afr_raw[j]
                afr_roll_j = afr_rolling[j]

                # Dynamic Transient Lean Filter:
                # If AFR >= cutoff (15.0), allow up to transient_lean_max_samples (e.g. 3 samples = 0.3s)
                # only if vehicle is accelerating (v_j >= spd_s[j-1]) and RPM revving
                if afr_j >= cfg.afr_cutoff_threshold or afr_roll_j >= cfg.afr_cutoff_threshold:
                    consecutive_lean_count += 1
                    if consecutive_lean_count > cfg.transient_lean_max_samples:
                        logger.debug(f"Pull-Abbruch bei Index {j}: AFR {afr_j:.1f} > {cfg.afr_cutoff_threshold} über {consecutive_lean_count} Samples (Schiebebetrieb)")
                        break
                else:
                    consecutive_lean_count = 0

                # Hard abort 2: Major sustained deceleration (RPM drop > drop_threshold)
                if (peak_rpm - r_j) > cfg.drop_threshold_rpm:
                    break

                # Track peak RPM
                if r_j > peak_rpm:
                    peak_rpm = r_j
                    peak_idx = j

                j += 1

            # Post-loop: Clutch-pull trailing-spike trim.
            # When the clutch is released at WOT, the engine revs freely for 2-4 samples
            # BEFORE reaching the smoothed "peak". This means peak_idx itself has low dRPM/dt
            # (it's at the tip of the spike), but the 2-3 preceding samples have very high
            # dRPM/dt (the unloaded acceleration into that spike).
            # Strategy: Scan backwards from peak_idx-1. If we find a contiguous block of
            # high-dRPM samples (> clutch_pull_drpm_threshold), strip them: the true
            # pull_end is just before that block.
            if peak_idx > start_idx + 5:
                # Find the start of the trailing spike: walk back from peak_idx-1
                spike_end = peak_idx - 1
                while (spike_end > start_idx
                       and drpm_dt_arr[spike_end] > cfg.clutch_pull_drpm_threshold):
                    spike_end -= 1
                # spike_end is now the last sample BEFORE the trailing spike
                # Only apply trim if:
                # 1. We actually stripped something (spike_end < peak_idx - 1)
                # 2. The sample at spike_end has low dRPM/dt (confirming it's a plateau, not mid-burst)
                # 3. Sufficient RPM gain remains
                trimmed_gain = rpm_s[spike_end] - rpm_s[start_idx]
                if (spike_end < peak_idx - 1
                        and drpm_dt_arr[spike_end] < 500.0
                        and trimmed_gain > 1200.0):
                    logger.debug(
                        f"Kupplungsziehen-Trimming: peak_idx {peak_idx} ({rpm_s[peak_idx]:.0f} RPM) "
                        f"→ {spike_end} ({rpm_s[spike_end]:.0f} RPM); "
                        f"stripped {peak_idx - spike_end} spike samples"
                    )
                    peak_idx = spike_end
                    peak_rpm = rpm_s[spike_end]

            pull_end = peak_idx
            pull_duration = (pull_end - start_idx) * dt
            rpm_gain = peak_rpm - rpm_s[start_idx]
            speed_gain = spd_s[pull_end] - spd_s[start_idx]
            avg_accel = (speed_gain / 3.6) / pull_duration if pull_duration > 0 else 0.0
            
            segment_afrs = [a for a in afr_raw[start_idx:pull_end+1] if 8.0 < a < 22.0]
            avg_afr = np.mean(segment_afrs) if segment_afrs else 19.5

            # Multi-condition validation:
            # 1. Monotonic RPM gain / duration
            is_duration_ok = (pull_duration >= cfg.min_duration_sec) or (rpm_gain >= cfg.min_rpm_gain)
            # 2. Monotonic Speed increase (net positive acceleration and speed gain)
            is_accel_ok = (avg_accel >= cfg.min_accel_ms2 or speed_gain >= 10.0) and (speed_gain > 3.0)
            # 3. Load AFR within combustion window
            is_afr_ok = (cfg.afr_load_min <= avg_afr <= cfg.afr_load_max)

            if is_duration_ok and is_accel_ok and is_afr_ok:
                if rpm_gain > best_rpm_gain:
                    best_rpm_gain = rpm_gain
                    best_start = start_idx
                    best_end = pull_end
                    logger.info(
                        f"✅ Gültiges Dyno-Intervall gefunden: +{rpm_gain:.0f} RPM in {pull_duration:.1f}s | "
                        f"Speed +{speed_gain:.1f} km/h (a={avg_accel:.2f} m/s²) | AFR Ø {avg_afr:.2f}"
                    )

            i = max(j, peak_idx + 1)
            continue
        i += 1

    if best_start is None or best_rpm_gain < 1000.0:
        logger.warning(
            f"❌ Kein gültiger Dyno-Pull erkannt (Bedingungen nicht erfüllt: "
            f"Monotones v/RPM, AFR {cfg.afr_load_min}-{cfg.afr_load_max}, Delta RPM >= {cfg.min_rpm_gain:.0f})."
        )
        df_calc = calculate_telemetry_metrics(
            df,
            gear=gear,
            slope_percent=slope_percent,
            temp_c=temp_c,
            pressure_hpa=pressure_hpa,
            norm_standard=norm_standard
        )
        return df_calc, False

    # Calculate telemetry metrics on the full continuous series with lead-in/lead-out
    # to avoid one-sided boundary extrapolation artifacts at peak RPM.
    recalc_needed = ("PS" not in df.columns) or (
        gear is not None
        and "Detected_Gear" in df.columns
        and str(df["Detected_Gear"].iloc[0]) != str(gear)
    )
    if recalc_needed:
        df = calculate_telemetry_metrics(
            df,
            gear=gear,
            slope_percent=slope_percent,
            temp_c=temp_c,
            pressure_hpa=pressure_hpa,
            norm_standard=norm_standard
        )

    trimmed_df = df.iloc[best_start:best_end + 1].copy().reset_index(drop=True)
    return trimmed_df, True


def plot_telemetry(
    df: pd.DataFrame,
    title_suffix: str = "",
    output_path: Optional[str] = None,
    vehicle_name: str = "VMC177"
) -> None:
    """Plots telemetry data in professional dark P4-Look with engine power, wheel power, loss power, torque, AFR, and EGT."""
    plt.style.use("dark_background")

    fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(12, 10), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax2 = ax1.twinx()

    gear = df.get("Detected_Gear", pd.Series([3])).iloc[0] if "Detected_Gear" in df.columns else 3
    i_total = get_gear_total_ratio(gear)

    # 1. Power & Torque Curves (P4 Professional Layout)
    ax1.plot(df["RPM_smoothed"], df["PS"], color="#00ffcc", linewidth=3.0, label="P_Motor (DIN 70020)")
    if "P_Wheel_PS" in df.columns:
        ax1.plot(df["RPM_smoothed"], df["P_Wheel_PS"], color="#76ff03", linewidth=2.0, linestyle="--", label="P_Rad (Hinterrad)")
    if "P_Loss_PS" in df.columns:
        ax1.plot(df["RPM_smoothed"], df["P_Loss_PS"], color="#ff7043", linewidth=1.8, linestyle=":", label="P_Verlust (Schleppleistung)")
    ax2.plot(df["RPM_smoothed"], df["Nm"], color="#ffb300", linewidth=2.8, label="Drehmoment (Nm)")

    ax1.grid(True, color="#333333", linestyle="--", alpha=0.7)

    max_ps = float(df["PS"].max()) if len(df) > 0 and "PS" in df.columns else 20.0
    max_wheel_ps = float(df["P_Wheel_PS"].max()) if len(df) > 0 and "P_Wheel_PS" in df.columns else (max_ps * 0.88)
    max_loss_ps = float(df["P_Loss_PS"].max()) if len(df) > 0 and "P_Loss_PS" in df.columns else (max_ps * 0.12)
    max_nm = float(df["Nm"].max()) if len(df) > 0 and "Nm" in df.columns else 0.0
    ps_top = max(20.0, math.ceil((max_ps * 1.18) / 5.0) * 5.0)

    ax1.set_ylim(0, ps_top)
    ax2.set_ylim(0, ps_top * 2.5)

    ax1.set_title(f"StreetDyno 2.0 - {vehicle_name} Leistungsmessung{title_suffix}", fontsize=14, fontweight="bold", pad=15, color="#ffffff")
    ax1.set_ylabel("Leistung [PS]", color="#00ffcc", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Drehmoment [Nm]", color="#ffb300", fontsize=12, fontweight="bold")

    ax1.tick_params(axis="y", colors="#00ffcc")
    ax2.tick_params(axis="y", colors="#ffb300")

    if max_ps > 0 and max_nm > 0:
        peak_ps_idx = df["PS"].idxmax()
        peak_ps = df["PS"].max()
        peak_ps_rpm = df.loc[peak_ps_idx, "RPM_smoothed"]

        peak_nm_idx = df["Nm"].idxmax()
        peak_nm = df["Nm"].max()
        peak_nm_rpm = df.loc[peak_nm_idx, "RPM_smoothed"]

        slope_val = df.get("Slope_Pct", pd.Series([0.0])).iloc[0] if "Slope_Pct" in df.columns else 0.0
        p_slope_avg = df.get("Slope_Power_PS", pd.Series([0.0])).mean() if "Slope_Power_PS" in df.columns else 0.0
        slope_tag = f"\nSteigung: {slope_val:+.1f}% ({p_slope_avg:+.1f} PS)" if abs(slope_val) >= 0.1 else ""

        k_norm_val = df.get("Weather_K_Norm", pd.Series([1.0])).iloc[0] if "Weather_K_Norm" in df.columns else 1.0

        annotation_text = (
            f"🎯 Gang: {gear}. Gang (i={i_total:.2f}){slope_tag}\n"
            f"⚡ P_Motor (DIN): {peak_ps:.2f} PS @ {int(peak_ps_rpm)} U/min\n"
            f"🏁 P_Rad: {max_wheel_ps:.2f} PS | P_Verlust: {max_loss_ps:.2f} PS\n"
            f"🔧 Drehmoment: {peak_nm:.2f} Nm @ {int(peak_nm_rpm)} U/min\n"
            f"🌤️ Wetter-Faktor: k_DIN = {k_norm_val:.3f}"
        )
        ax1.text(0.02, 0.95, annotation_text, transform=ax1.transAxes, fontsize=10, verticalalignment="top",
                 bbox=dict(boxstyle="round,pad=0.5", facecolor="#1a1a1a", alpha=0.9, edgecolor="#00ffcc", linewidth=1.5))

    # Combined Legend
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper right", facecolor="#1a1a1a", edgecolor="#555555", fontsize=9)

    # 2. AFR & EGT Subplot
    ax4 = ax3.twinx()
    ax3.plot(df["RPM_smoothed"], df["AFR"], color="#ff3366", linewidth=2.2, label="AFR")
    ax4.plot(df["RPM_smoothed"], df["EGT_cleaned"], color="#ffcc00", linewidth=2.2, label="EGT (°C)")

    ax3.axhline(12.8, color="#ff3366", linestyle=":", alpha=0.6, label="Optimal AFR Last (12.8-13.0)")
    ax4.axhline(630.0, color="#ffcc00", linestyle=":", alpha=0.7, label="Kritische EGT (630°C)")

    ax3.grid(True, color="#333333", linestyle="--", alpha=0.7)
    ax3.set_ylabel("AFR", color="#ff3366", fontsize=12, fontweight="bold")
    ax4.set_ylabel("EGT [°C]", color="#ffcc00", fontsize=12, fontweight="bold")
    ax3.set_xlabel("Motordrehzahl [U/min]", fontsize=12, fontweight="bold")
    ax3.tick_params(axis="y", colors="#ff3366")
    ax4.tick_params(axis="y", colors="#ffcc00")

    handles3, labels3 = ax3.get_legend_handles_labels()
    handles4, labels4 = ax4.get_legend_handles_labels()
    ax3.legend(handles3 + handles4, labels3 + labels4, loc="upper right", facecolor="#1a1a1a", edgecolor="#555555", fontsize=9)

    plt.tight_layout()
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        plt.savefig(output_path, dpi=130, facecolor=fig.get_facecolor(), edgecolor="none")
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
        return False
    if not os.path.exists(credentials_json):
        return False
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(credentials_json, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open(spreadsheet_name).sheet1
        export_cols = ["Time", "RPM_smoothed", "PS", "Nm", "AFR", "EGT_cleaned", "Speed_smoothed"]
        available = [c for c in export_cols if c in df.columns]
        data_to_export = [available] + df[available].fillna(0).values.tolist()
        sheet.clear()
        sheet.update("A1", data_to_export)
        return True
    except Exception as e:
        logger.error(f"Fehler beim Google Sheets Export: {e}")
        return False
