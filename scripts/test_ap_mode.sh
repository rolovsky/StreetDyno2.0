#!/usr/bin/env bash
# ==============================================================================
# StreetDyno 2.5 - Safe 5-Minute Hotspot Connectivity Test with Auto-Rollback
# ==============================================================================

DURATION=300

echo "============================================================"
echo "🚀 Starte StreetDyno AP-Testmodus (Dauer: $((DURATION/60)) Minuten)"
echo "   SSID:     StreetDyno"
echo "   Passwort: dyno1234"
echo "   Pi-IP:    10.42.0.1"
echo "   HUD-URL:  http://10.42.0.1:8080/hud"
echo "============================================================"

# Schedule auto-rollback in background
nohup bash -c "sleep $DURATION && nmcli connection up Pachacamac" > /tmp/ap_test_rollback.log 2>&1 &

echo "🛡️  Sicherheits-Timer aktiv: Pi schaltet in $((DURATION/60)) Min. automatisch ins Heimnetz (Pachacamac) zurück."
echo "Aktiviere Access Point jetzt..."
nmcli connection up Hotspot
