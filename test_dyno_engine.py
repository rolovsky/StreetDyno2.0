#!/usr/bin/env python3
import os
import sys
import glob
import pandas as pd
import matplotlib
matplotlib.use('Agg')

# Find base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
PLOTS_DIR = os.path.join(BASE_DIR, "plots")

sys.path.insert(0, SRC_DIR)

import config
from data.analyzer_logic import (
    clean_egt_data,
    calculate_telemetry_metrics,
    detect_dyno_pull,
    detect_gear_ratio,
    plot_telemetry,
    export_to_gsf_dyno_csv,
    get_theoretical_rpm_per_kmh
)

def run_tests():
    print("=" * 65)
    print("       STREETDYNO 2.0 PHYSICAL ENGINE VERIFICATION")
    print("=" * 65)
    print(f"Vehicle: {config.VEHICLE_NAME} ({getattr(config, 'VEHICLE_DESCRIPTION', '')})")
    print(f"Total Mass: {config.TOTAL_MASS_KG} kg | Tire: {config.TIRE_CIRCUMFERENCE_M} m | Primary: {config.PRIMARY_RATIO:.4f}")
    print("Theoretical Gear Ratios (RPM / km/h):")
    for g in sorted(config.GEAR_RATIOS.keys()):
        ratio = get_theoretical_rpm_per_kmh(g)
        print(f"  - Gang {g}: {ratio:.1f} RPM/(km/h)")
    print("-" * 65)

    test_pattern = os.path.join(LOGS_DIR, "dyno_log_*.csv")
    all_logs = sorted(glob.glob(test_pattern))
    
    if not all_logs:
        print(f"[ERROR] No logs found matching {test_pattern}")
        return

    # Select representative logs (e.g. latest runs and largest runs)
    selected_logs = [all_logs[-1], all_logs[-2], all_logs[-3]] if len(all_logs) >= 3 else all_logs

    os.makedirs(os.path.join(PLOTS_DIR, "test_verification"), exist_ok=True)

    tested = 0
    for log_path in selected_logs:
        fname = os.path.basename(log_path)
        print(f"\n📂 Testing Log: {fname}")
        df = pd.read_csv(log_path)
        df.columns = [c.strip() for c in df.columns]

        if len(df) < 20:
            print(f"   [SKIP] Too short ({len(df)} rows)")
            continue

        # 1. Test EGT cleaning
        df = clean_egt_data(df)

        # 2. Test metrics calculation
        df = calculate_telemetry_metrics(df)

        # 3. Test Pull Detection
        trimmed_df, detected = detect_dyno_pull(df)
        gear, i_total, median_ratio, conf = detect_gear_ratio(trimmed_df if detected else df)

        print(f"   Status: {'✅ PULL DETECTED' if detected else '⚠️ Full Log Fallback'}")
        print(f"   Detected Gear: {gear}. Gang (i={i_total:.2f}) | RPM/kmh: {median_ratio:.1f} (Confidence: {conf*100:.0f}%)")
        print(f"   Pull Points: {len(trimmed_df)} rows")
        
        peak_ps = trimmed_df['PS'].max()
        peak_ps_rpm = trimmed_df.loc[trimmed_df['PS'].idxmax(), 'RPM_smoothed']
        peak_nm = trimmed_df['Nm'].max()
        peak_nm_rpm = trimmed_df.loc[trimmed_df['Nm'].idxmax(), 'RPM_smoothed']
        avg_afr = trimmed_df['AFR'].mean()
        max_egt = trimmed_df['EGT_cleaned'].max()

        print(f"   ⚡ Peak Power:      {peak_ps:.1f} PS @ {int(peak_ps_rpm)} U/min")
        print(f"   🔧 Peak Torque:     {peak_nm:.1f} Nm @ {int(peak_nm_rpm)} U/min")
        print(f"   📊 Mean AFR:        {avg_afr:.2f}")
        print(f"   🔥 Max EGT:         {max_egt:.1f} °C")

        # 4. Generate Plot
        plot_out = os.path.join(PLOTS_DIR, "test_verification", f"test_{os.path.splitext(fname)[0]}.png")
        plot_telemetry(trimmed_df, f" ({gear}. Gang Test)", plot_out)
        print(f"   📈 Plot saved to: {plot_out}")

        # 5. Test GSF-Dyno export
        gsf_out = os.path.join(LOGS_DIR, f"test_{os.path.splitext(fname)[0]}_gsf.csv")
        export_to_gsf_dyno_csv(trimmed_df, gsf_out)
        print(f"   💾 GSF CSV exported: {gsf_out}")

        tested += 1

    print("\n" + "=" * 65)
    print(f"✅ VERIFICATION COMPLETE: {tested} test logs evaluated successfully.")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
