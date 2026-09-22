# HAEO Klima Forecast – Spezifikation

Dieses Dokument beschreibt das Projekt vollständig genug, um es bei Bedarf
komplett neu zu implementieren – auch mit einer anderen Architektur oder
einem anderen Framework als Home Assistant. Es trennt bewusst:

- **Fachliche Anforderungen**: WAS das System tut und WARUM, unabhängig von
  Home Assistant, Python oder der konkreten Umsetzung. Diese Anforderungen
  gelten unverändert, egal in welcher Umgebung das System läuft.
- **Technische Anforderungen**: WIE es aktuell umgesetzt ist – Home-Assistant-
  spezifische Mechanismen, APIs und Entscheidungen, die sich bei einem
  Plattform-/Framework-Wechsel ändern würden.

Stand: Version 0.1.0 (siehe [CHANGELOG.md](CHANGELOG.md)).

---

## 1. Fachliche Anforderungen

### 1.1 Zweck

Für ein Klimaanlagen-System (ein Außengerät, ggf. mehrere Innengeräte,
Heizen und/oder Kühlen) soll die zukünftige elektrische Leistungsaufnahme
stundenweise vorhergesagt werden – als Eingabe für eine Energie-Optimierung
(z. B. HAEO) oder eigene Automatisierungen. Die Vorhersage soll sich
automatisch aus historischen Verbrauchs-, Innentemperatur- und Wetterdaten
"lernen" (Energiesignatur-/Freiheitsgradstunden-Ansatz, wie er auch bei der
Gebäudeenergie-Kennlinienanalyse nach ASHRAE/IPMVP verwendet wird) und dabei
mit **möglichst geringem Konfigurationsaufwand** gute Prognosen liefern –
der Nutzer soll insbesondere nicht jedes Innengerät, jede Sonderanpassung
(Nachtabsenkung, Taktverlängerung) oder sonstige interne Regelmechanismen
einzeln konfigurieren oder modellieren müssen (s. 1.2).

### 1.2 Fachliches Domänenmodell

**Klimasystem** (das zentrale fachliche Objekt): besteht physisch aus einem
Außengerät und einem oder mehreren Innengeräten – die Leistung wird dabei
ohnehin nur systemweit am Außengerät gemessen, nie je Innengerät (s. 1.5).
Für dieses System sind **drei Datenquellen je Klimasystem** ausreichend und
nötig, jede einzelne Innengeräte-Konfiguration entfällt bewusst:

1. ein historisierter **Leistungssensor** (Gesamtsystem) – Zielgröße des
   Trainings,
2. eine **Wetterquelle mit Vorhersage** – Außentemperatur (Pflicht),
   optional Globalstrahlung, Windgeschwindigkeit, Luftfeuchtigkeit,
3. eine historisierte **Innentemperatur** – wahlweise über ein
   eigenständiges Thermometer oder das Ist-Temperatur-Attribut einer
   beliebigen Klima-Entität des Systems; repräsentativ für das
   Gesamtsystem, ohne Anspruch, jeden Raum einzeln zu erfassen.

Mehrere unabhängige Klimasysteme müssen parallel betrieben werden können
(z. B. mehrere Gebäude/Wohnungen), jedes mit eigener Konfiguration, eigenem
Training und eigenen Vorhersagen – keine gegenseitige Beeinflussung.

**Wetterdaten** pro Stunde:
- Außentemperatur (°C, Pflichtfeld)
- Globalstrahlung (W/m², optional)
- Windgeschwindigkeit (m/s, optional)
- Windrichtung (°, optional) – neu, sofern sich ein Einfluss zeigt (s. 1.3:
  als Rundungsgröße braucht sie eine gesonderte Kodierung, keine einfache
  lineare Eingangsgröße)
- Luftfeuchtigkeit (%, optional) – anders als in früheren Entwurfsständen
  dieses Dokuments eine tatsächlich **aktiv nutzbare** Eingangsgröße (s. 1.3).

Alle vier optionalen Größen werden **automatisch** verwendet, wenn sie in
der Vorhersagequelle vorhanden sind – **kein** gesondertes
Konfigurationsfeld pro Größe nötig (s. 1.5, 1.7).

**Zwei getrennte Wetter-Datenpfade** (Entscheidung, s. 1.7 für die
Begründung):
- **Vorhersage**: kommt nicht direkt von einem Wetterdienst, sondern von
  einer vom Nutzer konfigurierten, bereits vorhandenen HA-Entität in einem
  standardisierten Forecast-Template-Format – bestätigtes Schema:
  ```yaml
  state: 5.3   # aktuelle Außentemperatur (°C)
  attributes:
    forecast:
      - time: "2026-09-23T14:00:00+00:00"
        value: 6.1        # Außentemperatur (°C), Pflicht
        humidity: 72      # %, optional
        radiation: 210    # W/m², optional
        wind_speed: 3.4   # m/s, optional
        wind_direction: 180  # °, optional
      - ...
  ```
  Die vier optionalen Felder werden **nur berücksichtigt, wenn im jeweiligen
  Forecast-Eintrag tatsächlich vorhanden** – nicht per separatem
  An/Aus-Konfigurationsfeld gesteuert (s. o.). Die Umwandlung eines
  konkreten Wetterdienstes (Open-Meteo, DWD, …) in dieses Format übernimmt
  ein separates, provider-spezifisches **Mapper-Helper-Projekt** – nicht
  Teil dieses Systems.
- **Historie** (fürs Training): wird von diesem System weiterhin direkt
  abgerufen, aber fest über die **Open-Meteo Historical Weather API**
  anhand der konfigurierten Koordinaten – kein pluggable
  Mehr-Anbieter-Mechanismus mehr nötig, da nur noch ein Anbieter für die
  Historie verwendet wird. Ein optionales Feature (z. B. `humidity`) wird
  fürs Training nur dann verwendet, wenn es **sowohl** in der Historie
  **als auch** in der Vorhersagequelle vorliegt – sonst gäbe es trainierte
  Gewichte für eine Größe, die zur Vorhersagezeit gar nicht zur Verfügung
  steht.

**Physikalisches Modell (Energiesignatur/Freiheitsgradstunden):** Der
Wärmebedarf eines Gebäudes lässt sich näherungsweise als
`Dämmwert × (Innentemperatur − Außentemperatur)` beschreiben. Übertragen auf
die Regression: die **Außentemperatur** ist die treibende Eingangsgröße, die
**Innentemperatur** bestimmt den Bezugspunkt (ab welcher Temperaturdifferenz
überhaupt geheizt/gekühlt werden muss), und der **Dämmwert** ergibt sich als
gelernte Steigung des Regressionsmodells – er muss nicht konfiguriert oder
bekannt sein. Da das System sowohl heizen als auch kühlen kann, ergibt sich
grafisch eine asymmetrische U-/Badewannenkurve: ein flacher Mittelteil
(Grundlast, wenn weder Heiz- noch Kühlbedarf besteht) und zwei
unterschiedlich steile Äste für Heizen und Kühlen (unterschiedliche
Balance-Points und Steigungen, u. a. weil Solareinstrahlung und interne
Gewinne bei Heizen und Kühlen entgegengesetzt wirken, und weil sich der
COP von Wärmepumpen bei Kälteextremen anders verhält als bei Hitze).
Reine Temperatur ist dabei erfahrungsgemäß der mit Abstand dominante
Faktor (typischerweise > 80–90 % erklärte Varianz allein durch die
Außentemperatur); Globalstrahlung, Wind und Luftfeuchtigkeit liefern in
dieser Reihenfolge abnehmend zusätzliche, aber sekundäre Erklärungskraft.

**Sonderanpassungen des Sollwerts** (Nachtabsenkung, Taktverlängerung –
bestehende, extern gesteuerte Mechanismen, die dieses System nicht selbst
steuert): Da das System nicht den Sollwert, sondern die **tatsächlich
gemessene Innentemperatur** beobachtet, wirken beide Mechanismen automatisch
und **ohne jede eigene Konfiguration** in die Freiheitsgradstunden hinein –
ihre Wirkung zeigt sich unmittelbar als niedrigere (Nachtabsenkung im
Heizfall) bzw. höhere (Taktverlängerung) gemessene Innentemperatur. Eine
gesonderte Erfassung, Konfiguration oder Modellierung dieser beiden
Mechanismen ist damit nicht mehr nötig (frühere Entwurfsstände dieser
Spezifikation sahen dafür noch eigene Konfigurationsfelder und
Regressions-Features vor – s. 1.9 zur Historie dieser Entscheidung).

**Für die Vorhersage** gibt es dabei keinen Verlauf, auf dem man aufbauen
könnte – nur die Wettervorhersage und die aktuelle bzw. historische
Innentemperatur. **Entscheidung:** Für jede Vorhersagestunde wird die
tatsächlich gemessene Innentemperatur von vor 24 Stunden übernommen
(zyklisch: vom entsprechenden Zeitpunkt des Vortages, auch über einen
mehrtägigen Horizont hinweg). Das bildet täglich wiederkehrende Muster
(insbesondere die Nachtabsenkung) automatisch ab, ohne dass eine der
zugrunde liegenden Steuerungslogiken (zeit-, temperatur- oder lastgeführt)
nachgebildet werden müsste. Nicht täglich wiederkehrende, situative Effekte
(z. B. eine Taktverlängerung ausgelöst durch eine an diesem Tag ungewöhnlich
hohe Last) werden dadurch nicht erfasst. Da sowohl Nachtabsenkung als auch
Taktverlängerung den Verbrauch ausschließlich *senken*, führt dieses
Nicht-Erfassen den Vorhersagewert bestenfalls zu **hoch**, nie zu
**niedrig** aus – ein bewusst akzeptierter, konservativer Fehler in
unkritische Richtung. Diese Entscheidung gilt, solange keine verlässlichere
Vorhersagegrundlage für situative Lastanpassungen existiert, und ist bei
Bedarf zu revidieren.

### 1.3 Fachlicher Kernprozess: Training (Gewichte berechnen)

Ziel: aus der Vergangenheit lernen, wie stark welche Einflussgröße die
Leistungsaufnahme bestimmt.

1. Ein konfigurierbarer Trainingszeitraum (Standard 365 Tage, Minimum 14,
   Maximum 730 Tage) wird stundenweise betrachtet.
2. Für jede Stunde im Zeitraum werden folgende Werte ermittelt:
   - **Ziel-/Zeilenwert**: die mittlere Leistungsaufnahme (kW) dieser
     Stunde aus dem historischen Leistungssensor.
   - **Eingangsgrößen** ("Features"), exakt in dieser fachlichen
     Bedeutung:
     1. **bias** – konstanter Term (Grundlast/Standby)
     2. **heating_degree_hours** – `max(0, Innentemperatur − Außentemperatur)`
        dieser Stunde
     3. **cooling_degree_hours** – analog fürs Kühlen:
        `max(0, Außentemperatur − Innentemperatur)`
     4. **shortwave_radiation** – Globalstrahlung der Stunde (nur wenn in
        Historie *und* Vorhersagequelle vorhanden, s. 1.2/1.7)
     5. **wind_speed** – Windgeschwindigkeit der Stunde (nur wenn verfügbar)
     6. **humidity** – Luftfeuchtigkeit der Stunde (nur wenn verfügbar)
     7. **wind_direction** – Windrichtung der Stunde (nur wenn verfügbar
        *und* sich ein Einfluss zeigt, s. 1.9). Anders als die übrigen
        Größen ist sie eine **zirkuläre** Größe (0–360°, 359° und 1° liegen
        praktisch nebeneinander) und kann nicht direkt linear als
        Eingangsgröße verwendet werden – sie muss vorher aufbereitet
        werden, z. B. durch Zerlegung in `sin(Windrichtung)`/
        `cos(Windrichtung)`-Komponenten oder durch eine feste Zuordnung zu
        Himmelsrichtungs-Sektoren (Windexposition der Gebäudeseite). Diese
        Aufbereitung ist bei einer Neuimplementierung explizit zu treffen.
   - Verwendet wird dabei die **tatsächlich gemessene Innentemperatur**
     (Thermometer oder Ist-Temperatur-Attribut einer Klima-Entität, s. 1.2),
     nicht ein Sollwert. Ob eine Stunde heiz- oder kühlseitig zu behandeln
     ist, ergibt sich rein aus dem Vorzeichen (Innentemperatur größer oder
     kleiner als Außentemperatur) – ein gesondertes Tracking von
     Betriebsmodus, Aktiv-Status oder Anzahl laufender Innengeräte ist damit
     nicht mehr nötig.
   - **Nachtabsenkung und Taktverlängerung sind bewusst keine eigenen
     Eingangsgrößen** (siehe Entscheidung in 1.2): Ihre Wirkung zeigt sich
     bereits unmittelbar in der gemessenen Innentemperatur und damit
     automatisch in den Freiheitsgradstunden. Separate Offset-Historien
     müssen für das Training nicht beschafft werden.
   - Stunden ohne vollständige Daten (fehlende Wetter-, Innentemperatur-
     oder Leistungsdaten) werden aus dem Training ausgeschlossen.
   - **Zu beachten**: Die meisten Eingangsgrößen liegen in der zugrunde
     liegenden Statistik nicht als Einzelwert, sondern je Stunde als
     Aggregat in Form von **Minimum, Mittelwert und Maximum** vor (typisch
     für Langzeitstatistiken numerischer Sensoren). Für jede Eingangsgröße
     muss daher bewusst festgelegt werden, welches dieser Aggregate
     fachlich sinnvoll ist – Standardfall ist der **Mittelwert** der Stunde
     (so wie es auch für den Ziel-/Zeilenwert der Leistungsaufnahme gilt);
     für einzelne Größen kann jedoch Minimum oder Maximum die fachlich
     richtigere Wahl sein (z. B. um kurzzeitige Spitzen oder das
     Nicht-Erreichen eines Schwellwerts innerhalb der Stunde zu erfassen).
     Diese Festlegung ist bei einer Neuimplementierung explizit zu treffen
     und zu dokumentieren, nicht implizit durch die Wahl einer API
     vorzugeben.
3. Aus den gesammelten Zeilen wird ein lineares Regressionsmodell
   geschätzt, das die konfigurierten Eingangsgrößen (mindestens drei: bias,
   heating_degree_hours, cooling_degree_hours; höchstens sieben/acht, wenn
   Globalstrahlung, Wind, Windrichtung – ggf. als zwei sin/cos-Spalten – und
   Luftfeuchtigkeit alle konfiguriert sind) auf die Leistungsaufnahme
   abbildet (siehe technischer Teil für das konkrete Verfahren). Ergebnis:
   ein Koeffizient (Gewicht) pro Eingangsgröße.
4. Qualitätsmaß: Bestimmtheitsmaß R² des Modells auf den Trainingsdaten.
5. Es müssen mindestens `Anzahl Features + 5` nutzbare Datenpunkte
   vorhanden sein, sonst schlägt das Training mit einer verständlichen,
   handlungsleitenden Fehlermeldung fehl (z. B. "zu wenige Datenpunkte,
   längeren Zeitraum wählen oder später erneut versuchen"). Das Training
   darf dabei die Konfiguration/Einrichtung des Systems selbst nicht
   verhindern – ein Klimasystem muss auch ohne vorhandenes Training
   eingerichtet werden können, es liefert dann lediglich noch keine
   Vorhersage.
6. Ergebnis (Gewichte, R², Anzahl Datenpunkte, Zeitpunkt der Berechnung)
   wird dauerhaft gespeichert und übersteht einen Neustart des Systems.

**Auslösung des Trainings** – drei gleichwertige fachliche Wege:
- manuell/on-demand für ein bestimmtes Klimasystem,
- manuell/on-demand für alle konfigurierten Klimasysteme gleichzeitig,
- automatisch nach einem wiederkehrenden Zeitplan (Standard: wöchentlich,
  sonntags 03:30 lokale Zeit; muss je Klimasystem einzeln ein-/ausschaltbar
  sein und den Schaltzustand über einen Neustart hinweg behalten).

Ein fehlgeschlagener automatischer Lauf darf den nächsten planmäßigen
Lauf nicht verhindern.

### 1.4 Fachlicher Kernprozess: Vorhersage

1. In konfigurierbarem Intervall (Standard alle 30 Minuten, 5–360 Minuten)
   wird eine neue Vorhersage berechnet.
2. Eingaben: aktuelle Wettervorhersage für den konfigurierten Horizont
   (Standard 72 Stunden, 6–168 Stunden, inkl. der konfigurierten optionalen
   Größen), die zuletzt berechneten Gewichte, sowie die tatsächlich
   gemessene Innentemperatur von vor 24 Stunden (Grundlage der
   Freiheitsgradstunden-Projektion, s. 1.2).
3. Für jede Vorhersagestunde wird nach demselben fachlichen Modell wie beim
   Training (Abschnitt 1.3, Formel: Summe der Gewichte × Feature-Werte)
   die erwartete Leistung berechnet:
   - Die Freiheitsgradstunden werden nicht aus der *aktuellen*, sondern aus
     der vor 24 Stunden tatsächlich gemessenen Innentemperatur gebildet
     (zyklisch: vom entsprechenden Zeitpunkt des Vortages, s. 1.2) – das
     bildet u. a. die Nachtabsenkung automatisch ab, ohne deren
     Steuerungslogik nachzubilden.
   - Die Taktverlängerung wird dadurch nicht gesondert projiziert (s. 1.2,
     Entscheidung): kein zusätzlicher Term, kein Versuch einer eigenen
     Schätzung ihres Effekts. Das führt bestenfalls zu einer leichten
     Überschätzung, nie zu einer Unterschätzung der vorhergesagten Leistung.
   - Eine vorhergesagte Leistung darf nie negativ sein (physikalisch
     unsinnig) – negative Rohwerte werden auf 0 begrenzt.
4. Ausgabe je Stunde: Zeitstempel, vorhergesagte Leistung (kW),
   Außentemperatur, Heiz-/Kühl-Freiheitsgradstunden (bereits auf Basis der
   24h-Innentemperatur, s. o.).
5. Der aktuelle Zustand (nächste Stunde) muss als Einzelwert abrufbar sein,
   die komplette Stundenreihe als strukturierte Liste.
6. Solange für ein Klimasystem noch keine Gewichte trainiert wurden, liefert
   die Vorhersage keinen Wert und macht das über eine klare, handlungs-
   leitende Fehlermeldung kenntlich (Verweis auf das Training).

### 1.5 Fachliche Konfigurationsanforderungen

Pro Klimasystem konfigurierbar – bewusst minimal, entsprechend dem Ziel aus
1.1 ("möglichst geringer Konfigurationsaufwand"):

| Feld | Pflicht | Bedeutung |
|---|---|---|
| Name | ja | Bezeichnung des Systems |
| Leistungssensor (kW) | ja | historisiert, Zielgröße des Trainings (Gesamtsystem – die Leistung wird ohnehin nur am Außengerät gemessen, s. 1.2) |
| Wetter-Vorhersage-Entität | ja | Referenz auf eine bestehende HA-Entität im Forecast-Template-Format (s. 1.7); der dahinterliegende Wetterdienst ist für dieses System irrelevant |
| Standort (Breite/Länge) | ja, Default = Systemstandort | nur noch für den direkten Open-Meteo-Historie-Abruf (s. 1.7), nicht mehr für die Vorhersage |
| Innentemperatur-Quelle | ja | ein Thermometer **oder** eine Klima-Entität (dann wird deren Ist-Temperatur-Attribut verwendet); repräsentativ fürs Gesamtsystem (s. 1.2) |
| Vorhersagehorizont (Stunden) | nein, Default 72 | 6–168 |
| Aktualisierungsintervall (Minuten) | nein, Default 30 | 5–360 |
| Trainingszeitraum (Tage) | nein, Default 365 | 14–730, zusätzlich begrenzt durch die Verfügbarkeit des Leistungssensors (s. 1.6) |

Bewusst **nicht mehr Teil der Konfiguration** (Historie dieser Entscheidung
in 1.9): eine Liste einzelner Innengeräte, ein separater Energiezähler,
ein Wetterdienst-Auswahlfeld, je ein An/Aus-Schalter pro optionaler
Wettergröße (Globalstrahlung/Wind/Windrichtung/Luftfeuchtigkeit – deren
Nutzung sich automatisch aus der Datenverfügbarkeit ergibt, s. 1.2), sowie
sämtliche Nachtabsenkungs-/Taktverlängerungs-spezifischen Felder
(Offset-Größe, Start/Ende, Lastsensor, Schwellwert, Maximal-Offset,
Schrittweite, Aktiv-Kennungen) – ihre Wirkung wird stattdessen automatisch
über die gemessene Innentemperatur erfasst (s. 1.2/1.3).

Die Konfiguration muss nachträglich änderbar sein (z. B. Wechsel der
Innentemperatur-Quelle oder der Vorhersage-Entität), ohne die bereits
aufgebaute Trainingshistorie zu verlieren, auch wenn das Klimasystem
komplett entfernt und neu angelegt wird (siehe 1.6).

Mehrsprachigkeit der Bedienoberfläche: mindestens Deutsch und Englisch.

### 1.6 Fachliche Anforderung an Datenhaltung/Historie

- Damit das Training auch über sehr lange Zeiträume (bis zu 2 Jahre)
  zuverlässig funktioniert, muss das System in der Lage sein, sich seine
  eigene Langzeit-Historie relevanter Werte selbst aufzubauen, statt sich
  ausschließlich auf die (oft kurze) Historie der zugrunde liegenden
  Sensoren/Zustände oder auf die Archiv-API des Wetterdienstes zu verlassen.
- Ein Wechsel/Neuanlegen der Konfiguration eines Klimasystems darf die
  bereits aufgebaute Historie der verwendeten Innentemperatur- und
  Leistungsquelle nicht verwerfen (Kontinuität der Trainingsdaten hat
  Vorrang vor der technischen Identität der Konfiguration).
- Fehlt eigene Historie für einen Zeitraum (z. B. weil das System dort noch
  nicht installiert war), muss ersatzweise auf die Rohhistorie der
  Quell-Sensoren bzw. die Archiv-API des Wetterdienstes zurückgegriffen
  werden.
- Historische Wetterdaten werden **nur für den Zeitraum abgerufen, den auch
  der Leistungssensor abdeckt** – es hat keinen Nutzen, weiter zurückliegende
  Wetterhistorie zu beschaffen, als ohnehin mangels Leistungsdaten ungenutzt
  bliebe (Effizienz-Anforderung, verhindert unnötige API-Last).

**Entscheidung (Stand dieses Dokuments): eigener Datenspeicher statt
Re-Fetch bei jedem Training.** Rohdaten werden zu Stundenwerten verdichtet
und **dauerhaft in einem eigenen, vom System selbst verwalteten
Datenspeicher** gehalten (nicht bei jeder Trainingsanforderung neu von
externen Quellen bzw. per Fein-Historie-Abfrage ermittelt). Begründung:
- Ein bei jedem Training komplett neu berechneter Abgleich mit externen
  APIs bzw. der Fein-Historie ist unnötig langsam und erzeugt wiederholt
  dieselbe Last (insbesondere gegenüber dem Wetterdienst).
- Die Befüllung erfolgt zweistufig: **einmaliger Rückwärts-Abgleich**
  ("Backfill") bei Einrichtung bzw. erstem Training – so weit zurück, wie
  Leistungssensor und Wetterhistorie es hergeben (nicht erst ab
  Einrichtungszeitpunkt organisch aufbauend) – gefolgt von einer
  **periodischen Aktualisierung** (z. B. täglich), die nur die seit dem
  letzten Abgleich neu hinzugekommenen Stunden ergänzt.
- Werte, die nur als kurzlebige Rohhistorie eines Zustands vorliegen (nicht
  als eigene Langzeitstatistik), müssen **eigenständig stündlich erfasst**
  werden, sobald das System eingerichtet ist (ein Wert pro Stunde, keine
  Aufzeichnung jeder Einzeländerung). Für den davor liegenden, nicht mehr
  auf diese Weise erfassbaren Zeitraum wird ersatzweise der Mittelwert der
  noch verfügbaren Rohhistorie dieses Zustands verwendet (typischerweise nur
  wenige Tage rückwirkend verfügbar) – darüber hinaus bleibt die Stunde ohne
  Wert und wird vom Training ausgeschlossen (s. 1.3).

### 1.7 Fachliche Anforderungen an Wetterdaten-Beschaffung

**Entscheidung (Stand dieses Dokuments):** Vorhersage und Historie werden
bewusst **getrennt** beschafft, mit unterschiedlichen Anforderungen:

- **Vorhersage**: Dieses System betreibt selbst **keine
  Wetterdienst-Anbindung** mehr. Es konsumiert stattdessen eine vom Nutzer
  konfigurierte, bereits in HA vorhandene Entität in einem standardisierten
  Forecast-Template-Format (State = aktueller Wert, Attribut `forecast` =
  Liste künftiger Stundenwerte mit Zeitstempel, Temperatur und optional
  Luftfeuchtigkeit/Globalstrahlung/Windgeschwindigkeit/Windrichtung – exaktes
  Schema s. 1.2). Welcher Wetterdienst dahinter
  steckt und wie dessen natives Format in dieses Schema überführt wird, ist
  **nicht mehr Aufgabe dieses Systems**, sondern eines separaten,
  provider-spezifischen "Mapper-Helper"-Projekts (je Wetterdienst eines,
  z. B. für Open-Meteo, DWD, …). Dadurch entfällt für dieses System die
  Anforderung, mehrere Wetterdienst-APIs selbst pluggable zu unterstützen –
  die Austauschbarkeit verlagert sich auf die Wahl des passenden
  Mapper-Helper-Projekts.
- **Historie** (fürs Training): Dieses System ruft historische Wetterdaten
  weiterhin **selbst und direkt** ab, aber fest über die **Open-Meteo
  Historical Weather API** (Reanalyse-Daten, weltweit per Koordinaten
  abrufbar, kostenlos, kein API-Key) – kein pluggabler
  Mehr-Anbieter-Mechanismus mehr nötig, da nur ein Anbieter für die Historie
  verwendet wird. Der abgefragte Zeitraum ist zusätzlich durch die
  Verfügbarkeit des Leistungssensors begrenzt (s. 1.6).
- Historische Wetterdaten sind bei diesem Dienst erst nach einigen Tagen
  Verzögerung verfügbar (Qualitätssicherung) – das System muss damit
  fachlich korrekt umgehen (kein Absturz, sinnvolle Einschränkung des
  abgefragten Zeitraums, nachvollziehbare Diagnosemeldung statt stillem
  Datenausfall).
- Beide Datenpfade müssen dieselbe fachliche Struktur pro Stunde liefern
  (siehe 1.2), damit Training (aus der Historie) und Vorhersage (aus dem
  Forecast-Template-Sensor) mit denselben Gewichten arbeiten können.

### 1.8 Fachliche Diagnose-/Transparenzanforderungen

Für jedes Klimasystem muss jederzeit einsehbar sein:
- die aktuell wirksamen Gewichte (pro Einflussgröße),
- die Modellgüte (R²) des letzten Trainings,
- Anzahl der genutzten Trainings-Datenpunkte,
- Zeitpunkt des letzten Trainings.

### 1.9 Bewusste fachliche Einschränkungen und Entscheidungshistorie

**Aktuelle bewusste Einschränkungen:**

- Windrichtung ist (Stand dieses Dokuments) ein **spekulatives** Feature –
  ob sie überhaupt einen messbaren, von der Windgeschwindigkeit
  unabhängigen Effekt hat, ist noch nicht validiert; sie sollte erst nach
  einer Auswertung an echten Trainingsdaten dauerhaft aufgenommen werden.
- Es wird nur **eine** aggregierte Innentemperatur je Klimasystem
  verwendet, nicht je Raum/Innengerät. Das ist eine bewusste Entscheidung
  zugunsten von 1.1 ("möglichst geringer Konfigurationsaufwand"), kein
  technisches Versehen – sie kostet die Fähigkeit, z. B. den Ausfall eines
  einzelnen Innengeräts oder raumweise unterschiedliches Verhalten zu
  erkennen.
- Das Modell ist stückweise linear (additiv, mit getrennter Steigung für
  Heizen/Kühlen, s. 1.2); nichtlineare Effekte an Temperaturextremen (z. B.
  COP-Einbruch/Abtauzyklen einer Wärmepumpe bei starkem Frost) werden nicht
  gesondert modelliert und könnten die Prognose dort systematisch verzerren.
- Situative, nicht täglich wiederkehrende Lastanpassungen (insbesondere
  eine last-/ereignisgetriebene Taktverlängerung) werden von der
  24h-Wiederholungs-Projektion nicht erfasst (s. 1.2) – die Vorhersage kann
  dadurch bestenfalls zu hoch, nie zu niedrig ausfallen.
- Ändert sich das Nutzungsverhalten dauerhaft (z. B. neue Heizgewohnheiten,
  andere Ziel-Innentemperatur), bildet die Vorhersage das erst nach einem
  erneuten Training korrekt ab, nicht in Echtzeit.

**Entscheidungshistorie (frühere Entwurfsstände dieser Spezifikation, zur
Nachvollziehbarkeit bewusst dokumentiert statt stillschweigend entfernt):**

- Frühere Fassungen sahen eine Konfiguration je Innengerät vor (Name +
  Climate-Entität, mit Sollwert-/Modus-/Aktiv-Tracking), einen separaten
  Energiezähler-Sensor sowie eigene Konfigurationsfelder und
  Regressions-Features für Nachtabsenkung und Taktverlängerung (Offset-
  Größe, Zeitfenster, Lastsensor, Schwellwert, Maximal-Offset,
  Schrittweite, Aktiv-Kennungen). All das ist mit dem Wechsel zum
  aggregierten Innentemperatur-/Energiesignatur-Ansatz (s. 1.2) entfallen:
  Die Wirkung von Sollwert, Nachtabsenkung und Taktverlängerung zeigt sich
  bereits vollständig in der gemessenen Innentemperatur; eine gesonderte
  Erfassung würde denselben Effekt nur noch einmal (und mit erheblichem
  Konfigurationsaufwand) abbilden.
- Luftfeuchtigkeit wurde in einem Zwischenstand erfasst, aber nicht als
  Einflussgröße genutzt – das ist behoben (s. 1.3): sie ist jetzt ein
  optionales, aktiv nutzbares Feature.
- In einem Zwischenstand sollte dieses System selbst mehrere
  Wetterdienst-APIs pluggable für Vorhersage *und* Historie unterstützen
  (`WeatherProvider`-Interface, s. 2.10). Das ist zugunsten der getrennten
  Datenpfade aus 1.7 aufgegeben worden: Die Vorhersage kommt jetzt von
  einer providerunabhängigen Forecast-Template-Entität (Umwandlung
  übernehmen externe Mapper-Helper-Projekte), die Historie fest von der
  Open-Meteo Historical Weather API. Dieses System selbst muss damit keine
  Wetterdienst-Vorhersage-API mehr direkt ansprechen.
- Ein Zwischenstand sah vor, Trainingsdaten je nach Bedarf frisch über
  Mirror-Sensoren (HA-Langzeitstatistik) bzw. per erneutem API-Abruf zu
  beschaffen (s. 2.6, Ist-Zustand). Das ist zugunsten eines eigenen
  HistoryStores mit einmaligem Backfill und täglicher Inkrementalaktuali-
  sierung aufgegeben worden (s. 1.6) – Begründung: Mirror-Sensoren bauen
  Historie nur ab ihrer Einrichtung auf (kein Backfill), schreiben bei
  jeder Attributänderung einen eigenen Datensatz statt nur des
  Stundenwerts, und Wetterhistorie wurde bei jedem Training erneut
  komplett gegen die externe API abgeglichen statt zwischengespeichert.
- Der aktuelle Code (Abschnitt 2) implementiert noch die ältere,
  feingranulare Variante (Innengeräte-Liste, Mirror-Sensoren für Setpoint/
  Modus, Nachtabsenkungs-/Taktverlängerungs-Konfiguration, acht statt bis
  zu sieben/acht Features, `WeatherProvider`-Abstraktion für beide
  Datenpfade statt der Aufteilung aus 1.7, Re-Fetch der Wetterhistorie pro
  Training statt eines eigenen HistoryStores) – siehe Abschnitt 3 (Mapping)
  für die Konsequenzen einer Neuimplementierung nach dieser Spezifikation.

---

## 2. Technische Anforderungen (aktuelle Umsetzung: Home-Assistant-Integration)

Dieser Abschnitt beschreibt, **wie** die fachlichen Anforderungen aus
Abschnitt 1 aktuell mit Home Assistant als Zielplattform umgesetzt sind.
Bei einem Wechsel der Architektur/des Frameworks ist dies der Teil, der neu
entworfen werden muss – die fachliche Logik selbst (Feature-Definitionen,
Regressionsansatz) kann übernommen werden.

> **Wichtiger Hinweis zum Stand dieses Abschnitts:** Der unten beschriebene
> Code implementiert noch das **ältere, feingranulare Design** (Konfiguration
> je Innengerät mit Setpoint-/Modus-Tracking, Mirror-Sensoren dafür,
> Nachtabsenkungs-/Taktverlängerungs-Konfiguration, acht statt bis zu sechs
> Regressions-Features). Abschnitt 1 beschreibt inzwischen den vereinfachten
> **Energiesignatur-Ansatz** (aggregierte Innentemperatur statt
> Innengeräte-Liste, s. 1.2/1.9). Eine Neuimplementierung nach dieser
> Spezifikation würde weite Teile von 2.3, 2.5, 2.6, 2.9 und 2.12
> entsprechend vereinfachen bzw. entfallen lassen – siehe Abschnitt 3 für
> die konkreten Konsequenzen. Die folgenden Unterabschnitte sind dennoch
> vollständig als **Ist-Zustand-Dokumentation des heutigen Codes**
> beibehalten, da sie weiterhin exakt beschreiben, wie die aktuelle
> Home-Assistant-Integration funktioniert.

### 2.1 Plattform & Paketierung

- **Home Assistant Custom Integration**, verteilt via HACS
  ([hacs.json](hacs.json)), `integration_type: hub`, `iot_class: calculated`.
- Verzeichnis `custom_components/haeo_klima_forecast/`, Manifest
  ([manifest.json](custom_components/haeo_klima_forecast/manifest.json)):
  Domain `haeo_klima_forecast`, Abhängigkeit `numpy>=1.26.0`.
- Ein Home-Assistant-**Config Entry pro Klimasystem** (fachliche
  Mehrsystem-Anforderung aus 1.2 wird durch das native
  Mehrfach-Instanzen-Konzept von HA abgebildet).
- Lokalisierung über HA-Standardmechanismus:
  [strings.json](custom_components/haeo_klima_forecast/strings.json) (Quelle)
  und `translations/{de,en}.json`.

### 2.2 Architektur-Übersicht (Module)

| Datei | Fachliche Zuordnung | Technische Rolle |
|---|---|---|
| `__init__.py` | Setup/Service-Registrierung | Config-Entry-Lifecycle, registriert den `recalculate_weights`-Service |
| `const.py` | Konfigurationsschlüssel, Defaults | zentrale Konstanten |
| `config_flow.py` | 1.5 Konfiguration | HA Config-/Options-Flow (mehrstufiger Assistent) |
| `coordinator.py` | 1.4 Vorhersage-Prozess | `DataUpdateCoordinator`, periodischer Poll |
| `weighting.py` | 1.3 Training | Datenbeschaffung aus Recorder/Statistics + Regression |
| `forecast.py` | 1.3/1.4 Modellformel | reine, HA-unabhängige Berechnungsfunktionen |
| `special_adjustments.py` | 1.2 Sonderanpassungen | Nachtabsenkung/Taktverlängerung als Funktionen |
| `mirror.py` | 1.6 Historie | Hilfsfunktionen für "Mirror-Sensoren" |
| `weather/` | 1.7 Wetteranbindung | Provider-Interface + `openmeteo.py`/`dwd.py` |
| `entity.py` | – | gemeinsame Basis-Entity-Klasse |
| `sensor.py` | 1.4/1.8/1.6 | Vorhersage-Sensor, Gewichte-Sensor, Mirror-Sensoren |
| `button.py` | 1.3 (manuelle Auslösung) | Button-Entity pro System |
| `switch.py` | 1.3 (automatische Auslösung) | Schalter-Entity mit internem Zeitplan |

Designprinzip (bewusst beibehalten bei einer Neuimplementierung): Die
fachliche Kernlogik (`forecast.py`, `special_adjustments.py`, Teile von
`weighting.py`) ist als **reine Funktionen ohne Framework-Abhängigkeit**
gehalten, um sie unabhängig testbar und portierbar zu halten. Nur die
Datenbeschaffung (Recorder-Zugriff, HTTP) und die Entity-Repräsentation
hängen an Home Assistant.

### 2.3 Konfigurations-UI

- `ConfigFlow` (Ersteinrichtung): Schritt "user" (allgemeine Einstellungen
  inkl. Anzahl Innengeräte) → N× Schritt "indoor_unit" (sequentiell, ein
  Formular je Innengerät) → `async_create_entry`.
- `OptionsFlow` (Nachträgliche Anpassung): Menü mit vier Zweigen (general,
  indoor_units, night_setback, duty_throttle); indoor_units erlaubt erneutes
  Setzen der Anzahl und Durchlaufen aller Innengeräte-Formulare mit
  vorbelegten Defaults aus der aktuellen Konfiguration.
- Eingabefelder nutzen HA-`selector`-Typen (`EntitySelector` mit
  Domain-/Device-Class-Filter, `SelectSelector`), damit im UI nur passende
  Entities wählbar sind (z. B. `climate.*` für Innengeräte, `sensor` mit
  `device_class: power` für den Leistungssensor).
- Bei Änderung der Optionen wird der Config Entry über einen
  `add_update_listener` automatisch neu geladen (`async_reload`).
- **Totes Feld:** `CONF_NIGHT_SETBACK_ACTIVE_ENTITY` (`night_setback_active_entity`)
  wird im Options-Flow abgefragt und gespeichert, aber weder in
  `coordinator.py` noch in `weighting.py` ausgewertet – siehe fachliche
  Anmerkung in 1.9. Bei einer Neuimplementierung entfällt es ersatzlos.

### 2.4 Vorhersage-Ausführung (`coordinator.py`)

- `HaeoForecastCoordinator(DataUpdateCoordinator)`, ein Coordinator pro
  Config Entry, `update_interval` aus der Konfiguration.
- `_async_update_data()`:
  1. lädt gespeicherte Gewichte (Cache oder `WeightStore`); fehlen sie,
     wird `UpdateFailed` geworfen (führt zu "unavailable"-Sensoren, blockiert
     aber **nicht** das Laden der übrigen Entities/Setup des Config Entry –
     bewusst mit `async_refresh()` statt
     `async_config_entry_first_refresh()` in `__init__.py`).
  2. holt Wettervorhersage vom konfigurierten Provider (frische
     `aiohttp.ClientSession` je Aufruf).
  3. der erste Wetterpunkt ("jetzt") wird zusätzlich als
     `latest_weather_point` gepuffert und von den Weather-Mirror-Sensoren
     angezeigt (Grundlage für 2.6).
  4. sammelt den aktuellen Zustand aller Innengeräte
     (`_collect_indoor_unit_plans`) direkt aus `hass.states`.
  5. ruft `compute_forecast_series()` (siehe `forecast.py`) auf und mappt
     das Ergebnis in die vom Sensor konsumierte Datenstruktur.
- `async_recalculate_weights()`: ruft `weighting.async_train_weights()` auf,
  persistiert das Ergebnis über `WeightStore`, aktualisiert den
  In-Memory-Cache und stößt `async_request_refresh()` an.

### 2.5 Training – Datenbeschaffung (`weighting.py`)

Technisch anspruchsvollster Teil, da Home-Assistant-interne, teils
undokumentierte APIs genutzt werden:

- `homeassistant.components.recorder.statistics.statistics_during_period`
  für stündliche Statistik von `sensor`-Entities mit `state_class`
  (Leistungssensor, Mirror-Sensoren). Die HA-Statistik liefert je Stunde
  grundsätzlich `min`/`mean`/`max`; die aktuelle Implementierung fragt
  ausschließlich `{"mean"}` ab und verwendet damit für **alle** Größen den
  Mittelwert der Stunde (siehe fachliche Anmerkung in 1.3 zur bewussten
  Aggregat-Wahl je Feature – eine Neuimplementierung sollte diese
  Entscheidung pro Feature explizit treffen, statt sie wie hier pauschal
  auf `mean` festzulegen). **Synchron**, daher zwingend über
  `get_instance(hass).async_add_executor_job(...)` aufgerufen (sonst
  Blocking-Call-Fehler im Event-Loop).
- `homeassistant.components.recorder.history.state_changes_during_period`
  für rohe Zustandshistorie (Fallback, sowie für Entities ohne
  Statistics wie `input_number`/`number` für Nachtabsenkung/Taktverlängerung).
  **Umsetzungsstand ggü. 1.2/1.3 (Ziel-Verhalten):** Der Code fragt hierüber
  weiterhin `CONF_NIGHT_SETBACK_OFFSET_ENTITY`/`CONF_DUTY_THROTTLE_OFFSET_ENTITY`
  ab und führt beide Werte als eigene Regressions-Features
  (`night_setback_offset`, `duty_throttle_offset`, s. `FEATURE_NAMES`) mit;
  laut 1.2/1.3 sind diese beiden Abfragen und Features für eine
  Neuimplementierung ersatzlos zu streichen, da ihre Wirkung bereits über
  den (bereits verschobenen) historischen Sollwert in den Freiheitsgrad-
  stunden enthalten ist.
- `_resample_last_value()`: bildet aus unregelmäßigen Zustandsänderungen
  eine "letzter bekannter Wert vor Stundenmarke"-Stufenfunktion je volle
  Stunde (`hour_marks`).
- Reihenfolge der Datenquellen je Feature: zuerst eigene
  Mirror-Statistics (siehe 2.6), für nicht abgedeckte Stunden Fallback auf
  Rohhistorie bzw. die Historie-API des Wetter-Providers.
- Fehlerbehandlung: `ValueError` mit sprechendem Text bei zu wenigen
  Datenpunkten (`len(FEATURE_NAMES) + 5`); wird in `button.py` in eine
  `HomeAssistantError` übersetzt, die im UI sichtbar ist.

### 2.6 "Mirror-Sensoren" (`mirror.py`) – heutiger Workaround, laut 1.6 abgelöst

> **Ziel-Design (ersetzt diesen gesamten Abschnitt, s. 1.6):** Der unten
> beschriebene Mirror-Sensor-Ansatz hat drei konkrete Nachteile, die zur
> Entscheidung in 1.6 geführt haben: (1) Historie existiert erst ab
> Einrichtung der Mirror-Entity, kein Backfill; (1.1) jede Attribut-
> änderung des Quellzustands erzeugt einen eigenen Datensatz in der
> `states`-Tabelle, nicht nur der gebrauchte Stundenwert; (2) für die
> Wetterhistorie wird bei jedem Training erneut die komplette Lücke gegen
> die externe Archiv-API abgeglichen (`weighting.py: _fetch_weather_by_hour`),
> ohne Zwischenspeicherung.
>
> Der Ziel-Ansatz ersetzt Mirror-Sensoren **und** den Re-Fetch bei jedem
> Training durch einen **eigenen HistoryStore** (z. B. eine SQLite-Datei
> über Python's `sqlite3`, im Executor ausgeführt wie die heutigen
> Recorder-Zugriffe, oder eine wachsende JSON-Struktur über den `Store`-
> Helper) mit einer Zeile/einem Eintrag pro Stunde und Klimasystem
> (`power_kw`, `indoor_temp`, `outdoor_temp`, `humidity`, `radiation`,
> `wind_speed`, `wind_direction`):
> - **Backfill** bei Einrichtung/erstem Training: ein `statistics_during_period`-
>   Aufruf über den ganzen verfügbaren Zeitraum für Leistungssensor (und
>   Innentemperatur, falls diese von einer Entität mit eigener
>   Langzeitstatistik kommt, z. B. einem echten Thermometer), plus ein
>   Open-Meteo-Archive-Aufruf über denselben Zeitraum.
> - **Tägliche Inkrementalaktualisierung** statt Re-Fetch pro Training: ein
>   einmal täglich laufender interner Job (technisch analog zum
>   `async_track_time_change`-Mechanismus des Wochen-Schalters, s. 2.11)
>   ergänzt nur die seit dem letzten Lauf neu hinzugekommenen Stunden.
> - **Innentemperatur von einer Climate-Entität** (kein eigenes
>   `state_class`, daher keine HA-Langzeitstatistik): Diese Größe wird vom
>   System **selbst stündlich abgetastet** und direkt in den HistoryStore
>   geschrieben (ein Eintrag/Stunde, kein Umweg über eine zusätzliche
>   HA-Entity und HAs Statistics-Engine). Für den Zeitraum vor Einrichtung,
>   für den keine eigene stündliche Abtastung existiert, wird ersatzweise
>   der Mittelwert der noch vorhandenen Rohhistorie
>   (`state_changes_during_period`, begrenzt durch `recorder.purge_keep_days`,
>   Standard 10 Tage) je Stunde gebildet; darüber hinaus bleibt die Stunde
>   ohne Wert.
> - Das Training (`weighting.py`) liest danach nur noch aus dem HistoryStore
>   – keine synchronen Recorder-Executor-Aufrufe und keine externen
>   API-Calls mehr zur Trainingszeit.
>
> Die folgende Beschreibung des heutigen Codes bleibt als
> Ist-Zustands-Dokumentation stehen.

Technischer Kernkniff, der **nur** wegen HA-Eigenheiten nötig ist:
`climate`- und `weather`-Entities bekommen in HA keine Langzeit-Statistik
(nur `sensor`-Entities mit `state_class` tun das); ihre Rohhistorie
unterliegt zudem `recorder.purge_keep_days` (typ. Tage, nicht Jahre).

Lösung: pro Innengerät und pro Klimasystem werden zusätzliche, für Nutzer
standardmäßig unsichtbare `sensor`-Entities (`entity_registry_visible_default
= False`, `entity_category: diagnostic`) angelegt, die einen numerischen
Wert aus dem Quellzustand "spiegeln" und `state_class: measurement` tragen,
damit HA dafür unbegrenzt Langzeitstatistik führt:

- **Climate-Mirrors** (`sensor.py: ClimateMirrorSensor`), event-getrieben via
  `async_track_state_change_event`: Setpoint, aktuelle Temperatur, aktiv
  (0/1), HVAC-Modus (kodiert: heat=1, cool=-1, sonst 0), HVAC-Aktion (analog
  kodiert). `unique_id` ist **bewusst nicht** an den Config-Entry gekoppelt,
  sondern nur an die Quell-`climate`-Entity-ID
  (`climate_mirror_unique_id()`), damit ein Löschen/Neuanlegen des Config
  Entries (neue, zufällige `entry_id`) nicht die aufgebaute Statistik verwaist
  (technische Umsetzung der fachlichen Anforderung 1.6).
- **Weather-Mirrors** (`WeatherMirrorSensor`), coordinator-getrieben:
  Außentemperatur, Globalstrahlung, Windgeschwindigkeit des jeweils
  zuletzt abgerufenen "jetzt"-Wetterpunkts. `unique_id` hier an die
  `entry_id` gekoppelt (ein Wettersatz gehört zu genau einem System).
- Da sich der `entity_id`-Suffix bei Namenskollisionen ändern kann, ist nur
  die `unique_id` stabil; die aktuelle `entity_id` wird bei Bedarf über die
  Entity-Registry aufgelöst (`resolve_entity_id()`).
- Encoding/Decoding der HVAC-Werte (`encode_hvac_mode`, `decode_hvac_mode`,
  …) existiert nur, damit sich String-Zustände als Zahl in der
  Statistik-Engine mitteln lassen.

### 2.7 Persistenz der Gewichte

- `WeightStore` kapselt `homeassistant.helpers.storage.Store`
  (`STORAGE_VERSION = 1`, Schlüssel `haeo_klima_forecast_weights_{entry_id}`)
  – HAs Standardmechanismus für kleine, JSON-serialisierbare
  Konfigurations-/Zustandsdaten außerhalb der Recorder-Datenbank.
- Gespeicherte Struktur: `coefficients` (dict), `r2`, `n_samples`,
  `trained_at` (ISO-Zeitstempel, UTC), `feature_names`.

### 2.8 Regressionsverfahren

- `numpy`-basierte Ridge-Regression (kleine Regularisierung
  `RIDGE_ALPHA = 1e-3` gegen Multikollinearität):
  `(XᵀX + αI)⁻¹Xᵀy` über `numpy.linalg.solve`.
- Feature-Matrix `X`: eine Zeile je nutzbarer Trainingsstunde, Spalten exakt
  in der Reihenfolge `FEATURE_NAMES` (siehe 1.3).
- R² klassisch über `1 − SS_res/SS_tot` (`SS_tot` gegen Division durch 0
  abgesichert).

### 2.9 Vorhersage-Berechnung (`forecast.py`)

- `compute_hour()` / `compute_forecast_series()`: reine Funktionen ohne
  HA-Bezug, nehmen `WeatherPoint`, `IndoorUnitPlan`-Liste, Koeffizienten-Dict
  und Nachtabsenkungs-Konfiguration entgegen.
- Rückwärtskompatibilität: falls ein gespeichertes Gewichte-Set noch das
  alte, undifferenzierte `degree_hours`-Feature statt der aufgeteilten
  `heating_/cooling_degree_hours` enthält, wird dieses als Fallback für
  beide neuen Felder verwendet (`legacy_degree_coeff`).
- **Umsetzungsstand ggü. 1.2/1.4 (Ziel-Verhalten):** aktuell projiziert
  `special_adjustments.projected_night_setback_offset()` die Nachtabsenkung
  weiterhin über das feste, konfigurierte Zeitfenster
  `CONF_NIGHT_SETBACK_START`/`CONF_NIGHT_SETBACK_END` und addiert den
  konfigurierten Offset-Betrag auf den *aktuellen* Sollwert (statt – wie
  laut 1.2/1.4 entschieden – direkt den vor 24 Stunden tatsächlich
  aufgezeichneten Sollwert zu verwenden); `projected_duty_throttle_offset()`
  nimmt weiterhin einen konstanten, optional konfigurierten Erfahrungswert
  an und `compute_hour()` verrechnet die beiden Offsets zusätzlich noch
  einmal separat mit eigenen Koeffizienten (`coefficients["night_setback_
  offset"]`, `coefficients["duty_throttle_offset"]`) – laut 1.2 sollte die
  Taktverlängerung dagegen für die Vorhersage komplett unberücksichtigt
  bleiben, und keiner der beiden Effekte sollte über ein eigenes Gewicht
  einfließen. Eine Neuimplementierung müsste daher `compute_hour()` so
  umbauen, dass die Freiheitsgradstunden direkt aus dem vor 24 Stunden
  aufgezeichneten Sollwert gebildet werden und die beiden Offset-
  Koeffizienten sowie deren Projektionsfunktionen entfallen.

### 2.10 Wetteranbindung (`weather/`)

- Abstrakte Basisklasse `WeatherProvider` (`base.py`) mit
  `async_get_forecast(hours)` und `async_get_historical(start, end)`,
  einheitliches `WeatherPoint`-Datenmodell.
- `openmeteo.py`: `api.open-meteo.com` (Vorhersage) /
  `archive-api.open-meteo.com` (Historie), kein API-Key. Archiv-Delay von
  5 Tagen wird aktiv berücksichtigt (Anfrage-Ende wird gekappt, bei
  vollständig unerreichbarem Zeitraum leere Liste + Warn-Log statt Fehler).
- `dwd.py`: nutzt Bright Sky (`api.brightsky.dev`) als offenen Wrapper um
  DWD-Open-Data (MOSMIX-Vorhersage + Stationsmessungen), da der DWD selbst
  keine einfache lat/lon-JSON-API anbietet. Beobachtungs-Delay von 1 Tag
  analog behandelt. Windgeschwindigkeit wird von km/h nach m/s umgerechnet,
  Strahlung von einer 10-Minuten-Summe (`solar_10`, J/cm²) in W/m²
  umgerechnet.
- Provider-Auswahl über `weather/__init__.py: get_provider(name, lat, lon,
  session)` (Factory), HTTP-Session je Coordinator-Aufruf frisch erzeugt und
  geschlossen (siehe Hinweis in `coordinator.py` zur potenziellen
  Umstellung auf `async_get_clientsession(hass)`).

### 2.11 Auslösung des Trainings (technische Umsetzung von 1.3)

- **Service** `haeo_klima_forecast.recalculate_weights`
  ([services.yaml](custom_components/haeo_klima_forecast/services.yaml)),
  optionales Feld `config_entry_id` (Selector-Typ `config_entry`); leer =
  alle registrierten Config Entries dieser Domain. Ungültige/veraltete
  Entry-ID liefert eine sprechende `HomeAssistantError`.
- **Button-Entity** `HaeoRecalculateWeightsButton`
  ([button.py](custom_components/haeo_klima_forecast/button.py)): ein
  Button je Config Entry, ruft direkt
  `coordinator.async_recalculate_weights()` auf; bleibt auch ohne
  vorhandene Gewichte drückbar; Trainingsfehler werden als
  `HomeAssistantError` sichtbar.
- **Switch-Entity** `HaeoAutoRecalculateSwitch`
  ([switch.py](custom_components/haeo_klima_forecast/switch.py)): statt
  eine echte HA-Automatisierung in die Nutzerkonfiguration zu schreiben
  (Nachteil: bliebe nach Deinstallation zurück, wäre außerhalb der
  Integrationskonfiguration), wird der wöchentliche Zeitplan **intern**
  über `homeassistant.helpers.event.async_track_time_change`
  (Stunde/Minute fix, Wochentag im Callback geprüft) verwaltet, solange der
  Schalter an ist. Zustand wird über `RestoreEntity`
  (`async_get_last_state()`) neustartfest gemacht. Ausnahmen im
  automatischen Lauf werden geloggt, verhindern aber nicht den nächsten
  Lauf (`try/except Exception` um den Trainingsaufruf).
- Konstanten für den Zeitplan zentral in
  [const.py](custom_components/haeo_klima_forecast/const.py)
  (`AUTO_RECALCULATE_WEEKDAY/HOUR/MINUTE`).

### 2.12 Entity-Modell (Übersicht)

| Entity | Plattform | unique_id-Schema | Sichtbarkeit |
|---|---|---|---|
| Vorhersage | `sensor` | `{entry_id}_forecast` | normal |
| Gewichte/Diagnose | `sensor` | `{entry_id}_weights` | diagnostic |
| Recalculate-Button | `button` | `{entry_id}_recalculate_weights` | normal |
| Weekly-Recalc-Switch | `switch` | `{entry_id}_auto_recalculate_weights` | config |
| Climate-Mirror (5×/Innengerät) | `sensor` | `haeo_klima_forecast_mirror_{climate_entity_id}_{suffix}` | hidden, diagnostic |
| Weather-Mirror (3×/System) | `sensor` | `{entry_id}_mirror_weather_{suffix}` | hidden, diagnostic |

Alle Entities eines Config Entries hängen an einem gemeinsamen HA-`Device`
(`DeviceInfo` mit `identifiers={(DOMAIN, entry_id)}`), realisiert über die
gemeinsame Basisklasse `HaeoBaseEntity`
([entity.py](custom_components/haeo_klima_forecast/entity.py)).

### 2.13 Tests

- `pytest-homeassistant-custom-component` (stellt u. a. den `hass`-Fixture,
  `MockConfigEntry`, `freezer`/Zeitreise, `async_fire_time_changed` bereit).
- [tests/conftest.py](tests/conftest.py): aktiviert das Laden der
  Custom-Integration in Tests.
- [tests/test_sensor_mirrors.py](tests/test_sensor_mirrors.py): End-to-End
  über echtes Config-Entry-Setup – prüft, dass Climate-/Weather-Mirror-
  Sensoren entstehen, mit der Quelle synchron bleiben, und dass ihre
  `unique_id` einen Config-Entry-Neuanlage überlebt (technische Umsetzung
  von 1.6).
- [tests/test_recalculate_entities.py](tests/test_recalculate_entities.py):
  Button löst Training aus und reicht Fehler als `HomeAssistantError`
  durch; Switch löst nur am konfigurierten Wochentag/Uhrzeit aus, nur
  solange er an ist, und sein Zustand wird per `RestoreEntity` wiederher-
  gestellt.
- [tests/test_mirror.py](tests/test_mirror.py): reine Unit-Tests der
  Kodier-/Namensfunktionen aus `mirror.py`.

### 2.14 Bekannte technische Risiken (bei Neuimplementierung gegen andere Plattform gegenstandslos, hier dokumentiert)

- `statistics_during_period` und `state_changes_during_period` sind interne,
  nicht öffentlich stabil garantierte HA-APIs; Signaturänderungen zwischen
  HA-Versionen sind möglich (README weist darauf hin).
- Bright Sky ist ein externer, nicht von DWD selbst betriebener Dienst.
- Frische `aiohttp.ClientSession` pro Coordinator-Zyklus statt HAs geteilter
  Session (`async_get_clientsession`) – funktional korrekt, aber nicht
  ressourcenschonend; als Verbesserung im Code vermerkt.

---

## 3. Mapping fachlich → technisch (Kurzreferenz für eine Neuimplementierung)

| Fachliche Anforderung | Heutige technische Lösung | Bei Framework-Wechsel zu ersetzen durch |
|---|---|---|
| Mehrere unabhängige Klimasysteme | HA Config Entries | eigene Mandanten-/Projekt-Verwaltung |
| Periodische Neuberechnung der Vorhersage | `DataUpdateCoordinator` + `update_interval` | Cronjob/Scheduler/Worker-Queue |
| Langzeit-Historie für Leistung/Innentemperatur/Wetter | Mirror-Sensoren + HA-Statistics (Ist-Zustand, s. 2.6) | eigener HistoryStore (Backfill + tägliches Inkrement, s. 1.6/2.6-Ziel-Design) – bei anderem Framework z. B. eigene Zeitreihen-Datenbank (InfluxDB/Timescale) |
| Persistenz der Gewichte | `Store`-Helper (JSON-Datei) | beliebiger Key-Value-/Dokumentenspeicher |
| Manuelle Trainings-Auslösung | Service + Button-Entity | API-Endpunkt/CLI-Befehl/UI-Button |
| Automatische wöchentliche Auslösung | interner `async_track_time_change` + Switch | Cron-Trigger + Konfigurationsflag |
| Konfigurationsassistent | Config-/Options-Flow (Voluptuous-Schemas) | beliebiges Formular-/Settings-UI |
| Wetteranbindung (Historie) | `WeatherProvider`-Interface, 2 Implementierungen (auch für Vorhersage) | reduziert auf einen festen Open-Meteo-Historical-Weather-API-Client (kein Interface/Pluggability mehr nötig, s. 1.7) |
| Wetteranbindung (Vorhersage) | Teil desselben `WeatherProvider`-Interfaces | entfällt komplett aus diesem System – Konsum einer externen Forecast-Template-Entität statt eigener API-Anbindung; Provider-Mapping wandert in separate Mapper-Helper-Projekte |
| Regressionsmodell | `numpy`-Ridge-Regression | unverändert übernehmbar (kein HA-Bezug) |
| Fachliche Vorhersage-/Trainingsformeln | `forecast.py`, Feature-Definition in `weighting.py` | größtenteils übernehmbar, aber gemäß 1.2/1.3/1.4 zu vereinfachen (s. u.) |

**Konkrete Vereinfachungen, die eine Neuimplementierung nach dem
aktualisierten Abschnitt 1 gegenüber dem heutigen Code mitbringen würde:**

| Heutiger Code (feingranular) | Entfällt zugunsten von (Energiesignatur-Ansatz) |
|---|---|
| `CONF_INDOOR_UNITS`-Liste, ein Climate-Mirror-Set (5 Sensoren) je Innengerät (2.6) | eine einzige historisierte Innentemperatur-Quelle je System |
| Setpoint-/HVAC-Modus-/Aktiv-Tracking je Innengerät (`coordinator._collect_indoor_unit_plans`, `_resolve_hvac_mode`) | entfällt vollständig – Heiz-/Kühlfall ergibt sich rein aus dem Vorzeichen (Innentemperatur vs. Außentemperatur) |
| `special_adjustments.py` (Nachtabsenkungs-Zeitfenster, Taktverlängerungs-Erfahrungswert) inkl. der zugehörigen Konfiguration in `const.py`/`config_flow.py` | entfällt vollständig – Wirkung steckt in der gemessenen Innentemperatur (s. 1.2) |
| 8 Features (`FEATURE_NAMES` inkl. `active_units`, `night_setback_offset`, `duty_throttle_offset`) | 3–8 Features (`bias`, `heating_/cooling_degree_hours`, optional `shortwave_radiation`/`wind_speed`/`humidity`/`wind_direction` als sin/cos) |
| `night_setback_active_entity` (bereits im heutigen Code totes Feld) | entfällt |
| Energiezähler-Pflichtfeld (im heutigen Code bereits ungenutzt) | entfällt |
| `weather/openmeteo.py`+`weather/dwd.py` decken sowohl Vorhersage als auch Historie ab | `openmeteo.py` bleibt (nur noch für die Historie), `dwd.py` entfällt aus diesem System (wandert in ein eigenständiges Mapper-Helper-Projekt) |

**Fazit:** `forecast.py` ist bereits framework-unabhängig geschrieben, ebenso
der Open-Meteo-Historie-Client aus dem `weather/`-Paket (bleibt bestehen,
nur ohne die `WeatherProvider`-Abstraktion und ohne `dwd.py`); die
Vorhersage-Beschaffung selbst entfällt komplett aus diesem System.
`weighting.py` liefert das Regressionsverfahren, das mit dem reduzierten
Feature-Satz aus 1.3
wiederverwendbar bleibt. `special_adjustments.py` sowie die gesamte
Innengeräte-/Mirror-Sensor-Logik für Climate-Entities (2.6, Teile von 2.3,
2.5, 2.12) entfallen bei einer Neuimplementierung nach dieser Spezifikation
ersatzlos – das ist die wichtigste architektonische Vereinfachung gegenüber
dem heutigen Code. Bei einem Framework-Wechsel bleiben vor allem die in
Abschnitt 2.4–2.7 beschriebenen HA-spezifischen Teile (Datenbeschaffung aus
dem Recorder, Storage, Scheduler, Entity-/UI-Modell) neu zu entwerfen –
jedoch anhand der schlankeren fachlichen Anforderungen aus Abschnitt 1.
