# 🛵 StreetDyno 2.0 – High-Precision Vespa Road Dyno & Telemetry System

[![Platform: Raspberry Pi](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.com/)
[![Firmware: Arduino Nano](https://img.shields.io/badge/Firmware-Arduino%20Nano%20(PlatformIO)-blue.svg)](https://platformio.org/)
[![Web: Flask & Chart.js](https://img.shields.io/badge/Web-Flask%20%2B%20Chart.js-brightgreen.svg)](https://flask.palletsprojects.com/)
[![Physics: Savitzky--Golay & DIN 70020](https://img.shields.io/badge/Physics-DIN%2070020%20%2B%20SG%20Filter-orange.svg)]()
[![Architecture: Clean Code & Modular](https://img.shields.io/badge/Architecture-Clean%20Code%20%26%20Modular-success.svg)]()
[![Cockpit: iPhone 15 Pro Max](https://img.shields.io/badge/Cockpit-iPhone%2015%20Pro%20Max%20Optimized-purple.svg)]()

**StreetDyno 2.0** ist ein mobiles Echtzeit-Telemetrie- und Leistungsmesssystem für klassische Vespa-Roller (Largeframe PX / VMC 177). Das System vereint hochfrequente Sensorik (RPM, AFR, EGT, GPS) mit physikalischer Fahrleistungsdynamik, automatischer Straßenneigungskompensation, 4-Zonen-Vergaserbedüsungsdiagnose, druckfertigen DIN 70020 Prüfstandsberichten und einer sauberen, modularen Clean-Code-Architektur.

---

## 📑 Inhaltsverzeichnis
1. [Systemarchitektur](#-systemarchitektur)
2. [Clean-Code Repository-Struktur](#-clean-code-repository-struktur)
3. [Kernfunktionen](#-kernfunktionen)
4. [Physik- & Dyno-Engine](#-physik---dyno-engine)
5. [Dell'Orto / BGM SI 24/24 Jetting Advisor](#-dellorto--bgm-si-2424-jetting-advisor)
6. [Hardware & Pinbelegung](#-hardware--pinbelegung)
7. [Web Interface & Endpunkte](#-web-interface--endpunkte)
8. [Automatisierte Tests & Verifikation](#-automatisierte-tests--verifikation)
9. [Installation & Service-Management](#-installation--service-management)

---

## 🏗️ Systemarchitektur

```mermaid
flowchart TD
    subgraph Hardware["Sensoren & Vespa Hardware"]
        RPM[SIP Tacho Signal / 3 Impulse] -->|D2 Interrupt| ARD[Arduino Nano V5.1]
        EGT[MAX6675 Abgastemperatur] -->|SPI D4/D5/D6| ARD
        AFR[Breitband-Lambda 0-5V] -->|A0 Analog| ARD
        GPS[Waveshare L76K GPS] -->|UART ttyAMA0 / 10Hz| PI[Raspberry Pi Zero W]
        ARD -->|USB Seriell 115200 Baud| PI
    end

    subgraph PiCore["Raspberry Pi Core (Clean Architecture)"]
        PI --> HWS[HardwareService Daemon]
        HWS --> LOG[CSV Logger / 10Hz Async]
        HWS --> DISP[SSD1306 / SH1106 OLED]
        PI --> FLASK[Flask Application Factory]
        FLASK --> BP[Blueprint: src/web/routes.py]
        BP --> DATA[Physics & Jetting Core: src/data/]
        BP --> TPL[Jinja2 Templates: src/templates/]
    end

    subgraph Cockpit["Smartphone / iPhone 15 Pro Max"]
        FLASK -->|WLAN AP| HUD[Live OLED Cockpit HUD]
        FLASK -->|WLAN AP| ANA[Interaktive Analyse & Vergleich]
        FLASK -->|WLAN AP| TUN[Vergaser-Setup Dashboard]
        FLASK -->|WLAN AP| PDF[A4 Prüfstandsbericht / AirPrint]
        HUD -->|Open-Meteo API / Mobilfunk| WTR[DIN 70020 Wetter-Norm]
        WTR -->|Client-seitiger Sync| BP
    end
```

---

## 📂 Clean-Code Repository-Struktur

Das Repository folgt strengen Clean-Code-Prinzipien mit strikter **Separation of Concerns** und zentraler mathematischer **Single Source of Truth**:

```
streetdyno2.0/
├── desktop_analyzer.py      # Desktop CLI für macOS/PC (nutzt src.data)
├── README.md                # Systemdokumentation
├── user_setup.json          # Persistente Vergaser- & Fahrzeugkonfiguration
├── firmware/
│   ├── platformio.ini       # PlatformIO Build-Konfiguration (Arduino Nano)
│   └── src/
│       └── main.cpp         # Modern C++ Firmware mit constexpr & atomarem ISR
├── src/
│   ├── config.py            # Zentrale Fahrzeugparameter, Übersetzungen, Konstanten
│   ├── main.py              # Schlanke WSGI Application Factory (< 55 Zeilen)
│   ├── data/
│   │   ├── analyzer_logic.py# Physik-Engine, SG-Filter, Neigung & DIN 70020
│   │   ├── jetting_advisor.py# 4-Zonen Vergaser-Diagnose für Dell'Orto SI 24
│   │   └── logger.py        # Threadsicherer 10Hz CSV-Logger
│   ├── hw/
│   │   ├── hardware_service.py # Threadsicherer Hardware-Daemon (Serial, GPS, OLED)
│   │   ├── gps_l76k.py      # GPSD L76K Treiber mit Höhenmessung (Alt)
│   │   ├── display_oled.py  # SSD1306/SH1106 OLED-Treiber
│   │   └── rpm_input.py     # GPIO Interrupt-Treiber
│   ├── templates/           # Saubere Jinja2 HTML/CSS/JS Templates
│   │   ├── hud.html         # Live Cockpit HUD (iPhone 15 Pro Max optimiert)
│   │   ├── logs.html        # Log-Archiv mit Run-Vergleichs-Auswahl
│   │   ├── analyze.html     # Einzel-Run Analyse mit P4-Kurve, Neigung & Wetter
│   │   ├── compare.html     # Interaktiver 2-Run Chart.js Vergleich
│   │   ├── tuning.html      # Vergaser-Setup Formular & Live-Diagnose
│   │   └── dyno_sheet.html  # Druckfertiger A4 Motorsport-Prüfstandsbericht
│   └── web/
│       └── routes.py        # Flask Blueprint mit allen Web- & JSON-API-Routen
├── systemd/
│   └── streetdyno.service   # Systemd Service-Definition
└── tests/
    └── test_dyno_core.py    # Automatisierte Unit-Test-Suite (100% Pass)
```

---

## ⚡ Kernfunktionen

* 📱 **Live Cockpit HUD (Optimiert für iPhone 15 Pro Max)**:
  * Pitch-Black OLED Dark Mode für maximale Lesbarkeit bei direkter Sonneneinstrahlung am Lenker.
  * Dynamische Safe-Area-Freistellung für **Dynamic Island** und iOS Home-Bar (`env(safe-area-inset-*)`).
  * Horizontale Drehzahlanzeige (0–10.000 U/min) mit **Shift-Light Blitz** ab 8.000 U/min.
  * Optischer Lean-AFR-Alarm (Blitzen bei AFR > 14.5 unter Last) und Abgastemperatur-Alarm (EGT $\ge 630^\circ\text{C}$).
  * **Screen WakeLock API** (verhindert Standby beim Fahren) und One-Tap Start/Stop-REC-Button.

* 🔬 **Dell'Orto / BGM SI 24/24 Carburetor Jetting Advisor**:
  * Teilt jeden Dyno-Pull automatisch in 4 Vergaser-Betriebsbereiche ein (**ND**, **Schieber**, **Mischrohr/HLKD**, **HD**).
  * Liefert konkrete Bedüsungsempfehlungen (z.B. HD 135 $\rightarrow$ 138/140 bei Magerlauf).
  * Dediziertes Web-Dashboard ([`/tuning`](http://192.168.1.130:8080/tuning)) mit persistentem JSON-Speicher auf dem Pi.

* 🏔️ **GPS Straßenneigungs- & Hangabtriebskompensation**:
  * Physikalische Formel: $F_{\text{slope}} = m \cdot g \cdot \sin\theta \approx m \cdot g \cdot s_{\%}$.
  * Dual-Modus: Automatische Höhen-Glättung via GPS oder manuelle Strecken-Presets (`0.0% Ebene`, `+0.8% Hausstrecke`, `+1.5% Bergauf`).
  * Eliminiert Bergauf-/Bergab-Verfälschungen vollständig aus den PS-Kurven.

* 🌤️ **DIN 70020 & SAE J1349 Wetter-Normierung (100% Offline-Sicher)**:
  * Der Pi bleibt im Fahrbetrieb komplett offline.
  * Das Smartphone zieht die exakten Wetterdaten (Temperatur, Luftdruck) per Mobilfunk über **Open-Meteo** anhand der GPS-Koordinaten aus dem Log.
  * Berechnet den Korrekturfaktor $k_{\text{DIN}} = \left(\frac{1013.25}{p}\right) \cdot \sqrt{\frac{T + 273.15}{293.15}}$.

* 📄 **Druckfähiger A4 Prüfstandsbericht (`/dyno_sheet`)**:
  * Offizieller Motorsport-Prüfstandsbericht mit Vektorkurven (Leistung, Drehmoment, AFR).
  * Vollständige Setup-Tabelle (Düsengrößen, Mischrohr, Auspuff, Gesamtgewicht).
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

## 🔬 Dell'Orto / BGM SI 24/24 Jetting Advisor

Der Vergaser-Berater wertet das gemessene Lambda/AFR in 4 Drehzahl- und Lastfenstern aus:

| Zone | Drehzahlbereich | Bauteil / Einfluss | Ziel-AFR | Diagnose & Auswirkung |
|---|---|---|---|---|
| **Zone 1** | 1.500 – 3.200 U/min | **Nebendüse (ND 60/160)** & Gemischschraube | **12.8 – 13.3** | Standgas, Ansprechverhalten & Schiebebetrieb |
| **Zone 2** | 3.200 – 4.800 U/min | **Gasschieber (Lemarxon Low)** Cutaway | **12.6 – 13.0** | Teillastübergang, verhindert Teillast-Magerklingeln |
| **Zone 3** | 4.800 – 6.500 U/min | **Mischrohr (x234)** & **HLKD (160)** | **12.5 – 12.9** | Vorzerstäubung beim Eintritt in die Auspuffresonanz |
| **Zone 4** | 6.500 – 9.000+ U/min | **Hauptdüse (HD 135)** | **12.4 – 12.8** | Volllast Spitzenleistung & thermischer Klemmschutz |

---

## 🔌 Hardware & Pinbelegung

### Arduino Nano V5.1
| Komponente | Arduino Pin | Funktion |
|---|---|---|
| **RPM Input (SIP Tacho Box)** | **D2** | Hardware Interrupt (RISING), 3 Impulse/Umdr. |
| **MAX6675 SO** | **D4** | SPI Serial Data Out (EGT Abgastemperatur) |
| **MAX6675 CS** | **D5** | SPI Chip Select |
| **MAX6675 SCK** | **D6** | SPI Serial Clock |
| **Lambda Controller (AFR)** | **A0** | Analog In (0–5V Breitband-Signal) |
| **Signal Masse** | **GND** | Gemeinsame Masse für Lambda & Sensoren |

### Raspberry Pi Zero W GPIO
| Komponente | Pi Pin (BCM) | Funktion |
|---|---|---|
| **OLED Display (SSD1306 / SH1106)** | **GPIO 2 / 3** | I2C SDA / SCL |
| **GPS Waveshare L76K** | **GPIO 14 / 15** | UART TX / RX (`/dev/ttyAMA0`) @ 9600 Baud |
| **Arduino Nano** | **USB** | Serieller Datenstrom (`/dev/ttyUSB0`) @ 115200 Baud |

---

## 🌐 Web Interface & Endpunkte

Das Flask-Webinterface läuft auf Port **8080** auf dem Raspberry Pi:

| Route / Endpunkt | Methode | Beschreibung |
|---|---|---|
| **`/`** | `GET` | Minimalistisches High-Contrast Live Cockpit HUD |
| **`/logs`** | `GET` | Aufgeräumtes Log-Archiv mit Multi-Select Vergleichs-Starter |
| **`/analyze?file=...`** | `GET` | P4-Dynokurve, Vergaser-Diagnose, Neigung & Wetter-Normierung |
| **`/compare?file1=...&file2=...`** | `GET` | Interaktiver 2-Run Kurvenvergleich mit Tooltips & Delta-Badges |
| **`/tuning`** | `GET` | Vergaser-Setup Formular & Live-Diagnose des letzten Pulls |
| **`/dyno_sheet?file=...`** | `GET` | Druckfertiger A4 Prüfstandsbericht für AirPrint & PDF-Export |
| **`/api/data`** | `GET` | Live JSON Telemetriestrom (RPM, Speed, AFR, EGT, GPS, Status) |
| **`/api/toggle_logging`** | `GET` | Startet / stoppt die CSV-Aufzeichnung per Tastendruck |
| **`/api/update_carb_setup`** | `POST` | Speichert geändertes Vergaser-Setup persistent in `user_setup.json` |

---

## 🧪 Automatisierte Tests & Verifikation

Das gesamte System wird durch eine automatisierte Test-Suite abgesichert:

```bash
# Unit-Tests ausführen
python3 -m unittest discover -s tests -p "test_*.py" -v
```

### Testergebnisse:
* `test_carb_jetting_advisor` $\rightarrow$ **OK** (4-Zonen Vergaser-Diagnoseregeln)
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
```

---

## 👤 Autor & Lizenz
* **Entwickler**: Roland Bachmann ([@rolovsky](https://github.com/rolovsky))
* **Projekt**: StreetDyno 2.0 (V5.1 Master Edition)
* **Lizenz**: MIT License
