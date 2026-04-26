# StreetDyno2.0
Hier ist die große Bestandsaufnahme deines **StreetDyno 2.0**. Wir haben uns von einem flackernden Terminal zu einer robusten Telemetrie-Einheit für die Vespa hochgearbeitet.
## 1. Teil: Die Arduino Master-Unit (Der Daten-Sammler)
Der Arduino agiert als Echtzeit-Interface. Seine Aufgabe: Millisekunden-präzise Signale in saubere Datenstrings zu verwandeln.
### Aktueller Stand: V3.9.1 (EMA-Filter Edition)
 * **RPM-Logik:** Nutzt jetzt einen **EMA-Filter (Exponential Moving Average)** mit \alpha = 0.12. Das sorgt für ein flüssiges, „analoges“ Nadelverhalten statt nervöser Sprünge.
 * **AFR-Modus:** Aktuell als **High-Precision Multimeter** konfiguriert (sendet Rohspannung 0–5V), um den perfekten Abgleich mit deinem SIP-Tacho zu ermöglichen.
 * **EGT-Modus:** Stabiles SPI-Reading mit Fehlererkennung (-1.0 bei Sensorverlust).
### Die Pinbelegung (Arduino Nano/Uno)
| Komponente | Pin | Funktion |
|---|---|---|
| **RPM (SIP Box)** | **D2** | Interrupt-Eingang (RISING), 3 Impulse/Umdr. |
| **MAX6675 SO** | **D4** | Serial Data Out (EGT) |
| **MAX6675 CS** | **D5** | Chip Select (EGT) |
| **MAX6675 SCK** | **D6** | Serial Clock (EGT) |
| **Lambda (AFR)** | **A0** | Analog-Eingang (0–5V Signal) |
| **Lambda GND** | **GND** | Signal-Masse (Wichtig!) |
### Evolutionsstufen
 1. **Vergangenheit (V1.0 - V3.0):** Einfache Intervall-Messung, gleitender Durchschnitt (Moving Average), Probleme mit „inf“-Werten und unsauberem Sync.
 2. **Gegenwart (V3.9.1):** SIP-Blackbox-Synchronisation, EMA-Glättung, atomare Datenblöcke zur Vermeidung von Rechenfehlern.
 3. **Zukunft (V4.x):** Integration eines zweiten EGT-Sensors (für Twin-Zylinder oder CHT-Vergleich) und dynamische Baudraten-Anpassung.
## 2. Teil: Raspberry Pi Zero (Das Gehirn)
Der Pi übernimmt das schwere Heben: Daten-Routing, Webserver, GPS-Verarbeitung und Langzeit-Logging.
### Aktueller Stand: „Golden Master“ V3.8
 * **Serial-Sync:** Automatischer Port-Flush beim Start. Er erkennt den Arduino selbstständig nach einem Reboot oder Reconnect.
 * **Multitasking:** Trennung von Hardware-Loop (Seriell/GPS/OLED) und Web-Server (Flask) via Threading.
 * **Robustheit:** Fehlerresistentes Parsing (verwirft kaputte Datenpakete automatisch).
### Die Pinbelegung (Pi Zero GPIO)
| Komponente | Pin (BCM) | Funktion |
|---|---|---|
| **OLED Display** | **SDA/SCL** | I2C Kommunikation (Standard Pins) |
| **Taster (Key1)** | **GPIO 21** | Modus-Umschaltung (Pull-Up) |
| **GPS L76K** | **TX/RX** | UART Kommunikation (Standard) |
| **Arduino** | **USB** | Serielle Daten @ 115200 Baud |
### Evolutionsstufen
 1. **Vergangenheit:** Reine Konsolen-Ausgabe, instabile USB-Verbindung, kein automatisches Recovery nach Stromausfall.
 2. **Gegenwart:** Vollautomatischer Systemd-Service, Flask-Web-Interface, GPS-Synchronisation und OLED-Statusanzeige.
 3. **Zukunft (V5.x):** Implementierung einer lokalen InfluxDB für noch schnellere Abfragen und ein Wi-Fi-Hotspot-Management für automatische Handy-Verbindung.
## 3. Teil: Das Handy-Dashboard (Die Kommandozentrale)
Das Dashboard ist keine einfache Webseite, sondern ein vollwertiges Diagnose-Tool, das über Port **8085** erreichbar ist.
### Features & Funktionen
 * **Live-Telemetrie:** Echtzeit-Anzeige von RPM, Speed, EGT und AFR (aktuell Volt).
 * **Visuelle Alarme:** Das EGT-Feld blinkt rot, sobald die Temperatur **630°C** überschreitet (Motorschutz).
 * **Log-Management:**
   * **Auto-Logging:** Jede Fahrt wird als .csv im Ordner /logs gespeichert.
   * **Browsing:** Über die /logs URL können alte Fahrten direkt im Browser aufgelistet werden.
   * **Analyse:** Ein Klick auf "Analyse" bereitet die Daten grafisch auf (Pandas/Matplotlib Integration vorbereitet).
 * **Download:** Möglichkeit, die CSV-Dateien direkt auf das Handy zu ziehen, um sie in Excel oder MegaLogViewer zu prüfen.
