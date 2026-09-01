#!/usr/bin/env python3
"""
StreetDyno 2.0 - Core Application Entry Point
Initializes Flask web framework, registers Web & API Blueprint,
and manages background HardwareService lifecycle.
"""

from __future__ import annotations
import os
import sys

# Ensure src/ directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from config import LOG_DIR, PLOT_DIR
from hw.hardware_service import HardwareService
from web.routes import dyno_bp


def create_app() -> Flask:
    """Creates and configures the StreetDyno Flask Application."""
    template_folder = os.path.join(os.path.dirname(__file__), 'templates')
    static_folder = os.path.join(os.path.dirname(__file__), 'static')
    
    app = Flask(
        __name__,
        template_folder=template_folder,
        static_folder=static_folder
    )

    # Initialize and start background hardware service
    hw_service = HardwareService(log_dir=LOG_DIR)
    hw_service.start()
    app.config['HW_SERVICE'] = hw_service

    # Register Web & API Blueprint
    app.register_blueprint(dyno_bp)

    return app


app = create_app()

if __name__ == '__main__':
    # Ensure runtime directories exist
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(PLOT_DIR, exist_ok=True)

    print("=" * 60)
    print("🚀 StreetDyno 2.0 - V5.1 Golden Master Edition")
    print(f"📡 Webserver running at http://0.0.0.0:8080")
    print(f"📁 Log Directory: {LOG_DIR}")
    print("=" * 60)

    try:
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
    except (KeyboardInterrupt, SystemExit):
        hw = app.config.get('HW_SERVICE')
        if hw:
            hw.stop()
        print("\n🛑 StreetDyno 2.0 terminated.")
