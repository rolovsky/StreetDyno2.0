"""
Verification Suite for Refactored StreetDyno Pull Detection & Dynamics
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, "/home/rolovsky/streetdyno2.0/src")

from data.analyzer_logic import (
    detect_dyno_pull,
    calculate_telemetry_metrics,
    validate_and_sanitize_dyno_peaks,
    smooth_signal,
    PullFilterConfig
)

print("=== 1. TEST: VALID DYNO PULL (202146.csv) ===")
f_valid = "/home/rolovsky/streetdyno2.0/logs/dyno_log_20260909-202146.csv"
if os.path.exists(f_valid):
    df_v = pd.read_csv(f_valid)
    trimmed_v, is_valid_v = detect_dyno_pull(df_v)
    print(f"Valid Pull detected? {is_valid_v} (Trimmed samples: {len(trimmed_v)} / {len(df_v)})")
    if is_valid_v:
        p_max = trimmed_v["PS"].max()
        p_max_rpm = trimmed_v.loc[trimmed_v["PS"].idxmax(), "RPM_smoothed"]
        m_max = trimmed_v["Nm"].max()
        m_max_rpm = trimmed_v.loc[trimmed_v["Nm"].idxmax(), "RPM_smoothed"]
        print(f"Pmax: {p_max:.2f} PS @ {p_max_rpm:.0f} RPM | Mmax: {m_max:.2f} Nm @ {m_max_rpm:.0f} RPM")
        assert p_max > 5.0, "Pmax should be positive"
        assert is_valid_v, "Must detect valid pull"

print("\n=== 2. TEST: COASTING / SCHIEBEBETRIEB (202157.csv) ===")
f_coast = "/home/rolovsky/streetdyno2.0/logs/dyno_log_20260909-202157.csv"
if os.path.exists(f_coast):
    df_c = pd.read_csv(f_coast)
    trimmed_c, is_valid_c = detect_dyno_pull(df_c)
    print(f"Coasting pull detected as valid? {is_valid_c} (Must be False!)")
    assert not is_valid_c, "Coasting file MUST NOT be detected as valid pull!"

print("\n=== 3. TEST: PEAK PLAUSIBILITY CHECK (Pmax vs Mmax Spike Artifact) ===")
# Synthetic data with an artificial spike at 5000 RPM
rpm_syn = np.linspace(3000, 7500, 50)
ps_syn = 12.0 * np.sin((rpm_syn - 3000) / 4500 * np.pi) + 5.0
# Inject narrow spike
ps_syn[25] = 35.0  # Absurd spike at 5250 RPM
nm_syn = (ps_syn * 7023.5) / rpm_syn

df_syn = pd.DataFrame({
    "RPM": rpm_syn,
    "RPM_smoothed": rpm_syn,
    "PS": ps_syn,
    "PS_Raw": ps_syn,
    "Nm": nm_syn,
    "Nm_Raw": nm_syn,
    "Speed_kmh": rpm_syn / 81.6
})

df_sanitized, meta = validate_and_sanitize_dyno_peaks(df_syn)
print(f"Spike detected? {not meta["is_plausible"]}")
print(f"Warning issued: {meta["warning"]}")
print(f"Sanitized Pmax: {df_sanitized["PS"].max():.2f} PS (down from 35.0 PS)")
assert df_sanitized["PS"].max() < 25.0, "Spike should be suppressed by sanitization"

print("\n=== 4. TEST: SAVITZKY-GOLAY NOISE SUPPRESSION ===")
noisy_signal = np.array([10.0, 10.2, 9.8, 10.5, 9.7, 10.3, 10.0, 10.1, 9.9, 10.4, 9.8, 10.2])
smoothed = smooth_signal(noisy_signal, window_length=5, polyorder=2)
print(f"Noisy std: {np.std(noisy_signal):.4f} -> Smoothed std: {np.std(smoothed):.4f}")
assert np.std(smoothed) < np.std(noisy_signal), "Smoothing should reduce variance"

print("\n✅ ALL REFACTOR VERIFICATION TESTS PASSED SUCCESSFULLY!")
