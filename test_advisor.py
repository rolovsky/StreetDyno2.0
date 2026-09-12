import sys, os
import pandas as pd
from src.data.analyzer_logic import clean_egt_data, calculate_telemetry_metrics, detect_dyno_pull
from src.data.jetting_advisor import analyze_carb_jetting

fpath = sys.argv[1] if len(sys.argv) > 1 else "/home/rolovsky/streetdyno2.0/logs/dyno_log_20260612-163455.csv"
df = pd.read_csv(fpath)
df.columns = [c.strip() for c in df.columns]
df = clean_egt_data(df)
df = calculate_telemetry_metrics(df)
t, det = detect_dyno_pull(df)

res = analyze_carb_jetting(t)
print("=" * 60)
print("OVERALL STATUS:", res["overall_status"])
print("VERDICT:", res["overall_verdict"])
print("AVG AFR:", res["avg_total_afr"])
print("=" * 60)
for z in res["zones"]:
    print(f"ZONE: {z['name']} ({z['rpm_range']})")
    print(f"  Component: {z['component']}")
    print(f"  AFR: Mean={z['mean_afr']} (Target: {z['target']}) -> {z['status_text']}")
    print(f"  Advice: {z['advice']}")
    print("-" * 60)
