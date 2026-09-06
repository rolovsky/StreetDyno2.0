# 📐 StreetDyno 2.0 – Clean Code Directive & Architektur-Richtlinien

Dieses Dokument definiert die verbindlichen Architektur-, Physik-, Hardware- und Programmier-Standards für das Projekt **StreetDyno 2.0**. Alle künftigen Erweiterungen, Refactorings und Code-Generierungen müssen diesen Regeln ausnahmslos folgen.

---

## 1. 🏗️ Architektur & Datenfluss-Trennung

### 1.1 Strikte Trennung von Logging und Visualisierung
* **Logging & Rohdatenstrom (Backend / Hardware)**:
  * Sensorwerte von Arduino (RPM, AFR, EGT) und GPS (Speed, Alt, Lat, Lon) werden mit voller mathematischer Präzision (**ungedämpfte Floats**, keine Rundung/Quantisierung) verarbeitet und in die CSV-Dateien geschrieben.
  * Das Backend (`src/hw/hardware_service.py` und `src/data/logger.py`) liefert unverfälschte 10-Hz-Rohdaten, damit physikalische Ableitungen ($d\text{RPM}/dt$) und der Jetting Advisor auf wissenschaftlicher Datenbasis arbeiten können.
* **Cockpit-Visualisierung (Frontend / Live-HUD)**:
  * Jegliche Glättung, Dämpfung und Quantisierung findet **ausschließlich im Browser** in der 60-FPS `requestAnimationFrame`-Renderschleife statt.
  * **Drehzahl (RPM)**: PT1-Dämpfung ($\tau \approx 200\text{ ms}$, $\alpha = 0{,}20$) mit **10er-Raster Quantisierung** (`Math.round(rpm / 10) * 10`) gegen Ziffernflackern bei Vibrationen.
  * **Lambda (AFR)**: PT1-Tiefpassfilter ($\tau \approx 350\text{ ms}$, $\alpha = 0{,}14$) und Formatierung auf **genau 1 Nachkommastelle** (`displayAfr.toFixed(1)`).
  * **Drehzahlbalken**: Kontinuierlicher Float-Sweep mit 60 FPS für ein analoges Zeigergefühl.

---

## 2. 🎯 Single Source of Truth (SSOT)

* **Konfigurations-Zentralisierung in [`src/config.py`](file:///Users/rolandbachmann/Library/Mobile%20Documents/com~apple~CloudDocs/StreetDyno/pi-status-quo/streetdyno2.0/src/config.py)**:
  * Sämtliche fahrzeugspezifischen Konstanten liegen exklusiv in `src/config.py`.
  * **Physik-Konstanten**:
    * `TOTAL_MASS_KG = 190.0` (112 kg Vespa + 78 kg Fahrer)
    * `ROTATIONAL_MASS_FACTOR = 1.05` (Trägheitsfaktor für rotierende Massen)
    * `TIRE_CIRCUMFERENCE_M = 1.350` (100/90-10 Abrollumfang)
    * `PRIMARY_RATIO = 68.0 / 23.0` (2.9565)
    * `GEAR_RATIOS = {1: 58/12, 2: 42/13, 3: 38/17, 4: 35/21}`
    * `CW_A = 0.50`, `CR = 0.015`, `AIR_DENSITY = 1.205`, `TRANSMISSION_EFFICIENCY = 0.90`
  * **Stöchiometrie**:
    * `Super_E5: 14.30`, `Super_E10: 14.10`, `SuperPlus_E0: 14.70`
    * Optimales Ziel-Lambda unter Resonanz-Volllast: $\lambda = 0{,}86\text{--}0{,}90$.
* **Keine Hardcoded-Duplikate**: Weder in Templates noch in Analyse-Modulen dürfen Getriebeübersetzungen oder Massenwerte hardcodiert werden.

---

## 3. 🏁 Prüfstands- & Dyno-Mathematik (Ammerschläger-P4 Standard)

1. **Drehmoment-Formel & Schnittpunkt**:
   $$\text{Nm} = \frac{\text{PS} \cdot 7023{,}5}{\text{RPM}}$$
   * Bei $7.023{,}5\text{ U/min}$ schneiden sich die PS- und Nm-Kurven mathematisch zwingend.
2. **Ammerschläger-P4 Achsenskalierung**:
   $$\text{Y2}_{\max}\text{ (Nm)} = \text{Y1}_{\max}\text{ (PS)} \times 2{,}5$$
   * Sowohl in Matplotlib (`plot_telemetry`) als auch in Chart.js (`dyno_sheet.html`) wird die rechte Drehmomentachse dynamisch auf das 2,5-fache der linken Leistungsachse skaliert.
   * Dadurch liegt die Drehmomentkurve optisch harmonisch als solides Fundament unter der PS-Kurve.
3. **Adaptive 2-Stufen-Glättung für kurze Züge ($N < 50$ Datenpunkte)**:
   * **Stufe 1**: 3-Punkt zentrierter Gleitmittelwert (`rolling(3, center=True).mean()`) zur Unterdrückung von Zündaussetzern / 2-Takt-Viertakten.
   * **Stufe 2**: Savitzky-Golay Filter 2. Ordnung (`window_length = min(17..21, n)`).
4. **P4 Resonanzbogen & Dellen-Harmonisierung**:
   * Gemischbedingte Zwischentäler ($< 2{,}5\text{ PS}$ über $< 400\text{ RPM}$) werden per Savitzky-Golay Envelope harmonisiert, um einen stetigen Resonanzverlauf abzubilden.
5. **Physikalisches Clamping**:
   * Drehbeschleunigung im 3. Gang: $\text{dRPM}/dt \le 1.800\text{ RPM/s}$.
   * Lineare Fahrzeugbeschleunigung: $a \le 4{,}2\text{ m/s}^2$ ($\approx 0{,}43\text{ g}$).
   * Automatische GPS-Steigungskompensation: $\max \pm 2{,}5\%$ (filtert GPS-Höhensprünge).

---

## 4. ⚡ Sensorik & Hardware-Schutz

1. **MAX6675 Thermoelement (EGT)**:
   * **Kaltstart / Aufwärmphase**: Werte bis $50\,^\circ\text{C}$ klettern frei ohne Filterung.
   * **Betriebsbereich ($> 50\,^\circ\text{C}$)**:
     * Discard von typischen SPI-Open-Circuit Werten ($701\,^\circ\text{C}$ und $705\,^\circ\text{C}$).
     * Hard-Jumps von $|\text{EGT}_i - \text{EGT}_{i-1}| > 50\,^\circ\text{C}$ pro Zeitschritt werden verworfen (Hold-Last-Valid).
2. **KOSO Breitband-Lambda Kennlinie (Bosch LSU 4.2 Kit / 5001AFJ0)**:
   * **Offizielle 0–5V Kennlinie**: $0{,}0\text{ V} = 10{,}0\text{ AFR}$ (maximal fett), $5{,}0\text{ V} = 20{,}0\text{ AFR}$ (maximal mager / Free Air).
   * **Lineare Berechnungsformel**:
     $$\text{AFR} = (2{,}0 \cdot V_{\text{A0}}) + 10{,}0$$
   * **Bandgap-Kompensation**: Dynamische Erfassung der Betriebsspannung via interner $1{,}1\text{V}$ Bandgap-Referenz (`readVccMillivolts()`), um ADC-Drift bei schwankender Bordspannung auszugleichen.
   * **Zonen-Schwellenwerte (Super E5)**:
     * **Vollgas-WOT-Sicherheitsbereich**: $12{,}2\text{--}12{,}8\text{ AFR}$ (optimal $\approx 12{,}5$, $\lambda \approx 0{,}86$).
     * **Magerwarnung unter Last**: $\text{AFR} > 13{,}5$ (ab $13{,}8$ roter Cockpit-Alarm & Klemmgefahr).
     * **Teillast-Überfettung**: $\text{AFR} < 12{,}0$ bei $1/8\text{--}1/4$ Schieberöffnung $\rightarrow$ ND abmagern / größerer Schieber-Cutaway.
3. **WOT Auto-Trigger (3. Gang)**:
   * Mindestgeschwindigkeit $v > 15{,}0\text{ km/h}$.
   * Getriebe-Fenster: $60 \le \frac{\text{RPM}}{\text{Speed}} \le 110$ (nominal 81.6 RPM/(km/h)).
   * Drop-Filter: Vorzeitige Gaswegnahme ($d\text{RPM}/dt \le -500\text{ RPM/s}$ bei $< 1.000\text{ RPM}$ Gain) verwirft Fehlstarts automatisch.
4. **Dell'Orto SI Nebendüsen-Mathematik (ND Quotient $Q = \text{Luft} / \text{Benzin}$)**:
   * Die ND-Bezeichnung lautet Benzin / Luft (z.B. `60/160` $\rightarrow$ Benzin 60, Luft 160).
   * Der physikalische Quotient ist $Q = \frac{\text{Luft}}{\text{Benzin}} = \frac{160}{60} = 2{,}67$.
   * **Physik**: Ein **höherer Quotient** (z.B. `55/160` = 2,91 oder `50/140` = 2,80) bedeutet MEHR LUFT auf weniger Benzin $\rightarrow$ **magerer**.
   * **Anfetten**: Um das Gemisch anzufetten, muss der Quotient **kleiner** werden (z.B. `55/140` = 2,55 $\rightarrow$ `50/120` = 2,40 $\rightarrow$ `55/120` = 2,18).
   * Formel: `is_richer_idle_jet(new, cur) = (new.air / new.fuel) < (cur.air / cur.fuel)`.

---

## 5. 🧼 Code-Hygiene & Repository-Standards

1. **Python PEP 8 & Typing**:
   * Vollständige Type-Hints (`from __future__ import annotations`, `typing.Dict`, `Optional`, `Tuple`).
   * Keine globalen ausführbaren Skripte ohne `if __name__ == '__main__':`.
2. **Pandas Best Practices**:
   * Keine deprecated Methoden wie `.fillna(method='bfill')` oder `.fillna(method='ffill')`.
   * Ausschließlich modernes `.bfill().ffill()` verwenden.
   * Array-Clipping auf NumPy-Arrays strikt mit `np.clip(a, a_min, a_max)` oder `np.maximum(a, 0.0)`.
3. **Ordnerstruktur & Sauberkeit**:
   * Keine temporären Plots, Test-Skripte oder `.csv`-Dateien im `src/`-Verzeichnis.
   * Logs liegen ausschließlich in `logs/`, Plots in `plots/`, Tests in `tests/`.
   * Unit-Tests in `tests/test_dyno_core.py` müssen nach jeder Änderung zu 100% grün durchlaufen.
