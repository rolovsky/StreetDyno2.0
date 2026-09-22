#!/usr/bin/env bash
# ==============================================================================
# StreetDyno 2.5 – Google Drive Catch-Up Sync
# ==============================================================================
# Copies all local trip and dyno logs to Google Drive:
#   - trips/ -> gdrive:StreetDyno/logs/trips/
#   - dyno/  -> gdrive:StreetDyno/logs/dyno/
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS_DIR="$DIR/logs"
TRIPS_DIR="$LOGS_DIR/trips"

if ! command -v rclone &>/dev/null; then
    echo "❌ rclone ist nicht installiert. Führe 'sudo apt install -y rclone' aus."
    exit 1
fi

if [ ! -f "$HOME/.config/rclone/rclone.conf" ]; then
    echo "❌ rclone ist noch nicht konfiguriert (~/.config/rclone/rclone.conf fehlt)."
    echo "Bitte führe 'rclone config' aus, um das 'gdrive' Remote einzurichten."
    exit 1
fi

echo "🚀 Synchronisiere StreetDyno Logs mit Google Drive (gdrive:StreetDyno/logs/)..."

# 1. Sync trips
if [ -d "$TRIPS_DIR" ]; then
    echo "📁 Synchronisiere Fahrten (trips)..."
    rclone copy "$TRIPS_DIR" "gdrive:StreetDyno/logs/trips/" \
        --include "trip_*.csv" \
        --update \
        --progress
fi

# 2. Sync dyno logs
if [ -d "$LOGS_DIR" ]; then
    echo "📁 Synchronisiere Dyno-Pulls (dyno)..."
    rclone copy "$LOGS_DIR" "gdrive:StreetDyno/logs/dyno/" \
        --include "dyno_log_*.csv" \
        --update \
        --progress
fi

echo "✅ Synchronisation abgeschlossen!"
