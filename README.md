# 🛵 StreetDyno 2.0 – High-Precision Vespa Road Dyno & Telemetry System

[![Platform: Raspberry Pi](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.com/)
[![Firmware: Arduino Nano](https://img.shields.io/badge/Firmware-Arduino%20Nano%20(AVR)-blue.svg)](https://platformio.org/)
[![Web: Flask & Chart.js](https://img.shields.io/badge/Web-Flask%20%2B%20Chart.js-brightgreen.svg)](https://flask.palletsprojects.com/)
[![Physics: Savitzky--Golay & DIN 70020](https://img.shields.io/badge/Physics-DIN%2070020%20%2B%20SG%20Filter-orange.svg)]()
[![Architecture: Clean Code & Modular](https://img.shields.io/badge/Architecture-Clean%20Code%20%26%20Modular-success.svg)]()
[![Cockpit: iPhone 15 Pro Max](https://img.shields.io/badge/Cockpit-iPhone%2015%20Pro%20Max%20Optimized-purple.svg)]()
[![Tests: 13/13 Passing](https://img.shields.io/badge/Tests-13%2F13%20Passed%20(100%25)-brightgreen.svg)]()

**StreetDyno 2.0** ist ein mobiles Echtzeit-Telemetrie- und Leistungsmesssystem für klassische Vespa-Roller (Largeframe PX / VMC 177). Das System vereint hochfrequente Sensorik (RPM, AFR, EGT, GPS) mit physikalischer Fahrleistungsdynamik, autonomem **WOT Auto-Trigger (3. Gang)**, **Multi-Period Impuls-Akkumulation**, automatischer Straßenneigungskompensation, **4-Zonen SI 24/24 Vergaser-Matrix mit Ethanol-Stöchiometrie**, druckfertigen DIN 70020 Prüfstandsberichten und einer sauberen, modularen Clean-Code-Architektur.

---

## 📑 Inhaltsverzeichnis
1. [Systemarchitektur](#-systemarchitektur)
2. [Clean-Code Repository-Struktur](#-clean-code-repository-struktur)
3. [Kernfunktionen](#-kernfunktionen)
4. [Physik- & Dyno-Engine](#-physik---dyno-engine)
5. [Dell'Orto / BGM SI 24/24 Jetting Advisor & Setup-Matrix](#-dellorto--bgm-si-2424-jetting-advisor--setup-matrix)
6. [Hardware & Pinbelegung](#-hardware--pinbelegung)
7. [Web Interface & Endpunkte](#-web-interface--endpunkte)
8. [Automatisierte Tests & Verifikation](#-automatisierte-tests--verifikation)
9. [Installation & Service-Management](#-installation--service-management)

---

## 🏗️ Systemarchitektur

```mermaid
flowchart TD
    subgraph Hardware["Sensoren & Vespa Hardware"]
        RPM[SIP Tacho Signal / 3 Impulse] -->|D2 Interrupt FALLING| ARD[Arduino Nano V5.1]
        EGT[MAX6675 Abgastemperatur] -->|SPI D4/D5/D6| ARD
        AFR[Breitband-Lambda 0-5V] -->|A0 Analog 4.71V Ref| ARD
        GPS[Waveshare L76K GPS] -->|UART ttyAMA0 / 10Hz| PI[Raspberry Pi Zero 2 W]
        ARD -->|USB Seriell 115200 Baud / Multi-Period Accumulator| PI
    end

    subgraph PiCore["Raspberry Pi Core (Debian Trixie / ZRAM 416MB)"]
        PI --> HWS[HardwareService Daemon]
        HWS --> TRIG[3-Punkt WOT Auto-Trigger]
        HWS --> LOG[CSV Logger mit 1.0s Pre-Trigger Buffer]
        HWS --> DISP[SSD1306 / SH1106 OLED]
        PI --> FLASK[Flask Application Factory]
        FLASK --> BP[Blueprint: src/web/routes.py]
        BP --> DATA[Physics & Jetting Core: src/data/]
        BP --> TPL[Jinja2 Templates: src/templates/]
    end

    subgraph Cockpit["Smartphone / iPhone 15 Pro Max (http://streetdyno.local:8080)"]
        FLASK -->|WLAN / mDNS| HUD[Live OLED Cockpit HUD]
        FLASK -->|WLAN / mDNS| ANA[Interaktive Analyse & Vergleich]
        FLASK -->|WLAN / mDNS| TUN[Vergaser-Setup Dashboard]
        FLASK -->|WLAN / mDNS| PDF[A4 Prüfstandsbericht / AirPrint]
        HUD -->|Open-Meteo API / Mobilfunk| WTR[DIN 70020 Wetter-Norm]
        WTR -->|Client-seitiger Sync| BP
    end
```

---

## 📂 Clean-Code Repository-Struktur

Das Repository folgt strengen Clean-Code-Prinzipien mit strikter **Separation of Concerns** und zentraler mathematischer **Single Source of Truth**:

```
streetdyno2.0/
├── .agents/rules/           # Persistente Projekt- & Hardware-Richtlinien
│   └── streetdyno-guidelines.md
├── desktop_analyzer.py      # Desktop CLI für macOS/PC (nutzt src.data)
├── README.md                # Systemdokumentation
├── user_setup.json          # Persistente Vergaser- & Fahrzeugkonfiguration
├── firmware/
│   ├── platformio.ini       # PlatformIO Build-Konfiguration (Arduino Nano)
│   └── src/
│       └── main.cpp         # Modern C++ Firmware mit Multi-Period Akkumulator
├── src/
│   ├── config.py            # Zentrale Fahrzeugparameter, Bauteil-Mappings & Konstanten
│   ├── main.py              # Schlanke WSGI Application Factory (< 55 Zeilen)
│   ├── data/
│   │   ├── analyzer_logic.py# Physik-Engine, SG-Filter, Neigung & DIN 70020
│   │   ├── jetting_advisor.py# 4-Zonen Vergaser-Diagnose & Lambda-Stöchiometrie
│   │   └── logger.py        # Threadsicherer 10Hz CSV-Logger mit Pre-Trigger Puffer
│   ├── hw/
│   │   ├── hardware_service.py # Threadsicherer Hardware-Daemon & WOT Auto-Trigger
│   │   ├── gps_l76k.py      # GPSD L76K Treiber mit Höhenmessung (Alt)
│   │   ├── display_oled.py  # SSD1306/SH1106 OLED-Treiber
│   │   └── rpm_input.py     # GPIO Interrupt-Treiber
│   ├── templates/           # Saubere Jinja2 HTML/CSS/JS Templates
│   │   ├── hud.html         # Live Cockpit HUD (iPhone 15 Pro Max optimiert)
│   │   ├── logs.html        # Log-Archiv mit Multi-Select Vergleichs-Starter
│   │   ├── analyze.html     # Einzel-Run Analyse mit P4-Kurve, Neigung & Wetter
│   │   ├── compare.html     # Interaktiver 2-Run Chart.js Vergleich
│   │   ├── tuning.html      # Vergaser-Setup Formular mit Bauteil-Dropdowns
│   │   └── dyno_sheet.html  # Druckfertiger A4 Motorsport-Prüfstandsbericht
│   └── web/
│       └── routes.py        # Flask Blueprint mit allen Web- & JSON-API-Routen
├── systemd/
│   └── streetdyno.service   # Systemd Service-Definition
└── tests/
    └── test_dyno_core.py    # Automatisierte Unit-Test-Suite (13 Tests, 100% Pass)
```

---

## ⚡ Kernfunktionen

* 🚀 **Autonomer WOT Auto-Trigger (3. Gang Messfahrten)**:
  * **Automatischer Aufzeichnungsstart**: Erkennt Vollgasbeschleunigung im 3. Gang ($\ge 2.800\text{ RPM}$, $\text{dRPM/dt} \ge 200\text{ RPM/s}$ über 300ms, Übersetzung $60\text{--}110\text{ RPM/(km/h)}$).
  * **1,0s Pre-Trigger Ringspeicher**: Der CSV-Logger speichert den rollenden Vorlauf aus dem RAM mit ab, sodass der exakte Startzeitpunkt des Gasaufreißens erfasst wird.
  * **Intelligenter Auto-Stop**: Beendet die Messung nach Erreichen des Peaks ($\Delta\text{RPM} \le -350\text{ RPM}$ oder Gas weggenommen).
  * **Spike-Schutz**: Verwirft unvollständige Läufe ($< 1{,}0\text{s}$ oder $< 1.200\text{ RPM}$ Anstieg) automatisch.

* ⏱️ **Jitter-freie Multi-Period Impuls-Akkumulation (Arduino Nano)**:
  * Der Hardware-Interrupt summiert alle Zündimpulse und Gesamt-Mikrosekunden im 100ms-Fenster.
  * Bei 6.000 RPM wird über **~30 reale Zündungen gemittelt**, wodurch Signalrauschen und Zündfunkenprellen (1500µs GSF-Lockout) eliminiert werden.
  * Sendet stufenlose, ungerundete Raw-Floats an den Raspberry Pi.
  * **3-Punkt Rolling Central Derivative**: Berechnet stufenfreie $\text{dRPM/dt}$ Beschleunigungswerte ohne Phasenverzug.

* 📱 **Live Cockpit HUD (Optimiert für iPhone 15 Pro Max)**:
  * Erreichbar über mDNS: **`http://streetdyno.local:8080`**.
  * Pitch-Black OLED Dark Mode für maximale Lesbarkeit bei direkter Sonneneinstrahlung am Lenker.
  * Dynamische Safe-Area-Freistellung für **Dynamic Island** und iOS Home-Bar (`env(safe-area-inset-*)`).
  * Horizontale Drehzahlanzeige (0–10.000 U/min) mit **Shift-Light Blitz** ab 8.000 U/min.
  * Optischer Lean-AFR-Alarm (Blitzen bei AFR > 14.5 unter Last) und Abgastemperatur-Alarm (EGT $\ge 630^\circ\text{C}$).
  * **Screen WakeLock API** (verhindert Standby beim Fahren).

* 🔬 **Dell'Orto / BGM SI 24/24 Carburetor Jetting Advisor & Setup-Matrix**:
  * **Stöchiometrie-Skalierung**: Unterstützt Super E5 ($14{,}30$), Super E10 ($14{,}10$) und SuperPlus E0 ($14{,}70$). Alle 4 Zonen skalieren nach 2-Takt-Volllast-$\lambda$ ($\lambda \approx 0{,}86$).
  * **Gasschieber-Matrix**: Lemarxon Low (fett), Lemarxon Mid, BGM FastFlow Standard mit Cutaway (mager).
  * **Ansaugung & Trichter**: Polini Venturi Trichter (+6 bis +10 HD-Kompensation), 22mm Lemarxon Reduzierhülse, gebohrter Filter (5/8mm), Offen.
  * **Vergaserwanne / Deckel**: Polini Airbox (großer Deckel), Originaldeckel, Ohne Deckel.
  * Dediziertes Web-Dashboard ([`/tuning`](http://streetdyno.local:8080/tuning)) mit persistentem JSON-Speicher auf dem Pi.

* 🏔️ **GPS Straßenneigungs- & Hangabtriebskompensation**:
  * Physikalische Formel: $F_{\text{slope}} = m \cdot g \cdot \sin\theta \approx m \cdot g \cdot s_{\%}$.
  * Dual-Modus: Automatische Höhen-Glättung via GPS oder manuelle Strecken-Presets (`0.0% Ebene`, `+0.8% Hausstrecke`, `+1.5% Bergauf`).
  * Eliminiert Bergauf-/Bergab-Verfälschungen vollständig aus den PS-Kurven.

* 🌤️ **DIN 70020 & SAE J1349 Wetter-Normierung (100% Offline-Sicher)**:
  * Das Smartphone zieht die exakten Wetterdaten (Temperatur, Luftdruck) per Mobilfunk über **Open-Meteo** anhand der GPS-Koordinaten aus dem Log.
  * Berechnet den Korrekturfaktor $k_{\text{DIN}} = \left(\frac{1013.25}{p}\right) \cdot \sqrt{\frac{T + 273.15}{293.15}}$.

* 📄 **Druckfähiger A4 Prüfstandsbericht (`/dyno_sheet`)**:
  * Offizieller Motorsport-Prüfstandsbericht mit Vektorkurven (Leistung, Drehmoment, AFR).
  * Vollständige Setup-Tabelle (Düsengrößen, Mischrohr, Schieber, Ansaugung, Airbox, Gesamtgewicht).
  * Ein-Klick iOS Safari **"Als PDF sichern"** und AirPrint.

* 📊 **In-Browser Run-Vergleich (`/compare`)**:
  * Schneller Vergleich zweier Dyno-Runs mit interaktivem Chart.js Multi-Line-Overlay und $\Delta\text{PS}$ / $\Delta\text{Nm}$ Deltas.

---

## 📐 Physik- & Dyno-Engine

Die Berechnung der Rad- und Motorleistung basiert auf dem vollständigen fahrphysikalischen Kräftegleichgewicht:

$$F_{\text{wheel}} = F_{\text{acc}} + F_{\text{aero}} + F_{\text{roll}} + F_{\text{slope}}$$

$$F_{\text{wheel}} = (m \cdot k_{\text{rot}}) \cdot a + \frac{1}{2} \rho \cdot c_w A \cdot v^2 + c_r \cdot m \cdot g + m \cdot g \cdot \sin\theta$$

$$P_{\text{engine}} = \frac{F_{\text{wheel}} \cdot v}{\eta_{\text{trans}}} \cdot k_{\text{DIN}}$$

### Fahrzeug-Referenzkonfiguration (VMC 177 / Vespa PX):
| Parameter | Wert | Beschreibung |
|---|---|---|
| Gesamtmasse ($m$) | **190.0 kg** | 112 kg Vespa PX + 78 kg Fahrer |
| Massenfaktor ($k_{\text{rot}}$) | **1.05** | Rotatorische Trägheit (Polrad, Kurbelwelle, Räder) |
| Abrollumfang ($U$) | **1.350 m** | Reifen 100/90-10 |
| Primärübersetzung | **2.957** | 23/68 Zähne |
| Getriebeübersetzung | **2.235** | 3. Gang (17/38 Zähne) $\rightarrow i_{\text{total}} = 6.61$ |
| Luftwiderstand ($c_w A$) | **0.50 m²** | Fahrer leicht geduckt |
| Rollwiderstand ($c_r$) | **0.015** | Straßenreifen 2.2 bar |
| Getriebewirkungsgrad ($\eta$) | **0.90** | Schaltgetriebe & Primärtrieb |

---

## 🔬 Dell'Orto / BGM SI 24/24 Jetting Advisor & Setup-Matrix

Der Vergaser-Berater wertet das gemessene Lambda/AFR in 4 Drehzahl- und Lastfenstern auf Basis der Kraftstoff-Stöchiometrie aus:

| Zone | Drehzahlbereich | Bauteil / Einfluss | Ziel-Lambda ($\lambda$) | Ziel-AFR (Super E5) | Diagnose & Auswirkung |
|---|---|---|---|---|---|
| **Zone 1** | 1.500 – 3.200 U/min | **Nebendüse (ND 60/160)** & Gemischschraube | $0{,}895\text{--}0{,}930$ | **12.8 – 13.3** | Standgas, Ansprechverhalten & Schiebebetrieb |
| **Zone 2** | 3.200 – 4.800 U/min | **Gasschieber (Lemarxon Low/Mid)** | $0{,}881\text{--}0{,}909$ | **12.6 – 13.0** | Teillastübergang (1/4–1/2 Gas), verhindert Magerlöcher |
| **Zone 3** | 4.800 – 6.500 U/min | **Mischrohr (x234)** & **HLKD (160)** | $0{,}874\text{--}0{,}902$ | **12.5 – 12.9** | Vorzerstäubung beim Eintritt in die Auspuffresonanz |
| **Zone 4** | 6.500 – 9.500 U/min | **Hauptdüse (HD 135)** & **Polini Venturi** | $0{,}867\text{--}0{,}895$ | **12.4 – 12.8** | Volllast Spitzenleistung & thermischer Klemmschutz |

---

## 🔌 Hardware & Pinbelegung

### Arduino Nano V5.1
| Komponente | Arduino Pin | Funktion |
|---|---|---|
| **RPM Input (SIP Tacho Box)** | **D2** | Hardware Interrupt INT0 (`FALLING`), 3 Impulse/Umdr. |
| **MAX6675 SO** | **D4** | SPI Serial Data Out (EGT Abgastemperatur) |
| **MAX6675 CS** | **D5** | SPI Chip Select |
| **MAX6675 SCK** | **D6** | SPI Serial Clock |
| **Lambda Controller (AFR)** | **A0** | Analog In (0–5V Breitband-Signal @ 4.71V USB-Ref) |
| **Signal Masse** | **GND** | Gemeinsame Masse für Lambda & Sensoren |

### Raspberry Pi Zero 2 W GPIO & Schnittstellen
| Komponente | Pi Schnittstelle | Funktion |
|---|---|---|
| **OLED Display (SSD1306 / SH1106)** | **I2C-1 (`/dev/i2c-1`)** | Hardware I2C (GPIO 2 / 3) |
| **GPS Waveshare L76K** | **UART (`/dev/ttyAMA0`)** | 9600 Baud via `gpsd` (JSON-Modus) |
| **Arduino Nano** | **USB (`/dev/ttyUSB0`)** | 115200 Baud @ 10Hz Multi-Period Stream |
| **Arbeitsspeicher-Schutz** | **ZRAM (`/dev/zram0`)** | 416 MB LZ4-komprimierter RAM-Swap |

---

## 🌐 Web Interface & Endpunkte

Das Flask-Webinterface läuft auf Port **8080** auf dem Raspberry Pi und ist im Netzwerk über **`http://streetdyno.local:8080`** erreichbar:

| Route / Endpunkt | Methode | Beschreibung |
|---|---|---|
| **`/`** | `GET` | Minimalistisches High-Contrast Live Cockpit HUD |
| **`/logs`** | `GET` | Aufgeräumtes Log-Archiv mit Multi-Select Vergleichs-Starter |
| **`/analyze?file=...`** | `GET` | P4-Dynokurve, Vergaser-Diagnose, Neigung & Wetter-Normierung |
| **`/compare?file1=...&file2=...`** | `GET` | Interaktiver 2-Run Kurvenvergleich mit Tooltips & Delta-Badges |
| **`/tuning`** | `GET` | Vergaser-Setup Formular mit Bauteil-Dropdowns & Live-Diagnose |
| **`/dyno_sheet?file=...`** | `GET` | Druckfertiger A4 Prüfstandsbericht für AirPrint & PDF-Export |
| **`/api/data`** | `GET` | Live JSON Telemetriestrom (RPM, Speed, AFR, EGT, GPS, Status) |
| **`/api/toggle_logging`** | `GET` | Startet / stoppt die CSV-Aufzeichnung manuell |
| **`/api/update_carb_setup`** | `POST` | Speichert geändertes Vergaser-Setup persistent in `user_setup.json` |
| **`/api/toggle_display`** | `GET` | Schaltet die OLED-Anzeigemodi um (RPM $\rightarrow$ SPEED $\rightarrow$ AFR $\rightarrow$ EGT) |

---

## 🧪 Automatisierte Tests & Verifikation

Das gesamte System wird durch eine automatisierte Test-Suite abgesichert:

```bash
# Unit-Tests auf dem Pi ausführen
python3 -m unittest discover tests -v
```

### Testergebnisse (13/13 Passed):
* `test_logger_prebuffer_and_discard` $\rightarrow$ **OK** (1.0s Pre-Trigger & Auto-Discard)
* `test_carb_jetting_advisor` $\rightarrow$ **OK** (4-Zonen Vergaser-Diagnoseregeln)
* `test_fuel_stoichiometry_scaling` $\rightarrow$ **OK** (Dynamische Ziel-AFR Skalierung für E5, E10, E0)
* `test_slide_and_intake_diagnostics` $\rightarrow$ **OK** (BGM Cutaway $\leftrightarrow$ Lemarxon & Polini Venturi Empfehlungen)
* `test_din70020_weather_factor` $\rightarrow$ **OK** (DIN 70020 & SAE J1349 Faktoren)
* `test_gear_ratios` $\rightarrow$ **OK** (Getriebeuntersetzungen & Gangerkennung)
* `test_nd_ratio_parser` $\rightarrow$ **OK** (Nebendüsen-Verhältnisberechnung)
* `test_slope_calculation` $\rightarrow$ **OK** (Straßenneigung & Hangabtrieb)
* `test_api_data` $\rightarrow$ **OK** (10Hz Telemetrie JSON Stream)
* `test_api_update_carb_setup` $\rightarrow$ **OK** (Persistente JSON-Speicherung)
* `test_hud_page`, `test_logs_page`, `test_tuning_page` $\rightarrow$ **OK** (200 OK Response)

---

## 🛠️ Installation & Service-Management

StreetDyno 2.0 startet automatisch beim Booten über einen systemd-Service:

```bash
# Service Status prüfen
systemctl status streetdyno.service

# Service neu starten
sudo systemctl restart streetdyno.service

# Live-Logs ansehen
journalctl -u streetdyno.service -f

# Arduino Nano Firmware direkt vom Pi flashen
sudo systemctl stop streetdyno.service
sudo fuser -k /dev/ttyUSB0
/usr/local/bin/arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328 /tmp/sketch_build
sudo systemctl start streetdyno.service
```

---

## 👤 Autor & Lizenz
* **Entwickler**: Roland Bachmann ([@rolovsky](https://github.com/rolovsky))
* **Projekt**: StreetDyno 2.0 (V5.1 Master Edition)
* **Lizenz**: MIT License
