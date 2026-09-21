# 🛵 StreetDyno 2.5 – Minimal Bootstrap Edition (Zero-Risk Blackbox)

[![Branch: v3-bootstrap-minimal](https://img.shields.io/badge/Branch-v3--bootstrap--minimal-brightgreen.svg)]()
[![Architektur: Entkoppelt (IPC)](https://img.shields.io/badge/Architektur-2--Prozess--Entkoppelt%20(IPC)-blue.svg)]()
[![Hardware: Pi Zero 2 W + Arduino Nano](https://img.shields.io/badge/Hardware-Pi%20Zero%202%20W%20%2B%20Nano-orange.svg)]()
[![CPU-Last: <2%](https://img.shields.io/badge/CPU--Last-%3C%202%25%20(Blackbox)-success.svg)]()
[![RAM: ~50MB Total](https://img.shields.io/badge/RAM-~15MB%20Logger%20%2B%20~34MB%20Web-purple.svg)]()

Die **Minimal Bootstrap Edition (Phase 2.5)** ist eine radikal simplifizierte, hochstabile Neuausrichtung von StreetDyno für den Raspberry Pi Zero 2 W. 

Während der Fahrt agiert der Pi als **unzerstörbarer, leichtgewichtiger Blackbox-Datenlogger** (<2% CPU). Web-Dashboard und Datenabruf laufen als eigenständiger Prozess strikt getrennt – ohne Hardware-Locks, ohne Serial-Port-Konkurrenz und ohne Risiko von Messaussetzern.

---

## 📑 Inhaltsverzeichnis
1. [Architektur & Entkopplung](#-architektur--entkopplung)
2. [Hardware- & Sensor-Spezifikation](#-hardware---sensor-spezifikation)
3. [Die beiden Kernkomponenten](#-die-beiden-kernkomponenten)
   - [simple_logger.py (Blackbox Daemon)](#1-simple_loggerpy-blackbox-telemetrie-daemon)
   - [flask_web.py (Web- & API-Server)](#2-flask_webpy-web---api-server)
4. [Interprozesskommunikation (IPC) & Signale](#-interprozesskommunikation-ipc--signale)
5. [Web Interface & Endpunkte](#-web-interface--endpunkte)
6. [Systemd Service Management](#-systemd-service-management)
7. [Rollback-Garantie (V5.1 Golden Master)](#-rollback-garantie-v51-golden-master)

---

## 🏗️ Architektur & Entkopplung

Im bisherigen monolithischen Stand (V5.1) liefen Serial-Polling, GPS, Filterung, WOT-Trigger, OLED-Display und Flask in einem gemeinsamen Prozess. In Phase 2.5 sind Datenerfassung und Anzeige **vollständig entkoppelt**:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          HARDWARE-EBENE                                   │
│  Arduino Nano (D2=RPM, A0=AFR, D4-D6=EGT, D7=CHT) ──▶ /dev/ttyUSB0 (115k) │
│  Waveshare L76K GPS (UART / gpsd JSON Socket)      ──▶ Port 2947          │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│               PROZESS 1: streetdyno-logger.service                        │
│                         (src/simple_logger.py)                            │
│                                                                           │
│  • Thread 1 (Main): Serial Read, S-03 Hardware-Timestamps, XOR Checksum   │
│  • Thread 2 (Async): gpsd Non-blocking JSON Polling                       │
│  • Power-Safe CSV Write: flush() pro Zeile, fsync() alle 10 Zeilen        │
│  • Continuous Trip Logging (RPM > 400 oder Speed > 5 km/h)                │
│  • Signal-Handler: SIGHUP (Config Reload), SIGUSR1 (Dyno Toggle)          │
└──────────────────┬─────────────────────────────────────▲──────────────────┘
                   │                                     │
         schreibt atomar                   sendet POSIX-Signale
         (os.replace)                      (über PID-Datei)
                   │                                     │
                   ▼                                     │
        ┌────────────────────────────┐                   │
        │ /tmp/streetdyno_state.json │                   │
        │ /tmp/streetdyno_logger.pid │ ──────────────────┘
        └────────────────────────────┘
                   ▲
             liest Zustand
                   │
┌──────────────────┴────────────────────────────────────────────────────────┐
│                PROZESS 2: streetdyno-web.service                          │
│                          (src/flask_web.py)                               │
│                                                                           │
│  • Reiner Read-Only Webserver (kein Zugriff auf /dev/ttyUSB0)             │
│  • Live Cockpit HUD (/ & /hud) via tmpfs JSON-State                       │
│  • Vergaser-Setup (/tuning) & DIN 70020 RAD Wetterkompensation           │
│  • Hardware-Diagnose (/diagnostics) & Sternmasse-Audit                    │
│  • CSV Log- & Fahrtendownload (/logs & /trips)                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### Die wichtigsten Design-Entscheidungen:
1. **OLED physisch entfallen**: Keine I2C/SPI-Displaytreiber (`luma`, `RPi.GPIO`), keine Bus-Latenzen im Mess-Loop.
2. **Asynchrones GPS**: `GPSReader` läuft in eigenem Thread; der Serial-Thread fragt das GPS mit `Lock.acquire(timeout=0)` ab – Messungen blockieren **niemals**.
3. **Keine schweren Imports im Logger**: `simple_logger.py` importiert weder `pandas`, `matplotlib`, `flask` noch GUI-Bibliotheken.
4. **WOT-Erkennung im Post-Processing**: Keine Echtzeit-Ableitungen ($d\text{RPM}/dt$) während der Fahrt. Die Rohdaten landen in der CSV; WOT-Pulls werden nach der Fahrt offline segmentiert.

---

## 🔌 Hardware- & Sensor-Spezifikation

| Komponente | Schnittstelle / Pin | Funktion / Spezifikation |
|---|---|---|
| **Arduino Nano** | USB (`/dev/ttyUSB0` @ 115200 Baud) | Sensor-Hub mit 16 MHz Quarz-Zeitbasis |
| **Drehzahl (RPM)** | Pin D2 (Interrupt 0, FALLING) | 3 Impulse/Umdrehung (SIP Tacho / Ducati CDI) |
| **Breitband-Lambda (AFR)** | Pin A0 (Analog ADC) | Koso 0–5V Breitbandcontroller (0–20 AFR) |
| **Abgastemperatur (EGT)** | Pins D4 (SCK), D5 (CS), D6 (SO) | MAX6675 Thermoelement Typ K (bis 1024°C) |
| **Zylinderkopf (CHT)** | Pin D7 (CS2) | MAX6675 Sensor für Kopftemperatur (bis 170°C) |
| **GPS Waveshare L76K** | UART (`/dev/ttyAMA0` via `gpsd`) | 10 Hz NMEA, Steigung ($F_{\text{slope}}$) & Zeitsync |

### Telemetrie-Frame Format (XOR Checksum)
```text
$MICROS;RPM;AFR;EGT;CHT*XX\n
```
- `$MICROS`: 32-Bit Mikrosekunden-Zähler des ATmega328P (`micros()`)
- `*XX`: Hexadezimale XOR-Prüfsumme aller Zeichen zwischen `$` und `*`
- **S-03 Zeitbasis**: Hardware-Synchronisation eliminiert Linux-Scheduling-Jitter auf dem Pi Zero 2 W. CSV-Timestamps besitzen 0.1ms Auflösung (`f"{ts:.4f}"`).

---

## 📦 Die beiden Kernkomponenten

### 1. `simple_logger.py` (Blackbox Telemetrie Daemon)

- **Datei**: [`src/simple_logger.py`](file:///src/simple_logger.py)
- **Ressourcenverbrauch**: ~15 MB RAM, ~1.5% CPU-Last auf Pi Zero 2 W.
- **Speicherort Trip-CSVs**: `logs/trips/trip_YYYYMMDD-HHMMSS.csv`
  - Startet automatisch, sobald `RPM >= 400` oder `Speed >= 5 km/h` für 5 Zyklen anliegt.
  - Schließt die Datei nach 30 Sekunden Motorstillstand mit abschließendem `fsync()`.
- **Speicherort Dyno-CSVs**: `logs/dyno_log_YYYYMMDD-HHMMSS.csv`
  - Startet/stoppt manuell via SIGUSR1 (HUD-Button).
- **Power-Safe Flush**:
  - `flush()` bei jedem Sample (10 Hz).
  - `os.fsync()` alle 10 Samples (~1 Sekunde max. Datenverlust bei plötzlichem Spannungsabfall).

### 2. `flask_web.py` (Web- & API-Server)

- **Datei**: [`src/flask_web.py`](file:///src/flask_web.py)
- **Ressourcenverbrauch**: ~34 MB RAM, ~2% CPU-Last.
- **Isolationsgarantie**: Kann jederzeit abstürzen, neu gestartet oder beendet werden, **ohne** dass der Logger Daten verliert oder der serielle Port blockiert wird.
- **Fallback-Modus**: Ist der Logger nicht aktiv, liefert die API statuskonform `{"status": "LOGGER_OFFLINE"}`.

---

## 🔄 Interprozesskommunikation (IPC) & Signale

| Kanal | Mechanismus | Zweck |
|---|---|---|
| **Zustandsübertragung** | `/tmp/streetdyno_state.json` | Logger schreibt alle 200ms via `os.replace` atomar den aktuellen Zustand; Flask liest diesen non-blocking. |
| **Prozessidentifikation** | `/tmp/streetdyno_logger.pid` | Enthält die PID des laufenden `simple_logger.py`. |
| **Dyno-Pull Trigger** | `SIGUSR1` an Logger-PID | Startet oder stoppt eine gezielte `dyno_log_*.csv` Aufzeichnung. |
| **Setup-Reload** | `SIGHUP` an Logger-PID | Veranlasst den Logger, `lambda_ground_offset_mv` aus `user_setup.json` neu einzulesen (ohne Neustart). |

---

## 🌐 Web Interface & Endpunkte

Das Web-Interface ist für mobile Browser (Safari iOS / Chrome Android) unter `http://<pi-ip>:8080` optimiert:

| Pfad | Typ | Beschreibung |
|---|---|---|
| `/` bzw. `/hud` | HTML | **Live Cockpit HUD**: Großanzeige für Drehzahl, Speed, AFR, EGT, CHT mit Dämpfung, Schaltblitz und Audio-Alarmen. |
| `/tuning` | HTML/POST | **Vergaser-Setup & Wetter-Kompensation**: Konfiguration der Düsen/Nadeln mit DIN 70020 RAD-Berechnung (Relative Air Density). Speichert direkt in `user_setup.json` und triggert `SIGHUP`. |
| `/diagnostics` | HTML | **Hardware-Diagnose**: Live-Spannung an A0, Offset-Prüfung, Sternmasse-Audit-Protokolle. |
| `/logs` | HTML | Übersicht aller manuellen Prüfläufe (`dyno_log_*.csv`) mit Downloadlink. |
| `/trips` | HTML | Übersicht aller aufgezeichneten Dauerfahrten (`trip_*.csv`) mit Downloadlink. |
| `/api/telemetry` | JSON | Liefert den unveränderten Zustand aus `/tmp/streetdyno_state.json`. |
| `/api/data` | JSON | Kompatibilitäts-Endpunkt für das bestehende JavaScript im HUD. |
| `/api/toggle_dyno` | JSON | Sendet `SIGUSR1` an den Logger und gibt den neuen Status zurück. |
| `/api/update_carb_setup` | JSON/POST | Speichert Setup-Werte und sendet `SIGHUP` an den Logger. |
| `/api/diagnostics/live` | JSON | Liefert Telemetriedaten angereichert mit rekonstruierter A0-Analogspannung. |

---

## ⚙️ Systemd Service Management

Die beiden Prozesse werden über zwei getrennte Systemd-Units verwaltet:

```bash
# Status beider Dienste prüfen
systemctl status streetdyno-logger.service
systemctl status streetdyno-web.service

# Live-Logs des Loggers ansehen
journalctl -u streetdyno-logger.service -f

# Live-Logs des Webservers ansehen
journalctl -u streetdyno-web.service -f

# Dienste neu starten
sudo systemctl restart streetdyno-logger.service
sudo systemctl restart streetdyno-web.service
```

### Autostart aktivieren / deaktivieren
```bash
# Bootstrap 2.5 aktivieren
sudo systemctl enable streetdyno-logger.service streetdyno-web.service

# Dienste stoppen
sudo systemctl stop streetdyno-web.service streetdyno-logger.service
```

---

## 🛡️ Rollback-Garantie (V5.1 Golden Master)

Der bewährte Entwicklungsstand V5.1 ist durch ein unveränderliches Git-Tag und die unberührte Original-Servicedatei abgesichert:

- **Git-Tag**: `v5.1-golden-master` (Commit `03ba306`)
- **Original-Dateien**: `src/main.py` und `systemd/streetdyno.service` wurden **nicht modifiziert**.

### Sofortiger Rollback zu V5.1 bei Bedarf:
```bash
# 1. Bootstrap-Dienste stoppen und deaktivieren
sudo systemctl stop streetdyno-web.service streetdyno-logger.service
sudo systemctl disable streetdyno-web.service streetdyno-logger.service

# 2. V5.1 Golden Master aktivieren und starten
sudo systemctl enable streetdyno.service
sudo systemctl start streetdyno.service

# 3. Git-Stand zurücksetzen (optional)
git checkout main
```
