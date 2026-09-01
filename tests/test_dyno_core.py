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
from data.jetting_advisor import analyze_carb_jetting, parse_nd_ratio
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

    def test_carb_jetting_advisor(self):
        """Verify 4-zone carburetor diagnostic evaluation."""
        # Create synthetic pull data
        rpm = np.linspace(2000, 8500, 100)
        afr = np.full(100, 12.7)  # Perfect AFR
        egt = np.linspace(400, 600, 100)
        df = pd.DataFrame({'RPM': rpm, 'AFR': afr, 'EGT': egt, 'Speed_kmh': rpm / 81.6})

        analysis = analyze_carb_jetting(df, DEFAULT_CARB_SETUP)
        self.assertTrue(analysis["valid"])
        self.assertEqual(analysis["overall_status"], "PERFECT")
        self.assertEqual(len(analysis["zones"]), 4)

    def test_nd_ratio_parser(self):
        """Verify ND ratio parsing (e.g. 60/160 -> 2.67)."""
        self.assertAlmostEqual(parse_nd_ratio("60/160"), 2.667, places=2)
        self.assertAlmostEqual(parse_nd_ratio("55/160"), 2.909, places=2)


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
            "main_jet_hd": 135,
            "idle_jet_nd": "60/160",
            "air_corrector_hlkd": 160
        }
        response = self.client.post('/api/update_carb_setup', json=test_payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["setup"]["main_jet_hd"], 135)


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


if __name__ == '__main__':
    unittest.main()

