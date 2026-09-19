# StreetDyno 2.0 — Hardware & Sensor Hardening Kompendium

> **Praxisleitfaden für vibrationsfesten, störungsfreien und langlebigen Betrieb an klassischen 2-Takt-Schaltrollern (Vespa PX, Rally, Sprint, Smallframe mit Ducati CDI, SIP Vape oder Vespatronic).**

---

## 1. Einleitung & physikalische Herausforderungen auf der Vespa

Der Einsatz von Mikroelektronik (Raspberry Pi Zero 2 W, Arduino Nano ATmega328P, empfindliche Sensoren) an einem klassischen Vespa-Zweitakter stellt höchste Anforderungen an die Hardware-Architektur:

1. **Extreme Vibrationen:** Einzylinder-Zweitaktmotoren ohne Ausgleichswelle erzeugen erhebliche Vibrationen im Frequenzbereich von 20 Hz bis 250 Hz (1.200 bis 15.000 U/min). Ungesicherte Schraubklemmen, starre Leitungen und ungefederte Platinen brechen oder lockern sich innerhalb kürzester Zeit.
2. **Harte elektromagnetische Störfelder (EMI):** Hochspannungszündungen (Ducati CDI mit 300 V Primär- und bis zu 25.000 V Sekundärspannung) induzieren massive Spikes auf benachbarte Kabelbäume und Masseleitungen.
3. **Schmutziges 12V-Bordnetz:** Wechselstrom-Lichtmaschinen (AC) mit einfachen Thyristor-Spannungsreglern (BGM, Kokusan oder originale Vespa-Regler) liefern stark verrauschte Gleichspannung mit Lastabwurf-Spitzen (*Load Dump*) von über 40 V sowie Spannungseinbrüchen (*Brownouts*) bei niedriger Leerlaufdrehzahl.
4. **Masseschleifen (*Ground Loops*):** Fließen Heizströme (z. B. 1,5 A der Breitbandlambda) oder Zündströme über dieselbe Masseleitung wie empfindliche Analogsignale, verfälschen Spannungsabfälle (Ohm'sches Gesetz $U = R \cdot I$) die Messergebnisse drastisch.

---

## 2. 12V-Bordnetz & Brownout-Schutz

### 2.1 Problemstellung
Klassische Vespa-Gleichstromkreise (DC) brechen im Standgas (unter 1.500 U/min) bei eingeschaltetem Scheinwerfer oft auf unter 9 V ein. Beim plötzlichen Gaswegnehmen oder Schalten treten induktive Spannungsspitzen von 35 V bis 60 V auf.
Ein direkter Anschluss eines Standard-USB-Adapters führt entweder zur Zerstörung des Reglers oder zu spontanen Reboots (*Brownouts*) des Raspberry Pi, was Dateisystemkorruption auf der MicroSD-Karte nach sich zieht.

### 2.2 Schutzschaltung & Empfohlene Topologie

```
[12V Bordnetz DC] 
       │
      [+]
       │
     ┌─┴────────────────────────┐
     │  Schmelzsicherung 2A     │ (KFZ-Flachsicherung mini)
     └─┬────────────────────────┘
       │
     ┌─┴────────────────────────┐
     │  Schottky-Diode (1N5822) │ (Verpolungsschutz & Rückfluss-Sperre)
     └─┬────────────────────────┘
       │
       ├────────────────────────────────┐
       │                                │
     ┌─┴───────────────────────┐      ┌─┴────────────────────────┐
     │  TVS-Diode (SMBJ24A)    │      │  Low-ESR Elko 1000 µF    │
     │  (Überspannungs-Clamp)  │      │  35V (Brownout-Puffer)   │
     └─┬───────────────────────┘      └─┬────────────────────────┘
       │                                │
       ├────────────────────────────────┘
       │
     ┌─┴────────────────────────┐
     │  DC-DC Step-Down (35V+)  │ (z. B. TI LM2596HV oder MP1584EN)
     │  IN: 7-36V -> OUT: 5.1V  │
     └─┬────────────────────────┘
       │
      [+] 5.1V / 3A DC (Sauber, gepuffert)
       │
     ┌─┴────────────────────────┐
     │  Raspberry Pi & Arduino  │
     └──────────────────────────┘
```

### 2.3 Bauteil-Spezifikation

| Komponente | Bauteil-Empfehlung | Funktion & Dimensionierung |
| :--- | :--- | :--- |
| **Sicherung** | KFZ-Minisicherung 2A | Schneller Leitungsschutz vor Kurzschluss / Kabelbrand |
| **Verpolschutz** | **1N5822** oder **SS34** (Schottky) | Verhindert Zerstörung bei Falschanschluss; geringer Spannungsabfall ($V_F \approx 0.35\,\text{V}$) |
| **TVS-Diode** | **SMBJ24A** (unidirektional, 24V/600W) | Schneidet Transienten und Spikes jenseits von 26,7 V blitzschnell ab (Reaktionszeit < 1 ps) |
| **Puffer-Kondensator** | **1000 µF bis 2200 µF, 35V Low-ESR** | Überbrückt 100–300 ms Spannungseinbrüche an der Ampel; verhindert Pi-Reboot |
| **Step-Down Wandler** | **MP1584EN** oder **LM2596HV** (High-Voltage) | Weitbereichseingang bis mindestens 36 V (niemals Standard-Wandler mit max. 16V/20V einsetzen!) |

> [!TIP]
> **5,1 V statt 5,0 V:** Stellen Sie den Step-Down-Regler auf exakt **5,15 V Leerlaufspannung** ein. Bei Volllast des Pi Zero 2 W (4 Kerne aktiv, Wi-Fi aktiv) fällt über Kabel und Micro-USB-Stecker etwa 0,1 V ab, sodass stabile 5,05 V am Pi anliegen und keine *Under-voltage detected*-Drosselung ausgelöst wird.

---

## 3. Drehzahlabgriff (RPM Signal Hardening)

### 3.1 Signalquelle & Gefahren
* **Ducati CDI (Blaues Kabel / Pickup):** Liefert ein Pickup-Signal mit ca. 2 bis 5 V Scheitelwert, jedoch überlagert von Hochspannungsimpulsen der Zündspule.
* **SIP Tacho Geber:** Liefert ein sauberes Rechtecksignal (meist 12V oder Open-Collector), das für den 5V-Arduino-Eingang gedämpft werden muss.
* **Kritischer Fehler:** Ein direkter Anschluss an Pin D2 (INT0) ohne Schutzbeschaltung führt zur Zerstörung der internen Schutzdioden des ATmega328P oder zu phantomartigen Drehzahlspitzen von 15.000+ U/min.

### 3.2 Schutz- und Konditionierungsschaltung

#### Option A: Passive Z-Dioden-Begrenzung & RC-Filter (Standard)
```
CDI-Pickup / Signal
       │
     ┌─┴─────────────┐
     │  10 kΩ (1/4W) │ (Strombegrenzung)
     └─┬─────────────┘
       │
       ├────────────────────────────────┐
       │                                │
     ┌─┴───────────────────────┐      ┌─┴────────────────────────┐
     │  Z-Diode 5.1V (BZX55C)  │      │  Keramik-Kondensator     │
     │  (Spannungsbegrenzung)  │      │  1 nF bis 2.2 nF (Filter)│
     └─┬───────────────────────┘      └─┬────────────────────────┘
       │                                │
      GND                              GND
       │
       └──────> Arduino Pin D2 (INT0) mit internem/externem Pull-Up
```

#### Option B: Galvanische Trennung via Optokoppler (Höchste Störfestigkeit)
Bei extremen Zündstörungen (z. B. Rennzündungen mit variabler Frühzündung) empfiehlt sich ein Optokoppler **PC817** oder **6N137**:
* Signal über 1 kΩ Vorwiderstand und antiparallele Schutzdiode (1N4148) in die LED des PC817.
* Kollektor des PC817 an Arduino D2 mit 10 kΩ Pull-Up auf +5V, Emitter an Arduino GND.
* **Ergebnis:** Vollständige galvanische Trennung — Hochspannungsüberschläge erreichen den Microcontroller nicht.

### 3.3 Firmware-Debounce & Vespa Ducati CDI Timing
* Die Vespa-Zündung zündet einmal pro Kurbelwellenumdrehung (1 PPR).
* In der Firmware (`firmware/src/main.cpp`) ist ein Lockout-Debounce von **1500 µs** hinterlegt:
  $$f_{\max} = \frac{1}{0.0015\,\text{s}} = 666.6\,\text{Hz} \implies \text{RPM}_{\max} = 40.000\,\text{U/min}$$
  Prellimpulse und Nachschwingungen der Zündspule (< 1500 µs nach dem Hauptfunken) werden im ISR hardwarenah ignoriert.

---

## 4. EGT (Abgastemperatur) & CHT (Zylinderkopftemperatur) mit MAX6675

### 4.1 Die Masseschleifen-Falle (*Grounded vs. Ungrounded*)
Das Standard-K-Typ-Thermoelement besteht aus zwei Drähten (Chromel / Alumel). 
* **Grounded Probe (Auf Gehäuse geschweißt):** Der Messpunkt ist galvanisch mit der Metallhülse und damit über die Auspuffverschraubung mit der **Motormasse** verbunden. Da über das Kurbelgehäuse der gesamte Zündrückstrom der CDI fließt, wird der MAX6675-Konverter mit starken Störspannungen geflutet. Die Temperaturanzeige springt wild oder friert auf 1024 °C ein.
* **MANDATORY:** Verwenden Sie **ausschließlich ungrounded (potenzialfreie)** K-Typ-Thermoelemente (mineralisoliert, Inconel-Mantel). Der Messpunkt im Inneren ist durch Magnesiumoxid-Pulver vollständig vom Außengehäuse isoliert.

### 4.2 MAX6675 Entstörung & Filterung

```
Thermoelement K-Typ (Ungrounded)
  T+ ────────┬────────────────────────> MAX6675 T+
             │
           ┌─┴───────────────────┐
           │ 100 nF Keramik (X7R)│
           └─┬───────────────────┘
             │
  T- ────────┴────────────────────────> MAX6675 T-
                                           │
                                     MAX6675 GND
```

1. **100 nF Filterkondensator:** Löten Sie einen **100 nF SMD- oder Vielschicht-Keramikkondensator (X7R)** direkt über die Eingangsklemmen T+ und T- des MAX6675-Breakouts. Dies schließt hochfrequente Gleichtaktstörungen kurz.
2. **Kompensationsleitung:** Kürzen Sie das Thermoelementkabel niemals mit normalen Kupferkabeln! Jede Übergangsstelle zwischen Thermodraht und Kupfer erzeugt eine ungewollte Vergleichsstelle (Seebeck-Effekt), die die Temperatur um zig Grad verfälscht.
3. **SPI-Leitungen (SCK, CS, SO):** Halten Sie die Leitungen zwischen Arduino und MAX6675 kürzer als 15 cm. Bei längeren Strecken setzen Sie 4,7 kΩ Pull-Up-Widerstände an CS und SCK.

---

## 5. Breitband-Lambda (AFR) & Sternmasse-Architektur

### 5.1 Das Heizstrom-Problem (1.5 A Ground Offset)
Der Controller einer Breitbandsonde (z. B. Bosch LSU 4.2 / 4.9 über KOSO-, 14Point7- oder AEM-Controller) hat einen integrierten Heizelement-Treiber, der bis zu **1,5 A bis 2,0 A PWM-Strom** zieht.

Fließt dieser Heizstrom über ein gemeinsames Massekabel mit dem Arduino zurück, bewirkt ein Leitungswiderstand von lediglich **0,1 Ω**:
$$\Delta U = R \cdot I = 0.1\,\Omega \cdot 1.5\,\text{A} = 0.15\,\text{V}$$
Bei einer analogen 0–5 V Kennlinie (wobei 0 V = AFR 10 und 5 V = AFR 20 bedeutet) entspricht eine Verschiebung von 0,15 V einem massiven Messfehler von **0,3 AFR Punkten**! Der Motor scheint scheinbar abzumagern, sobald die Heizung taktet.

### 5.2 Sternmasse-Schaltplan (*Star Grounding*)

```
                         [Batterie / 12V Masse]
                                    │
                  ┌─────────────────┴─────────────────┐
                  │                                   │
           (Dicke Zuleitung)                   (Separate Zuleitung)
                  │                                   │
                  ▼                                   ▼
        [Lambda-Heizermasse]                 [Zentraler Sternpunkt (GND)]
        (Controller Pin GND-Power)                    │
                                            ┌─────────┴─────────┐
                                            │                   │
                                            ▼                   ▼
                                      [Arduino GND]       [Lambda Sensor-GND]
                                            │             (Signal Ground)
                                            │                   │
                                            └───[100 nF]────────┤
                                                  │             │
                                                Pin A0 <────────┘ (Analog 0-5V)
```

1. **Signal Ground vs. Power Ground:** Trennen Sie strikt die Stromversorgungsmasse des Controllers von der Signalmasse.
2. **Tiefpassfilter an Arduino A0:** Schalten Sie einen **100 nF Keramikkondensator** zwischen Pin A0 und Arduino GND. In Kombination mit dem Innenwiderstand des Analogausgangs bildet dies einen Rauschfilter gegen PWM-Schaltfrequenzen.

---

## 6. GPS-Modul (Waveshare L76K)

### 6.1 Antennenplatzierung
* Das Vespa-Chassis besteht aus dickem Tiefzieh-Stahlblech. Das Chassis wirkt wie ein **Faradayscher Käfig**.
* Eine Montage der Keramik-Patchantenne unter der Seitenhaube oder im Metall-Handschuhfach führt zu massivem Signalverlust (HDOP > 4.0, häufiger Satelliten-Abriss).
* **Ideale Positionen:**
  1. Unter der Kunststoff-Kaskade (vorne am Beinschild, oberhalb der Hupe).
  2. Im Rücklichtgehäuse / unter der Sitzbank auf einem Kunststoffträger.
  3. Oberseite des Handschuhfach-Deckels (falls aus Kunststoff) oder extern montiert.

### 6.2 Vibrationssicherung der HF-Verbindung
* Der u.FL- bzw. IPEX-Mikrostecker auf dem GPS-Board ist extrem stoßempfindlich und rastet mit nur geringer Haltekraft ein.
* **Sicherungsmaßnahme:** Sichern Sie den aufgesteckten u.FL-Stecker mit einem Tropfen **nicht-korrosivem Elektronik-Silikon ( neutral vernetzend)** oder elastischem **Fixierkleber (B-7000 / E6000)**.
* Das Antennenkabel muss unmittelbar hinter dem Stecker mit einer Zugentlastung fixiert werden, um Schwingungsbrüche der feinen Koaxialabschirmung zu verhindern.

---

## 7. Mechanische Entkopplung & Packaging

### 7.1 Gehäuse & Schwingungsdämpfung
* **Gehäuse-Schutzklasse:** Verwenden Sie ein Gehäuse nach mindestens **IP65** (staubdicht und strahlwassergeschützt) aus robustem Polycarbonat (PC) oder ABS mit Neopren-Dichtung.
* **Silentblöcke:** Verschrauben Sie das Elektronikgehäuse niemals starr mit dem Rahmen oder der Seitenhaube. Verwenden Sie **M4/M5 Gummimetallpuffer (Silentblöcke)** in Schorehärte 45–55 ShA. Sie absorbieren die hochfrequenten Vibrationen des Kurbeltriebs.

```
       [Vespa-Rahmen / Karosserieblech]
                     │
          ═══════════╧═══════════
             [Gummipuffer / Silentblock]
          ═══════════╤═══════════
                     │
       [StreetDyno IP65-Gehäuse]
```

### 7.2 Kabelbäume & Steckverbinder
* **Niemals starre Adern verwenden:** Ausschließlich feindrähtige KFZ-Fahrzeugleitungen (**FLRY-B**) verwenden.
* **Zugentlastung:** Alle Kabel müssen mit PG-Verschraubungen (Kabelverschraubungen mit Knickschutz) ins Gehäuse geführt werden.
* **Kabelbaum-Schutz:** Wickeln Sie alle Stränge mit hochwertigem **PET-Vliesband (z. B. Certoplast oder Coroplast KFZ-Gewebeband)** ein. Dies verhindert Scheuerstellen an Blechkanten.
* **Steckverbinder:** Verwenden Sie verriegelbare, wasserdichte Steckverbinder (z. B. **Superseal 1.5** oder **Deutsch DT-Serie**). Niemals offene Flachsteckhülsen ohne Rastung einsetzen.

---

## 8. Checkliste zur Inbetriebnahme

Vor der ersten Testfahrt am Fahrzeug folgende Prüfpunkte abarbeiten:

- [ ] **Bordnetz:** Mit Multimeter prüfen, ob am Eingang des Step-Down-Reglers im Leerlauf mindestens 9 V und bei Vollgas maximal 16 V anliegen.
- [ ] **Ausgangsspannung:** Am 5V-Ausgang des Step-Downs exakt 5,10 V bis 5,15 V unter Last gemessen.
- [ ] **EGT-Sensor:** Durchgangsprüfung mit Multimeter: Zwischen den beiden Sensorleitungen (T+/T-) und dem Auspuffrohr darf **kein Durchgang (Widerstand = $\infty$)** messbar sein.
- [ ] **Drehzahlsignal:** Signal an Pin D2 prüfen; im Standgas saubere, jitterfreie Anzeige im Cockpit (ca. 1.200–1.400 U/min ohne Spitzen auf 10.000 U/min).
- [ ] **Lambda-Masse:** Spannungsabfall zwischen Arduino GND und Controller Sensor-GND bei laufendem Motor und aktiver Sondenheizung messen ($\Delta U < 5\,\text{mV}$).
- [ ] **Mechanik:** Alle Schrauben mit Loctite 243 (mittelfest) gesichert; u.FL-Stecker am GPS elastisch fixiert.
