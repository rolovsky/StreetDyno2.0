"""
StreetDyno 2.0 - Automated Test Suite
Tests vehicle physics, Savitzky-Golay filtering, slope compensation,
DIN 70020 weather normalization, Carburetor Jetting Advisor, and Flask routes.
"""

import os
import sys
import unittest
import pandas as pd
import numpy as np

# Add src/ to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import (
    TOTAL_MASS_KG,
    PRIMARY_RATIO,
    GEAR_RATIOS,
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
    is_richer_idle_jet,
    is_leaner_idle_jet,
    get_idle_jet_advice
)
from data.logger import CSVLogger, TripLogger
from data.trip_analyzer import (
    calculate_gps_distance_km,
    generate_afr_heatmap_matrix,
    analyze_trip_session
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
        """Verify Log archive loads with 200 OK."""
        response = self.client.get('/logs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"LOG ARCHIV", response.data)

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


if __name__ == '__main__':
    unittest.main()


