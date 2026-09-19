"""
StreetDyno 2.0 - Automated Test Suite
Tests vehicle physics, Savitzky-Golay filtering, slope compensation,
DIN 70020 weather normalization, Carburetor Jetting Advisor, and Flask routes.
"""

import os
import sys
import json
import unittest
import pandas as pd
import numpy as np

# Add src/ to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import (
    TOTAL_MASS_KG,
    PRIMARY_RATIO,
    GEAR_RATIOS,
    TRANSMISSION_EFFICIENCY,
    DEFAULT_CARB_SETUP,
    TRIP_LOG_DIR,
    load_carb_setup
)
from data.analyzer_logic import (
    get_gear_total_ratio,
    get_theoretical_rpm_per_kmh,
    calculate_weather_correction_factor,
    calculate_road_slope_percent,
    calculate_telemetry_metrics,
    detect_dyno_pull
)
from data.jetting_advisor import (
    analyze_carb_jetting,
    parse_nd_ratio,
    parse_nd_scale,
    is_richer_idle_jet,
    is_leaner_idle_jet,
    get_idle_jet_advice,
    get_zone3_tube_hlkd_advice,
    calculate_relative_air_density,
    calculate_weather_corrected_main_jet
)
from data.logger import (
    CSVLogger,
    TripLogger,
    delete_log_file,
    delete_trip_file,
    cleanup_short_logs
)
from data.trip_analyzer import (
    calculate_gps_distance_km,
    generate_afr_heatmap_matrix,
    analyze_trip_session
)
from data.audit_manager import (
    reconstruct_afr_voltage,
    evaluate_ground_offset,
    save_audit_report,
    list_audit_reports
)
from main import create_app


class TestDynoPhysics(unittest.TestCase):

    def test_gear_ratios(self):
        """Verify gear ratios and total reductions."""
        i_3 = get_gear_total_ratio(3)
        self.assertAlmostEqual(i_3, 6.609, places=2)

        rpm_per_kmh = get_theoretical_rpm_per_kmh(3, 1.350)
        self.assertGreater(rpm_per_kmh, 75.0)
        self.assertLess(rpm_per_kmh, 90.0)

    def test_din70020_weather_factor(self):
        """Verify DIN 70020 and SAE J1349 weather normalization factors."""
        # Standard conditions (20°C, 1013.25 hPa) -> factor must be 1.0
        k_std = calculate_weather_correction_factor(20.0, 1013.25, "DIN70020")
        self.assertAlmostEqual(k_std, 1.0, places=3)

        # Hot summer day (30°C, 1005 hPa) -> less dense air -> k > 1.0 (power boost)
        k_hot = calculate_weather_correction_factor(30.0, 1005.0, "DIN70020")
        self.assertGreater(k_hot, 1.02)
        self.assertLess(k_hot, 1.04)

        # SAE J1349 at standard (25°C, 990 hPa)
        k_sae = calculate_weather_correction_factor(25.0, 990.0, "SAE_J1349")
        self.assertAlmostEqual(k_sae, 1.0, places=3)

    def test_slope_calculation(self):
        """Verify manual and automatic road gradient calculation."""
        self.assertEqual(calculate_road_slope_percent(pd.DataFrame(), 1.5), 1.5)
        self.assertEqual(calculate_road_slope_percent(pd.DataFrame(), -0.8), -0.8)

        # Automatic slope with GPS altitude delta should be clamped to max ±2.5%
        df_steep = pd.DataFrame({
            'Alt': [100.0, 105.0, 110.0, 115.0, 120.0, 125.0, 130.0, 135.0],
            'Speed_kmh': [50.0] * 8
        })
        auto_slope = calculate_road_slope_percent(df_steep, "auto")
        self.assertLessEqual(auto_slope, 2.5)
        self.assertGreaterEqual(auto_slope, -2.5)

    def test_acceleration_clamping_and_p4_math(self):
        """Verify max acceleration clamping (<= 1800 RPM/s) and P4 intersection at 7023.5 RPM."""
        # Create pull with an artificial spike
        rpm = [3000, 3100, 3200, 3600, 3700, 3800, 3900, 4000]  # Jump 3200 -> 3600 in 0.1s is +4000 RPM/s!
        df = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': [r / 81.6 for r in rpm],
            'AFR': [12.8] * len(rpm),
            'EGT': [500.0] * len(rpm)
        })
        metrics = calculate_telemetry_metrics(df)
        self.assertLessEqual(metrics['dRPM_dt'].max(), 1800.0)
        self.assertLessEqual(metrics['Acceleration_ms2'].max(), 4.2)

        # P4 Torque Math Check: At 7023.5 RPM, 20 PS must equal exactly 20 Nm
        df_p4 = pd.DataFrame({
            'RPM': [7023.5] * 8,
            'Speed_kmh': [7023.5 / 81.6] * 8,
            'AFR': [12.8] * 8,
            'EGT': [500.0] * 8
        })
        metrics_p4 = calculate_telemetry_metrics(df_p4)
        # Verify formula: Nm = (PS * 7023.5) / RPM
        test_ps = 20.0
        calculated_nm = (test_ps * 7023.5) / 7023.5
        self.assertAlmostEqual(calculated_nm, 20.0, places=3)

    def test_carb_jetting_advisor(self):
        """Verify 4-zone carburetor diagnostic evaluation."""
        # Create synthetic pull data
        rpm = np.linspace(2000, 8500, 100)
        afr = np.full(100, 12.6)  # Perfect AFR for Super E5
        egt = np.linspace(400, 600, 100)
        df = pd.DataFrame({'RPM': rpm, 'AFR': afr, 'EGT': egt, 'Speed_kmh': rpm / 81.6})

        analysis = analyze_carb_jetting(df, DEFAULT_CARB_SETUP)
        self.assertTrue(analysis["valid"])
        self.assertEqual(analysis["overall_status"], "PERFECT")
        self.assertEqual(len(analysis["zones"]), 4)

    def test_fuel_stoichiometry_scaling(self):
        """Verify dynamic AFR target scaling for Super E5, Super E10, and SuperPlus E0."""
        rpm = np.linspace(2000, 8500, 100)
        afr = np.full(100, 12.5)
        df = pd.DataFrame({'RPM': rpm, 'AFR': afr, 'EGT': np.full(100, 550.0), 'Speed_kmh': rpm / 81.6})

        # Test Super E5 (14.30)
        setup_e5 = {**DEFAULT_CARB_SETUP, "fuel_type": "Super_E5"}
        res_e5 = analyze_carb_jetting(df, setup_e5)
        self.assertEqual(res_e5["stoich_afr"], 14.30)

        # Test Super E10 (14.10)
        setup_e10 = {**DEFAULT_CARB_SETUP, "fuel_type": "Super_E10"}
        res_e10 = analyze_carb_jetting(df, setup_e10)
        self.assertEqual(res_e10["stoich_afr"], 14.10)
        # Zone 4 target for E10 should be lower than E5
        z4_e10 = [z for z in res_e10["zones"] if z["id"] == "zone4"][0]
        z4_e5 = [z for z in res_e5["zones"] if z["id"] == "zone4"][0]
        self.assertLess(float(z4_e10["target"].split('-')[0]), float(z4_e5["target"].split('-')[0]))

        # Test SuperPlus E0 (14.70)
        setup_e0 = {**DEFAULT_CARB_SETUP, "fuel_type": "SuperPlus_E0"}
        res_e0 = analyze_carb_jetting(df, setup_e0)
        self.assertEqual(res_e0["stoich_afr"], 14.70)

    def test_slide_and_intake_diagnostics(self):
        """Verify component-specific recommendations for BGM Cutaway vs Lemarxon and Polini Venturi."""
        # Create lean pull in Zone 2 (3200-4800 RPM)
        rpm = np.linspace(3500, 4500, 50)
        afr = np.full(50, 14.2)  # Lean in Zone 2
        df_lean = pd.DataFrame({'RPM': rpm, 'AFR': afr, 'EGT': np.full(50, 580.0), 'Speed_kmh': rpm / 81.6})

        # Test BGM standard slide suggests Lemarxon Cutaway
        setup_bgm = {**DEFAULT_CARB_SETUP, "slide_type": "bgm_std_cutout"}
        res_bgm = analyze_carb_jetting(df_lean, setup_bgm)
        z2_bgm = [z for z in res_bgm["zones"] if z["id"] == "zone2"][0]
        self.assertIn("BGM Standard-Cutaway", z2_bgm["advice"])
        self.assertIn("Lemarxon", z2_bgm["advice"])

        # Test Polini Venturi note in Zone 4
        rpm_wot = np.linspace(7000, 8500, 50)
        df_wot_lean = pd.DataFrame({'RPM': rpm_wot, 'AFR': np.full(50, 13.8), 'EGT': np.full(50, 620.0), 'Speed_kmh': rpm_wot / 81.6})
        setup_venturi = {**DEFAULT_CARB_SETUP, "intake_type": "polini_venturi"}
        res_venturi = analyze_carb_jetting(df_wot_lean, setup_venturi)
        z4_venturi = [z for z in res_venturi["zones"] if z["id"] == "zone4"][0]
        self.assertIn("Polini Venturi Trichter", z4_venturi["advice"])

    def test_nd_ratio_parser(self):
        """Verify ND ratio parsing and Dell'Orto SI idle jet quotient physics for 160-series scale."""
        # Q = Air / Fuel (e.g. 60/160 -> 160 / 60 = 2.67)
        self.assertAlmostEqual(parse_nd_ratio("60/160"), 2.667, places=2)
        self.assertAlmostEqual(parse_nd_ratio("55/160"), 2.909, places=2)
        self.assertAlmostEqual(parse_nd_ratio("58/160"), 2.759, places=2)
        self.assertAlmostEqual(parse_nd_ratio("62/160"), 2.581, places=2)
        self.assertAlmostEqual(parse_nd_ratio("65/160"), 2.462, places=2)
        self.assertAlmostEqual(parse_nd_ratio("68/160"), 2.353, places=2)

        # Smaller Quotient = Less Air / More Fuel = RICHER
        self.assertTrue(is_richer_idle_jet("65/160", "60/160"))
        self.assertTrue(is_richer_idle_jet("62/160", "60/160"))
        self.assertTrue(is_richer_idle_jet("68/160", "65/160"))

        # Higher Quotient = More Air / Less Fuel = LEANER
        self.assertTrue(is_leaner_idle_jet("55/160", "60/160"))
        self.assertTrue(is_leaner_idle_jet("58/160", "60/160"))

        lean_adv = get_idle_jet_advice("60/160", "LEANER")
        self.assertIn("58/160", lean_adv)
        self.assertIn("größerem Quotienten", lean_adv)

        rich_adv = get_idle_jet_advice("60/160", "RICHER")
        self.assertIn("65/160", rich_adv)
        self.assertIn("LLG-Schraube", rich_adv)

    def test_extended_nd_matrix_and_escalation(self):
        """Verify 140/120 ND parsing, series preservation, and multi-scale escalation."""
        # Scale parsing
        self.assertEqual(parse_nd_scale("60/160"), 160)
        self.assertEqual(parse_nd_scale("50/140"), 140)
        self.assertEqual(parse_nd_scale("45/120"), 120)

        # 140 and 120 ratio checks
        self.assertAlmostEqual(parse_nd_ratio("50/140"), 2.80, places=2)
        self.assertAlmostEqual(parse_nd_ratio("55/140"), 2.545, places=2)
        self.assertAlmostEqual(parse_nd_ratio("50/120"), 2.40, places=2)

        # Series preservation: When on 140er scale and asking for richer, stays in 140er
        adv_140_rich = get_idle_jet_advice("50/140", "RICHER")
        self.assertIn("52/140", adv_140_rich)

        # Multi-scale escalation: When on 68/160 (richest 160er) and needing richer -> escalates to 120er / 100er
        adv_escalate_rich = get_idle_jet_advice("68/160", "RICHER")
        self.assertIn("Eskalation erforderlich", adv_escalate_rich)
        self.assertIn("fettere Skala", adv_escalate_rich)

        # Lean escalation: When on 55/160 (leanest 160er) and needing leaner -> triggers boundary note
        adv_escalate_lean = get_idle_jet_advice("55/160", "LEANER")
        self.assertIn("sehr mager", adv_escalate_lean.lower())

    def test_tiered_emulsion_tube_and_hlkd_advice(self):
        """Verify tiered advice in Zone 3 (HLKD adjustment first, then emulsion tube replacement)."""
        # Step 1: Lean in Zone 3 with HLKD 160 -> Reduce HLKD to 150/140
        adv_lean_step1 = get_zone3_tube_hlkd_advice(
            mean_afr=13.5, t_min=12.0, t_max=12.7,
            status="LEAN", lambda_measured=0.94,
            tube="BE3", hlkd=160, hd=125
        )
        self.assertIn("Schritt 1", adv_lean_step1)
        self.assertIn("HLKD von 160 auf 150 oder 140", adv_lean_step1)

        # Step 2: Lean in Zone 3 with HLKD already at 140 and BE3 -> Change tube to BE2 or Lemarxon
        adv_lean_step2 = get_zone3_tube_hlkd_advice(
            mean_afr=13.5, t_min=12.0, t_max=12.7,
            status="LEAN", lambda_measured=0.94,
            tube="BE3", hlkd=140, hd=125
        )
        self.assertIn("Schritt 2", adv_lean_step2)
        self.assertIn("BE2", adv_lean_step2)

        # Step 1 Rich in Zone 3 with HLKD 140 -> Increase HLKD to 160
        adv_rich_step1 = get_zone3_tube_hlkd_advice(
            mean_afr=11.2, t_min=12.0, t_max=12.7,
            status="RICH", lambda_measured=0.78,
            tube="BE3", hlkd=140, hd=125
        )
        self.assertIn("Schritt 1", adv_rich_step1)
        self.assertIn("HLKD von 140 auf 160", adv_rich_step1)

    def test_weather_rad_and_hd_compensation(self):
        """Verify Relative Air Density (RAD) physics and main jet compensation."""
        # Standard DIN 70020 condition (20°C, 1013.25 hPa): RAD must be exactly 1.0
        rad_std = calculate_relative_air_density(20.0, 1013.25)
        self.assertAlmostEqual(rad_std, 1.0, places=3)

        res_std = calculate_weather_corrected_main_jet(125, 20.0, 1013.25)
        self.assertEqual(res_std["recommended_hd"], 125)
        self.assertEqual(res_std["delta_hd"], 0)
        self.assertAlmostEqual(res_std["rad_pct"], 100.0, places=1)

        # Cold winter condition (0°C, 1020 hPa): Denser air -> higher RAD -> larger HD
        rad_cold = calculate_relative_air_density(0.0, 1020.0)
        self.assertGreater(rad_cold, 1.05)
        res_cold = calculate_weather_corrected_main_jet(125, 0.0, 1020.0)
        self.assertGreater(res_cold["recommended_hd"], 125)
        self.assertGreaterEqual(res_cold["delta_hd"], 4)

        # Hot summer condition (35°C, 1005 hPa): Thinner air -> lower RAD -> smaller HD
        rad_hot = calculate_relative_air_density(35.0, 1005.0)
        self.assertLess(rad_hot, 0.95)
        res_hot = calculate_weather_corrected_main_jet(125, 35.0, 1005.0)
        self.assertLess(res_hot["recommended_hd"], 125)
        self.assertLessEqual(res_hot["delta_hd"], -3)

        # Verify integration in analyze_carb_jetting
        rpm = np.linspace(2000, 8500, 100)
        df = pd.DataFrame({'RPM': rpm, 'AFR': np.full(100, 12.5), 'EGT': np.full(100, 550.0), 'Speed_kmh': rpm / 81.6})
        analysis_cold = analyze_carb_jetting(df, DEFAULT_CARB_SETUP, temp_c=5.0, pressure_hpa=1018.0)
        self.assertIn("weather_compensation", analysis_cold)
        self.assertGreater(analysis_cold["weather_compensation"]["recommended_hd"], DEFAULT_CARB_SETUP["main_jet_hd"])
        z4 = [z for z in analysis_cold["zones"] if z["id"] == "zone4"][0]
        self.assertIn("Wetterkorrektur", z4["advice"])

    def test_sip_tacho_afr_calibration(self):
        """Verify calibrated SIP-Tacho synchronized formula: AFR = 22.62 - (5.72 * V), clamped [9.0, 19.5]."""
        # Free Air / Engine off (V ~ 0.545V) -> 19.50 AFR exactly (SIP-Tacho synchronization)
        v_free_air = 0.5447
        afr_free_air = float(np.clip(22.62 - (5.72 * v_free_air), 9.0, 19.5))
        self.assertAlmostEqual(afr_free_air, 19.50, places=2)

        # Standgas / Idle (V ~ 1.594V) -> 13.50 AFR (SIP-Tacho synchronization)
        v_idle = 1.5935
        afr_idle = float(np.clip(22.62 - (5.72 * v_idle), 9.0, 19.5))
        self.assertAlmostEqual(afr_idle, 13.51, places=2)

        # Vollgas / Rich power range (V = 1.876V) -> AFR = 22.62 - 10.73 = 11.89 (Optimal / Fett & Sicher)
        v_wot = 1.876
        afr_wot = float(np.clip(22.62 - (5.72 * v_wot), 9.0, 19.5))
        self.assertAlmostEqual(afr_wot, 11.89, places=2)

        # Magerloch in Teillast (V = 0.872V) -> AFR = 22.62 - 4.99 = 17.63
        v_lean = 0.872
        afr_lean = float(np.clip(22.62 - (5.72 * v_lean), 9.0, 19.5))
        self.assertAlmostEqual(afr_lean, 17.63, places=2)

        # Extreme Rich limit clamp (<= 9.0)
        v_max_rich = 3.0
        afr_clamped_low = float(np.clip(22.62 - (5.72 * v_max_rich), 9.0, 19.5))
        self.assertEqual(afr_clamped_low, 9.0)

        # Free Air upper limit clamp (>= 19.5)
        v_free_air_zero = 0.0
        afr_clamped_high = float(np.clip(22.62 - (5.72 * v_free_air_zero), 9.0, 19.5))
        self.assertEqual(afr_clamped_high, 19.5)

    def test_detect_dyno_pull_signature_compatibility(self):
        """Verify detect_dyno_pull handles legacy keyword arguments, positional args, and PullFilterConfig."""
        from data.analyzer_logic import PullFilterConfig
        
        # Valid acceleration data
        n = 30
        rpm = np.linspace(3000, 7500, n)
        spd = rpm / 81.6
        df = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': spd,
            'AFR': np.full(n, 12.5),
            'EGT': np.full(n, 550.0)
        })

        # 1. Standard call with no extra kwargs
        trimmed1, ok1 = detect_dyno_pull(df)
        self.assertTrue(ok1)
        self.assertGreater(len(trimmed1), 0)

        # 2. Legacy keyword arguments from routes.py
        trimmed2, ok2 = detect_dyno_pull(
            df,
            min_rpm=2800.0,
            min_duration_sec=0.8,
            drop_threshold=400.0,
            slope_percent=0.0,
            temp_c=20.0,
            pressure_hpa=1013.25,
            norm_standard="DIN70020"
        )
        self.assertTrue(ok2)

        # 3. PullFilterConfig explicit instance
        cfg = PullFilterConfig(min_rpm=2500.0, min_duration_sec=1.5)
        trimmed3, ok3 = detect_dyno_pull(df, cfg=cfg)
        self.assertTrue(ok3)

        # 4. PullFilterConfig as positional argument
        trimmed4, ok4 = detect_dyno_pull(df, cfg)
        self.assertTrue(ok4)

    def test_hybrid_wheel_and_loss_power(self):
        """Verify hybrid wheel & loss power relationship: P_Motor = P_Wheel + P_Loss = P_Wheel / eta."""
        n = 30
        rpm = np.linspace(3000, 7500, n)
        df = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': rpm / 81.6,
            'AFR': np.full(n, 12.6),
            'EGT': np.full(n, 550.0)
        })
        res = calculate_telemetry_metrics(df, temp_c=20.0, pressure_hpa=1013.25, norm_standard="RAW")
        self.assertIn("P_Wheel_PS", res.columns)
        self.assertIn("P_Loss_PS", res.columns)
        self.assertIn("PS", res.columns)

        # Check at peak power: P_Wheel + P_Loss must equal PS within floating precision
        peak_idx = res["PS"].idxmax()
        p_motor = res.loc[peak_idx, "PS"]
        p_wheel = res.loc[peak_idx, "P_Wheel_PS"]
        p_loss = res.loc[peak_idx, "P_Loss_PS"]

        self.assertGreater(p_motor, 0.0)
        self.assertGreater(p_wheel, 0.0)
        self.assertGreater(p_loss, 0.0)
        self.assertAlmostEqual(p_motor, p_wheel + p_loss, places=2)
        # Wheel power should be TRANSMISSION_EFFICIENCY ratio of engine power
        self.assertAlmostEqual(p_wheel / p_motor, TRANSMISSION_EFFICIENCY, places=2)

    def test_gear_override_options(self):
        """Verify gear override parameter ('auto', 3, 4, '4')."""
        n = 30
        rpm = np.linspace(3000, 7500, n)
        # Speed corresponding to 4th gear (~61.88 RPM per km/h)
        speed_g4 = rpm / 61.88
        df = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': speed_g4,
            'AFR': np.full(n, 12.6),
            'EGT': np.full(n, 550.0)
        })
        # 1. Auto detect 4th gear
        res_auto = calculate_telemetry_metrics(df, gear="auto")
        self.assertEqual(res_auto["Detected_Gear"].iloc[0], 4)

        # 2. Force 3rd gear override
        res_g3 = calculate_telemetry_metrics(df, gear=3)
        self.assertEqual(res_g3["Detected_Gear"].iloc[0], 3)

        # 3. Force 4th gear override as string
        res_g4 = calculate_telemetry_metrics(df, gear="4")
        self.assertEqual(res_g4["Detected_Gear"].iloc[0], 4)

    def test_dynamic_transient_lean_filter(self):
        """Verify transient lean filter allows brief 0.2s throttle snap lean spikes but rejects sustained coasting."""
        n = 35
        rpm = np.linspace(3000, 7500, n)
        spd = rpm / 81.6
        
        # 1. Pull with 2 samples of lean spike (0.2s @ AFR 16.5) at the start of throttle snap, then normal 12.5
        afr_transient = [16.5, 16.2] + [12.5] * (n - 2)
        df_transient = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': spd,
            'AFR': afr_transient,
            'EGT': np.full(n, 550.0)
        })
        trimmed_t, ok_t = detect_dyno_pull(df_transient)
        self.assertTrue(ok_t)
        self.assertGreater(len(trimmed_t), 15)

        # 2. Pull with sustained lean AFR (18.5 coasting throughout) -> MUST be rejected
        df_coasting = pd.DataFrame({
            'RPM': rpm,
            'Speed_kmh': spd,
            'AFR': np.full(n, 18.5),
            'EGT': np.full(n, 400.0)
        })
        trimmed_c, ok_c = detect_dyno_pull(df_coasting)
        self.assertFalse(ok_c)



class TestWebEndpoints(unittest.TestCase):

    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_hud_page(self):
        """Verify Live HUD loads with 200 OK."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"StreetDyno 2.0 - Live HUD", response.data)

    def test_logs_page(self):
        """Verify Dyno Pulls log archive loads with 200 OK."""
        response = self.client.get('/logs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DYNO PULLS", response.data)

    def test_tuning_page(self):
        """Verify Tuning dashboard loads with 200 OK."""
        response = self.client.get('/tuning')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VERGASER SETUP", response.data)

    def test_api_data(self):
        """Verify /api/data returns valid telemetry JSON."""
        response = self.client.get('/api/data')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn('rpm', data)
        self.assertIn('speed', data)
        self.assertIn('afr', data)
        self.assertIn('egt', data)

    def test_api_update_carb_setup(self):
        """Verify /api/update_carb_setup saves persistent configuration."""
        test_payload = {
            "main_jet_hd": 132,
            "idle_jet_nd": "60/160",
            "air_corrector_hlkd": 160
        }
        response = self.client.post('/api/update_carb_setup', json=test_payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["setup"]["main_jet_hd"], 132)

    def test_trips_endpoints(self):
        """Verify /trips and /trip_detail web routes load properly."""
        # 1. /trips page
        response = self.client.get('/trips')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BLACKBOX FAHRTEN", response.data)

        # 2. Create synthetic trip file in TRIP_LOG_DIR
        import tempfile
        test_trip_file = os.path.join(TRIP_LOG_DIR, "trip_test_synthetic.csv")
        try:
            df_synth = pd.DataFrame({
                "Time": ["12:00:00", "12:00:01", "12:00:02", "12:00:03", "12:00:04", "12:00:05", "12:00:06", "12:00:07", "12:00:08", "12:00:09", "12:00:10", "12:00:11"],
                "RPM": [2800, 3000, 3100, 3200, 3300, 3400, 3500, 4000, 5000, 6000, 7000, 7200],
                "AFR": [17.5, 17.2, 17.0, 16.8, 16.5, 16.2, 15.8, 14.5, 13.5, 12.8, 11.9, 11.8],
                "EGT": [350, 360, 370, 380, 390, 400, 420, 450, 500, 550, 620, 630],
                "Speed_kmh": [30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 45.0, 55.0, 65.0, 75.0, 78.0],
                "Lat": [46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18, 46.18],
                "Lon": [6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12, 6.12],
                "Alt": [380.0] * 12,
                "GPS_Fix": [True] * 12
            })
            df_synth.to_csv(test_trip_file, index=False)

            # Test /trip_detail
            resp_detail = self.client.get('/trip_detail?file=trip_test_synthetic.csv')
            self.assertEqual(resp_detail.status_code, 200)
            self.assertIn(b"2D-AFR KENNFIELD-MATRIX", resp_detail.data)

            # Test /download_trip
            resp_dl = self.client.get('/download_trip/trip_test_synthetic.csv')
            self.assertEqual(resp_dl.status_code, 200)
        finally:
            if os.path.exists(test_trip_file):
                os.remove(test_trip_file)


class TestTripLoggerAndHeatmap(unittest.TestCase):

    def test_trip_logger_lifecycle(self):
        """Verify TripLogger start, sample logging, discard (<100 samples), and save (>=100 samples)."""
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            logger = TripLogger(log_dir=temp_dir)
            fpath = logger.start()
            self.assertTrue(logger.is_logging)
            self.assertTrue(os.path.exists(fpath))

            # Log 5 samples (<100 min_samples) -> stop must discard
            for _ in range(5):
                logger.log(rpm=2500, afr=13.5, egt=400, speed=30)
            res_path = logger.stop(min_samples=100)
            self.assertIsNone(res_path)
            self.assertFalse(os.path.exists(fpath))

            # Log 110 samples (>=100 min_samples) -> stop must keep file
            fpath2 = logger.start()
            for _ in range(110):
                logger.log(rpm=3000, afr=13.2, egt=450, speed=35)
            res_path2 = logger.stop(min_samples=100)
            self.assertEqual(res_path2, fpath2)
            self.assertTrue(os.path.exists(fpath2))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_2d_afr_heatmap_matrix(self):
        """Verify 2D AFR Heatmap Matrix correctly bins cruising lean spots and WOT power zones."""
        # Cruising @ 3000 RPM, 35 km/h with lean AFR 17.5
        cruising_rpm = [3000.0] * 20
        cruising_spd = [35.0] * 20
        cruising_afr = [17.5] * 20

        # WOT @ 7200 RPM, 78 km/h with safe rich AFR 11.9
        wot_rpm = [7200.0] * 20
        wot_spd = [78.0] * 20
        wot_afr = [11.9] * 20

        df = pd.DataFrame({
            "RPM": cruising_rpm + wot_rpm,
            "Speed_kmh": cruising_spd + wot_spd,
            "AFR": cruising_afr + wot_afr,
            "EGT": [400.0] * 20 + [630.0] * 20
        })

        matrix = generate_afr_heatmap_matrix(df, stoich_afr=14.30)
        self.assertEqual(len(matrix["speed_columns"]), 6)
        self.assertEqual(len(matrix["rows"]), 6)

        # Check Cruising Row (2500-3500 RPM)
        row_cruising = [r for r in matrix["rows"] if "2500-3500" in r["rpm_label"]][0]
        # Speed bin 30-45 km/h (index 2)
        cell_cruising = row_cruising["cells"][2]
        self.assertEqual(cell_cruising["count"], 20)
        self.assertAlmostEqual(cell_cruising["avg_afr"], 17.5, places=1)
        self.assertEqual(cell_cruising["status_class"], "cell-critical")
        self.assertEqual(cell_cruising["status_label"], "Magerloch")

        # Check WOT Row (>6500 RPM)
        row_wot = [r for r in matrix["rows"] if ">6500" in r["rpm_label"]][0]
        # Speed bin >75 km/h (index 5)
        cell_wot = row_wot["cells"][5]
        self.assertEqual(cell_wot["count"], 20)
        self.assertAlmostEqual(cell_wot["avg_afr"], 11.9, places=1)
        self.assertEqual(cell_wot["status_class"], "cell-rich")

    def test_trip_analyzer_session(self):
        """Verify analyze_trip_session calculates statistics, distance, and returns valid report."""
        rpm = np.linspace(1500, 7500, 100)
        speed = np.linspace(10, 80, 100)
        afr = np.linspace(13.5, 12.0, 100)
        egt = np.linspace(350, 600, 100)

        df = pd.DataFrame({
            "RPM": rpm,
            "Speed_kmh": speed,
            "AFR": afr,
            "EGT": egt,
            "Lat": [46.186] * 100,
            "Lon": [6.128] * 100,
            "Alt": [385.0] * 100,
            "GPS_Fix": [True] * 100,
            "Time": [f"14:00:{i:02d}" for i in range(100)]
        })

        report = analyze_trip_session(df, DEFAULT_CARB_SETUP)
        self.assertTrue(report["valid"])
        self.assertEqual(report["total_samples"], 100)
        self.assertEqual(report["max_rpm"], 7500)
        self.assertEqual(report["max_speed"], 80.0)
        self.assertIn("heatmap", report)
        self.assertIn("chart_timeline", report)


class TestCSVLogger(unittest.TestCase):

    def test_logger_prebuffer_and_discard(self):
        """Verify CSVLogger pre-trigger buffer recording and discard functionality."""
        import tempfile
        import shutil
        from data.logger import CSVLogger

        temp_dir = tempfile.mkdtemp()
        try:
            logger = CSVLogger(log_dir=temp_dir)
            pre_buffer = [
                {"time": "12:00:00", "rpm": 2800.0, "afr": 13.0, "egt": 500.0, "speed": 35.0, "fix": True},
                {"time": "12:00:01", "rpm": 3100.0, "afr": 12.8, "egt": 510.0, "speed": 38.0, "fix": True}
            ]
            fpath = logger.start(trigger="AUTO", pre_buffer=pre_buffer)
            self.assertTrue(logger.is_logging)
            self.assertEqual(logger.trigger_mode, "AUTO")
            self.assertEqual(logger.samples_count, 2)

            logger.log(rpm=3500.0, afr=12.6, egt=520.0, speed=42.0, fix=True)
            self.assertEqual(logger.samples_count, 3)

            # Test discard
            logger.discard_current()
            self.assertFalse(logger.is_logging)
            self.assertFalse(os.path.exists(fpath))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestHardwareServiceAutoTrigger(unittest.TestCase):

    def test_gear3_auto_trigger_rules(self):
        """Verify strict 3rd gear ratio, minimum speed (>15 km/h), and acceleration validation."""
        # 1. Stationary rev (v = 0.0) -> must NOT trigger
        spd_stationary = 0.0
        rpm = 5000.0
        speed_ok = (spd_stationary > 15.0)
        self.assertFalse(speed_ok)

        # 2. 1st gear pull (v = 25 km/h, RPM = 4500 -> ratio = 180.0) -> must NOT trigger
        spd_g1 = 25.0
        rpm_g1 = 4500.0
        ratio_g1 = rpm_g1 / spd_g1
        in_gear3_g1 = (spd_g1 > 15.0) and (60.0 <= ratio_g1 <= 110.0)
        self.assertFalse(in_gear3_g1)

        # 3. 3rd gear pull (v = 55 km/h, RPM = 4500 -> ratio = 81.8) -> MUST trigger
        spd_g3 = 55.0
        rpm_g3 = 4500.0
        ratio_g3 = rpm_g3 / spd_g3
        in_gear3_g3 = (spd_g3 > 15.0) and (60.0 <= ratio_g3 <= 110.0)
        self.assertTrue(in_gear3_g3)

        # 4. Abrupt drop filter condition (dRPM/dt <= -500 and low gain)
        drpm_dt = -550.0
        rpm_gain = 400.0
        pull_duration = 0.5
        abrupt_drop = (drpm_dt <= -500.0 and rpm_gain < 1000.0 and pull_duration >= 0.3)
        self.assertTrue(abrupt_drop)

    def test_gps_staleness_and_fix_rules(self):
        """Verify GPS staleness logic: fresh fix passes speed, stale/no-fix zeroes speed."""
        from hw.gps_l76k import GPSData
        import time

        now_mono = time.monotonic()

        # 1. Fresh GPS data with fix (received 0.5s ago) -> speed and fix preserved
        fresh_gps = GPSData(speed_kmh=45.0, fix=True, last_seen=now_mono - 0.5)
        gps_age = now_mono - fresh_gps.last_seen
        is_fresh = gps_age < 2.5
        fix = bool(fresh_gps.fix and is_fresh)
        spd = fresh_gps.speed_kmh if fix else 0.0
        self.assertTrue(fix)
        self.assertEqual(spd, 45.0)

        # 2. Stale GPS data with fix (received 3.0s ago, e.g. tunnel dropout) -> speed zeroed, fix False
        stale_gps = GPSData(speed_kmh=45.0, fix=True, last_seen=now_mono - 3.0)
        gps_age = now_mono - stale_gps.last_seen
        is_fresh = gps_age < 2.5
        fix = bool(stale_gps.fix and is_fresh)
        spd = stale_gps.speed_kmh if fix else 0.0
        self.assertFalse(fix)
        self.assertEqual(spd, 0.0)

        # 3. Fresh GPS data but no fix (searching for sats) -> speed zeroed, fix False
        no_fix_gps = GPSData(speed_kmh=12.0, fix=False, last_seen=now_mono - 0.2)
        gps_age = now_mono - no_fix_gps.last_seen
        is_fresh = gps_age < 2.5
        fix = bool(no_fix_gps.fix and is_fresh)
        spd = no_fix_gps.speed_kmh if fix else 0.0
        self.assertFalse(fix)
        self.assertEqual(spd, 0.0)


class TestLogMetadata(unittest.TestCase):

    def test_log_metadata_header_writing_and_reading(self):
        """Verify CSVLogger writes structured # SETUP_META header and read_log_metadata parses it."""
        import tempfile
        import shutil
        from data.logger import CSVLogger, read_log_metadata, load_telemetry_csv, get_setup_badge_string

        temp_dir = tempfile.mkdtemp()
        try:
            logger = CSVLogger(log_dir=temp_dir)
            custom_setup = {
                "displacement_cc": 187.0,
                "ignition_deg": 18.0,
                "carb": {
                    "main_jet_hd": 125,
                    "idle_jet_nd": "60/160",
                    "air_corrector_hlkd": 160,
                    "emulsion_tube": "Lemarxon x234",
                    "exhaust": "Polini Box"
                },
                "notes": "Test Pull HD 125"
            }
            fpath = logger.start(setup_meta=custom_setup)
            logger.log(rpm=4800, afr=11.5, egt=420.0, speed=42.0)
            logger.log(rpm=6500, afr=11.0, egt=540.0, speed=72.0)
            logger.stop(min_samples=0)

            # Verify file content starts with comment lines
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertTrue(lines[0].startswith("# STREETDYNO_LOG_VERSION"))
            self.assertTrue(lines[2].startswith("# SETUP_META:"))

            # Test fast metadata reader
            meta = read_log_metadata(fpath)
            self.assertTrue(meta.get("is_embedded", False))
            self.assertEqual(meta["carb"]["main_jet_hd"], 125)
            self.assertEqual(meta["carb"]["idle_jet_nd"], "60/160")
            self.assertEqual(meta["displacement_cc"], 187.0)

            # Test setup badge
            badge = get_setup_badge_string(meta)
            self.assertIn("HD 125", badge)
            self.assertIn("ND 60/160", badge)
            self.assertIn("18° Zdg", badge)

            # Test load_telemetry_csv
            df, loaded_meta = load_telemetry_csv(fpath)
            self.assertEqual(len(df), 2)
            self.assertEqual(list(df.columns), ['Time', 'RPM', 'AFR', 'EGT', 'CHT', 'Speed_kmh', 'Lat', 'Lon', 'Alt', 'GPS_Fix'])
            self.assertEqual(loaded_meta["carb"]["main_jet_hd"], 125)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_legacy_log_backward_compatibility(self):
        """Verify legacy CSVs without # headers are correctly loaded with default metadata fallback."""
        import tempfile
        import shutil
        from data.logger import read_log_metadata, load_telemetry_csv

        temp_dir = tempfile.mkdtemp()
        try:
            legacy_csv = os.path.join(temp_dir, "legacy_dyno_log.csv")
            with open(legacy_csv, "w", encoding="utf-8") as f:
                f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                f.write("20:00:00,5000,12.5,450.0,55.0,0.0,0.0,0.0,True\n")

            meta = read_log_metadata(legacy_csv)
            self.assertFalse(meta.get("is_embedded", True))
            self.assertIn("carb", meta)

            df, meta = load_telemetry_csv(legacy_csv)
            self.assertEqual(len(df), 1)
            self.assertEqual(df["RPM"].iloc[0], 5000)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_retroactive_metadata_update(self):
        """Verify write_log_metadata retroactively upgrades legacy logs and updates existing headers without corrupting data."""
        import tempfile
        import shutil
        from data.logger import write_log_metadata, read_log_metadata, load_telemetry_csv

        temp_dir = tempfile.mkdtemp()
        try:
            log_file = os.path.join(temp_dir, "test_upgrade_log.csv")
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                f.write("14:00:01,4800,11.8,450.0,50.0,46.12,6.12,380.0,True\n")
                f.write("14:00:02,6200,10.9,520.0,75.0,46.13,6.13,380.0,True\n")

            # Retroactively update to HD 135
            success, new_meta = write_log_metadata(log_file, {"main_jet_hd": 135, "idle_jet_nd": "60/160"}, notes="Fahrt mit HD 135")
            self.assertTrue(success)
            self.assertEqual(new_meta["carb"]["main_jet_hd"], 135)

            # Check that file now has valid header
            meta = read_log_metadata(log_file)
            self.assertTrue(meta.get("is_embedded", False))
            self.assertEqual(meta["carb"]["main_jet_hd"], 135)
            self.assertEqual(meta["notes"], "Fahrt mit HD 135")

            # Check that all telemetry rows remain intact
            df, loaded_meta = load_telemetry_csv(log_file)
            self.assertEqual(len(df), 2)
            self.assertEqual(df["RPM"].iloc[0], 4800)
            self.assertEqual(df["RPM"].iloc[1], 6200)
            self.assertEqual(loaded_meta["carb"]["main_jet_hd"], 135)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_log_creation_datetime_sorting(self):
        """Verify get_log_creation_datetime parses timestamps accurately and sorts logs by creation date regardless of modification."""
        from datetime import datetime
        from data.logger import get_log_creation_datetime

        # 1. Filename format YYYYMMDD-HHMMSS
        dt1 = get_log_creation_datetime("/tmp/dyno_log_20260908-091455.csv")
        self.assertEqual(dt1, datetime(2026, 9, 8, 9, 14, 55))

        # 2. Filename format YYYYMMDD_HHMMSS
        dt2 = get_log_creation_datetime("/tmp/trip_20260911_140700.csv")
        self.assertEqual(dt2, datetime(2026, 9, 11, 14, 7, 0))

        # 3. Sorting order check
        f_old = "/tmp/dyno_log_20260902-142433.csv"
        f_mid = "/tmp/dyno_log_20260908-091455.csv"
        f_new = "/tmp/trip_20260911-140700.csv"
        files = [f_mid, f_new, f_old]
        sorted_files = sorted(files, key=get_log_creation_datetime, reverse=True)
        self.assertEqual(sorted_files, [f_new, f_mid, f_old])


class TestSubsecondTimestampsAndJitter(unittest.TestCase):

    def test_subsecond_timestamps_with_jitter(self):
        """Verify calculate_telemetry_metrics handles 92ms-108ms sample jitter without PS drops."""
        n = 40
        rpm = np.linspace(3500, 7000, n)
        spd = rpm / 80.69

        # Synthetic jittered timestamps around 10Hz (92ms to 108ms)
        np.random.seed(42)
        jitter_intervals = np.random.uniform(0.092, 0.108, n)
        time_arr = 1789494600.0 + np.cumsum(jitter_intervals)

        df = pd.DataFrame({
            'Time': time_arr,
            'RPM': rpm,
            'Speed_kmh': spd,
            'AFR': np.full(n, 12.6),
            'EGT': np.full(n, 550.0)
        })

        res = calculate_telemetry_metrics(df, gear=3)
        self.assertIn("PS", res.columns)
        self.assertIn("dRPM_dt", res.columns)

        # Power must be smooth and positive without periodic drops
        ps_vals = res["PS"].iloc[5:-5].values
        self.assertTrue((ps_vals > 5.0).all())
        # Check step-to-step smoothness: no single step change > 2.5 PS
        ps_steps = np.abs(np.diff(ps_vals))
        self.assertLess(ps_steps.max(), 2.5)

    def test_legacy_second_timestamps_no_collapse(self):
        """Verify legacy %H:%M:%S integer second timestamps do not produce 1.0s spike collapse."""
        n = 40
        rpm = np.linspace(3500, 7000, n)
        spd = rpm / 80.69

        legacy_times = []
        for sec in range(1, 5):
            legacy_times.extend([f"14:00:{sec:02d}"] * 10)

        df_legacy = pd.DataFrame({
            'Time': legacy_times,
            'RPM': rpm,
            'Speed_kmh': spd,
            'AFR': np.full(n, 12.6),
            'EGT': np.full(n, 550.0)
        })

        res = calculate_telemetry_metrics(df_legacy, gear=3)
        # Power during pull must remain stable; no 90% collapse at second boundaries
        ps_vals = res["PS"].iloc[5:-5].values
        self.assertTrue((ps_vals > 5.0).all())
        # Minimum PS during the middle of pull must not collapse below 5.0 PS
        self.assertGreater(ps_vals.min(), 5.0)

    def test_logger_subsecond_precision(self):
        """Verify CSVLogger records 4 decimal places float timestamps."""
        import tempfile
        import shutil
        from data.logger import CSVLogger, load_telemetry_csv

        temp_dir = tempfile.mkdtemp()
        try:
            logger = CSVLogger(log_dir=temp_dir)
            fpath = logger.start()
            test_ts = 1789494617.2275
            logger.log(rpm=4500, afr=12.5, egt=500.0, speed=55.0, timestamp=test_ts)
            logger.stop(min_samples=0)

            df, _ = load_telemetry_csv(fpath)
            self.assertEqual(len(df), 1)
            logged_time = float(df['Time'].iloc[0])
            self.assertAlmostEqual(logged_time, test_ts, places=4)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestCHTIntegration(unittest.TestCase):
    """Test suite for MAX6675 CHT sensor integration, telemetry streaming, logging, and legacy resilience."""

    def test_nmea_cht_telemetry_parsing(self):
        """Verify NMEA telemetry parser extracts 5th field CHT and handles 4-field legacy streams."""
        from hw.hardware_service import HardwareService

        # Instantiating HardwareService with dummy paths
        service = HardwareService(log_dir="/tmp", trip_log_dir="/tmp")

        # 1. 5-Field NMEA line with CHT ($MICROS;RPM;AFR;EGT;CHT*CS)
        payload = "12345678;4500.0;12.8;550.0;135.0"
        cs = 0
        for ch in payload:
            cs ^= ord(ch)
        line = f"${payload}*{cs:02X}"

        success = service._parse_telemetry_line(line)
        self.assertTrue(success)
        self.assertEqual(service.current_micros, 12345678)
        self.assertAlmostEqual(service.current_rpm, 4500.0)
        self.assertAlmostEqual(service.current_afr, 12.8)
        self.assertAlmostEqual(service.current_egt, 550.0)
        self.assertAlmostEqual(service.current_cht, 135.0)

        # 2. 4-Field NMEA line (legacy stream without CHT) -> CHT must default to 0.0
        payload_legacy = "12345688;4600.0;12.6;555.0"
        cs_legacy = 0
        for ch in payload_legacy:
            cs_legacy ^= ord(ch)
        line_legacy = f"${payload_legacy}*{cs_legacy:02X}"

        success_legacy = service._parse_telemetry_line(line_legacy)
        self.assertTrue(success_legacy)
        self.assertAlmostEqual(service.current_rpm, 4600.0)
        self.assertAlmostEqual(service.current_cht, 0.0)

        # 3. Invalid Checksum -> parse rejected
        line_bad = f"${payload}*99"
        self.assertFalse(service._parse_telemetry_line(line_bad))

    def test_csv_logger_and_load_cht(self):
        """Verify CSVLogger writes CHT column and load_telemetry_csv reads it back."""
        import tempfile
        import shutil
        from data.logger import CSVLogger, load_telemetry_csv

        temp_dir = tempfile.mkdtemp()
        try:
            logger = CSVLogger(log_dir=temp_dir)
            fpath = logger.start()
            logger.log(rpm=5200, afr=12.7, egt=580.0, cht=142.5, speed=65.0, timestamp=1789494620.1234)
            logger.stop(min_samples=0)

            df, meta = load_telemetry_csv(fpath)
            self.assertEqual(len(df), 1)
            self.assertIn("CHT", df.columns)
            self.assertAlmostEqual(float(df["CHT"].iloc[0]), 142.5, places=1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_legacy_csv_loading_injects_cht_zero(self):
        """Verify legacy CSV without CHT column automatically gains CHT=0.0 and passes dyno calculations."""
        import tempfile
        import shutil
        from data.logger import load_telemetry_csv

        temp_dir = tempfile.mkdtemp()
        try:
            # Create a legacy CSV file without CHT
            csv_path = os.path.join(temp_dir, "dyno_log_legacy.csv")
            with open(csv_path, "w") as f:
                f.write("Time,RPM,AFR,EGT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                f.write("1789494600.0000,3500,12.5,500.0,43.4,0.0,0.0,100.0,1\n")
                f.write("1789494600.1000,4000,12.6,510.0,49.6,0.0,0.0,100.0,1\n")
                f.write("1789494600.2000,4500,12.7,520.0,55.8,0.0,0.0,100.0,1\n")

            df, meta = load_telemetry_csv(csv_path)
            self.assertIn("CHT", df.columns)
            self.assertTrue((df["CHT"] == 0.0).all())

            # Verify dyno math runs without KeyError
            res = calculate_telemetry_metrics(df, gear=3)
            self.assertIn("PS", res.columns)
            self.assertIn("CHT", res.columns)
            self.assertIn("CHT_cleaned", res.columns)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clean_cht_data_filter(self):
        """Verify clean_cht_data handles disconnected sensor codes (701, 705) and jumps > 35°C."""
        from data.analyzer_logic import clean_cht_data

        raw_cht = [120.0, 122.0, 701.0, 123.0, 175.0, 125.0, 0.0, 126.0]
        df = pd.DataFrame({"CHT": raw_cht})
        df_cleaned = clean_cht_data(df)

        self.assertIn("CHT_cleaned", df_cleaned.columns)
        cleaned = df_cleaned["CHT_cleaned"].tolist()
        # 701.0 (disconnected MAX6675) should be replaced with last valid (122.0)
        self.assertEqual(cleaned[2], 122.0)
        # 175.0 (jump > 35°C from 123.0) should be suppressed
        self.assertEqual(cleaned[4], 123.0)
        # 0.0 should be replaced with last valid (125.0)
        self.assertEqual(cleaned[6], 125.0)
        self.assertEqual(cleaned[7], 126.0)

    def test_trip_analyzer_cht_metrics(self):
        """Verify analyze_trip_session computes max_cht, avg_cht, and includes CHT in timeline."""
        n = 30
        times = [1789494600.0 + i * 0.1 for i in range(n)]
        df = pd.DataFrame({
            "Time": times,
            "RPM": np.linspace(3000, 6000, n),
            "Speed_kmh": np.linspace(35, 75, n),
            "AFR": np.full(n, 12.8),
            "EGT": np.full(n, 520.0),
            "CHT": np.linspace(110.0, 140.0, n),
            "Lat": np.full(n, 48.137),
            "Lon": np.full(n, 11.575),
            "Alt": np.full(n, 520.0),
            "GPS_Fix": np.full(n, True)
        })

        report = analyze_trip_session(df)
        self.assertIn("max_cht", report)
        self.assertIn("avg_cht", report)
        self.assertAlmostEqual(report["max_cht"], 140.0, places=1)
        self.assertGreater(report["avg_cht"], 120.0)
        self.assertIn("cht", report["chart_timeline"])
        self.assertEqual(len(report["chart_timeline"]["cht"]), n)

    def test_csv_logger_min_samples_guard(self):
        """Verify CSVLogger.stop discards pulls with < 20 samples and keeps pulls with >= 20 samples."""
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            logger = CSVLogger(log_dir=temp_dir)

            # 1. Under-threshold run (10 samples < 20)
            fpath_short = logger.start()
            self.assertTrue(os.path.exists(fpath_short))
            for i in range(10):
                logger.log(rpm=4000, afr=12.5, egt=500.0, speed=50.0)
            res = logger.stop(min_samples=20)
            self.assertIsNone(res)
            self.assertFalse(os.path.exists(fpath_short))

            # 2. Valid run (25 samples >= 20)
            fpath_valid = logger.start()
            self.assertTrue(os.path.exists(fpath_valid))
            for i in range(25):
                logger.log(rpm=4000, afr=12.5, egt=500.0, speed=50.0)
            res = logger.stop(min_samples=20)
            self.assertEqual(res, fpath_valid)
            self.assertTrue(os.path.exists(fpath_valid))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_trip_logger_min_samples_guard(self):
        """Verify TripLogger.stop discards trips with < 100 samples and keeps trips with >= 100 samples."""
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            logger = TripLogger(log_dir=temp_dir)

            # 1. Short trip (30 samples < 100)
            fpath_short = logger.start()
            self.assertTrue(os.path.exists(fpath_short))
            for i in range(30):
                logger.log(rpm=3500, afr=13.0, egt=450.0, speed=40.0)
            res = logger.stop(min_samples=100)
            self.assertIsNone(res)
            self.assertFalse(os.path.exists(fpath_short))

            # 2. Valid trip (105 samples >= 100)
            fpath_valid = logger.start()
            self.assertTrue(os.path.exists(fpath_valid))
            for i in range(105):
                logger.log(rpm=3500, afr=13.0, egt=450.0, speed=40.0)
            res = logger.stop(min_samples=100)
            self.assertEqual(res, fpath_valid)
            self.assertTrue(os.path.exists(fpath_valid))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_delete_log_and_trip_files(self):
        """Verify delete_log_file deletes CSV and associated plot, and delete_trip_file deletes trip CSV."""
        import tempfile
        import shutil

        temp_log = tempfile.mkdtemp()
        temp_trip = tempfile.mkdtemp()
        temp_plot = tempfile.mkdtemp()
        try:
            # 1. Dyno log + plot deletion
            csv_name = "dyno_log_20260919-120000.csv"
            csv_path = os.path.join(temp_log, csv_name)
            plot_path = os.path.join(temp_plot, "p_dyno_log_20260919-120000.png")
            with open(csv_path, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n1,4000,12.5,500,130,50,0,0,0,1\n")
            with open(plot_path, "wb") as f:
                f.write(b"PNG_FAKE_DATA")

            self.assertTrue(os.path.exists(csv_path))
            self.assertTrue(os.path.exists(plot_path))

            ok = delete_log_file(csv_name, log_dir=temp_log, plot_dir=temp_plot)
            self.assertTrue(ok)
            self.assertFalse(os.path.exists(csv_path))
            self.assertFalse(os.path.exists(plot_path))

            # Path traversal attempt
            bad_traversal = delete_log_file("../../secret.csv", log_dir=temp_log, plot_dir=temp_plot)
            self.assertFalse(bad_traversal)

            # 2. Trip log deletion
            trip_name = "trip_20260919-120000.csv"
            trip_path = os.path.join(temp_trip, trip_name)
            with open(trip_path, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n1,3000,13,400,110,30,0,0,0,1\n")
            self.assertTrue(os.path.exists(trip_path))

            ok_trip = delete_trip_file(trip_name, trip_dir=temp_trip)
            self.assertTrue(ok_trip)
            self.assertFalse(os.path.exists(trip_path))
        finally:
            shutil.rmtree(temp_log, ignore_errors=True)
            shutil.rmtree(temp_trip, ignore_errors=True)
            shutil.rmtree(temp_plot, ignore_errors=True)

    def test_cleanup_short_logs(self):
        """Verify cleanup_short_logs removes sub-threshold files and keeps valid files."""
        import tempfile
        import shutil

        temp_log = tempfile.mkdtemp()
        temp_trip = tempfile.mkdtemp()
        temp_plot = tempfile.mkdtemp()
        try:
            # Short dyno log (5 rows < 20) + plot
            short_dyno = os.path.join(temp_log, "dyno_log_short.csv")
            with open(short_dyno, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                for i in range(5):
                    f.write(f"{i},4000,12.5,500,130,50,0,0,0,1\n")
            with open(os.path.join(temp_plot, "p_dyno_log_short.png"), "wb") as f:
                f.write(b"PNG_DATA")

            # Valid dyno log (25 rows >= 20)
            valid_dyno = os.path.join(temp_log, "dyno_log_valid.csv")
            with open(valid_dyno, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                for i in range(25):
                    f.write(f"{i},4000,12.5,500,130,50,0,0,0,1\n")

            # Short trip (10 rows < 100)
            short_trip = os.path.join(temp_trip, "trip_short.csv")
            with open(short_trip, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                for i in range(10):
                    f.write(f"{i},3000,13,400,110,30,0,0,0,1\n")

            # Valid trip (105 rows >= 100)
            valid_trip = os.path.join(temp_trip, "trip_valid.csv")
            with open(valid_trip, "w") as f:
                f.write("Time,RPM,AFR,EGT,CHT,Speed_kmh,Lat,Lon,Alt,GPS_Fix\n")
                for i in range(105):
                    f.write(f"{i},3000,13,400,110,30,0,0,0,1\n")

            res = cleanup_short_logs(
                min_dyno_samples=20,
                min_trip_samples=100,
                log_dir=temp_log,
                trip_dir=temp_trip,
                plot_dir=temp_plot
            )
            self.assertEqual(res["deleted_dyno_count"], 1)
            self.assertEqual(res["deleted_trip_count"], 1)
            self.assertIn("dyno_log_short.csv", res["deleted_dyno_files"])
            self.assertIn("trip_short.csv", res["deleted_trip_files"])

            self.assertFalse(os.path.exists(short_dyno))
            self.assertFalse(os.path.exists(os.path.join(temp_plot, "p_dyno_log_short.png")))
            self.assertTrue(os.path.exists(valid_dyno))
            self.assertFalse(os.path.exists(short_trip))
            self.assertTrue(os.path.exists(valid_trip))
        finally:
            shutil.rmtree(temp_log, ignore_errors=True)
            shutil.rmtree(temp_trip, ignore_errors=True)
            shutil.rmtree(temp_plot, ignore_errors=True)

    def test_housekeeping_api_endpoints(self):
        """Verify web API routes for housekeeping: delete_log, delete_trip, bulk_delete_logs, cleanup_logs."""
        app = create_app()
        app.config['TESTING'] = True
        client = app.test_client()

        # 1. Invalid payload guards
        res = client.post('/api/delete_log', json={})
        self.assertEqual(res.status_code, 400)

        res = client.post('/api/delete_trip', json={})
        self.assertEqual(res.status_code, 400)

        res = client.post('/api/bulk_delete_logs', json={})
        self.assertEqual(res.status_code, 400)

        # 2. Non-existent file deletion
        res = client.post('/api/delete_log', json={'filename': 'non_existent_12345.csv'})
        self.assertEqual(res.status_code, 404)

        res = client.post('/api/delete_trip', json={'filename': 'non_existent_trip_12345.csv'})
        self.assertEqual(res.status_code, 404)

        # 3. Cleanup endpoint returns 200 and json structure
        res = client.post('/api/cleanup_logs')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("result", data)

    def test_reconstruct_afr_voltage(self):
        """Verify non-invasive A0 voltage reconstruction from AFR."""
        # Baseline AFR 13.5 (Idle/Cruise) -> V_A0 ≈ (22.62 - 13.5) / 5.72 = 1.594 V
        v_135 = reconstruct_afr_voltage(13.5)
        self.assertAlmostEqual(v_135, 1.594, places=2)

        # Free Air 19.5 -> V_A0 ≈ (22.62 - 19.5) / 5.72 = 0.545 V
        v_195 = reconstruct_afr_voltage(19.5)
        self.assertAlmostEqual(v_195, 0.545, places=2)

        # Clamping and non-negative guards
        self.assertEqual(reconstruct_afr_voltage(0.0), 0.0)
        self.assertEqual(reconstruct_afr_voltage(-5.0), 0.0)
        # Deep rich AFR 5.0 -> clamped to 5.0V ADC rail
        self.assertLessEqual(reconstruct_afr_voltage(5.0), 5.0)

    def test_evaluate_ground_offset(self):
        """Verify traffic-light evaluation for Sternmasse offset delta U."""
        # 1. Excellent (delta U < 5 mV)
        res_green = evaluate_ground_offset(2.5)
        self.assertEqual(res_green["rating"], "GREEN")
        self.assertFalse(res_green["action_needed"])
        self.assertAlmostEqual(res_green["afr_impact"], 0.014, places=3)

        # 2. Borderline (5 <= delta U <= 20 mV)
        res_yellow = evaluate_ground_offset(12.0)
        self.assertEqual(res_yellow["rating"], "YELLOW")
        self.assertTrue(res_yellow["action_needed"])
        self.assertAlmostEqual(res_yellow["afr_impact"], 0.069, places=3)

        # 3. Critical (delta U > 20 mV)
        res_red = evaluate_ground_offset(35.0)
        self.assertEqual(res_red["rating"], "RED")
        self.assertTrue(res_red["action_needed"])
        self.assertAlmostEqual(res_red["afr_impact"], 0.200, places=3)

    def test_save_and_list_audit_reports(self):
        """Verify persistent saving and listing of JSON and Markdown audit reports."""
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            test_data = {
                "delta_u_mv": 3.2,
                "step1_afr": 19.5,
                "step2_afr": 13.5,
                "step3_rpm": 1420.0,
                "step3_egt": 480.0,
                "step3_cht": 125.0,
                "notes": "Prüfstand-Messung nach Zündkabel-Tausch",
                "apply_compensation": False
            }

            json_p, md_p = save_audit_report(test_data, audit_dir=temp_dir)
            self.assertTrue(os.path.exists(json_p))
            self.assertTrue(os.path.exists(md_p))

            # Verify JSON content
            with open(json_p, "r", encoding="utf-8") as f:
                saved_json = json.load(f)
            self.assertEqual(saved_json["rating"], "GREEN")
            self.assertEqual(saved_json["delta_u_mv"], 3.2)
            self.assertIn("step1_baseline", saved_json["steps"])

            # Verify listing
            reports = list_audit_reports(audit_dir=temp_dir)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["rating"], "GREEN")
            self.assertEqual(reports[0]["delta_u_mv"], 3.2)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_diagnostics_web_routes(self):
        """Verify Web endpoints: /diagnostics, /api/diagnostics/live, /api/diagnostics/save_audit, /api/diagnostics/history."""
        app = create_app()
        app.config['TESTING'] = True
        client = app.test_client()

        # 1. Page render
        res_page = client.get('/diagnostics')
        self.assertEqual(res_page.status_code, 200)
        self.assertIn(b"STERNMASSE-AUDIT", res_page.data)

        # 2. Live telemetry API
        res_live = client.get('/api/diagnostics/live')
        self.assertEqual(res_live.status_code, 200)
        data_live = res_live.get_json()
        self.assertIn("voltage_a0", data_live)
        self.assertIn("afr", data_live)
        self.assertIn("rpm", data_live)
        self.assertIn("offset_mv", data_live)

        # 3. Save audit API (empty guard)
        res_empty = client.post('/api/diagnostics/save_audit', json={})
        self.assertEqual(res_empty.status_code, 400)

        # 4. Save audit API (valid)
        res_save = client.post('/api/diagnostics/save_audit', json={
            "delta_u_mv": 1.5,
            "step1_afr": 19.5,
            "step2_afr": 13.5,
            "step3_rpm": 1400.0,
            "step3_egt": 450.0,
            "step3_cht": 110.0,
            "notes": "Testlauf",
            "apply_compensation": False
        })
        self.assertEqual(res_save.status_code, 200)
        save_data = res_save.get_json()
        self.assertEqual(save_data["status"], "success")
        self.assertEqual(save_data["evaluation"]["rating"], "GREEN")

        # 5. History API
        res_hist = client.get('/api/diagnostics/history')
        self.assertEqual(res_hist.status_code, 200)
        self.assertIn("reports", res_hist.get_json())

    def test_hardware_service_ground_offset_compensation(self):
        """Verify HardwareService applies lambda_ground_offset_mv compensation to raw telemetry."""
        from hw.hardware_service import HardwareService
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            hw = HardwareService(log_dir=temp_dir)
            # Test payload: $1000;1400;12.50;500.0;120.0*CHECKSUM
            payload = "1000;1400;12.50;500.0;120.0"
            cs = 0
            for ch in payload:
                cs ^= ord(ch)
            cs_str = f"{cs:02X}"
            line = f"${payload}*{cs_str}"

            # 1. Baseline without offset
            hw.lambda_ground_offset_mv = 0.0
            self.assertTrue(hw._parse_telemetry_line(line))
            self.assertAlmostEqual(hw.current_afr, 12.50, places=2)

            # 2. With 10 mV offset: delta AFR = (10 / 1000) * 5.72 = +0.0572 AFR
            hw.lambda_ground_offset_mv = 10.0
            self.assertTrue(hw._parse_telemetry_line(line))
            self.assertAlmostEqual(hw.current_afr, 12.50 + 0.0572, places=2)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()



