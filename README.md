# Energy Manager — Integration

Home-Assistant-Integration, die den PV-Überschuss berechnet und Verbraucher **nach Priorität
automatisch schaltet**.

Sie ist das Gegenstück zur [Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard):
Die Karte zeigt an und lässt manuell bedienen, die Integration entscheidet und schaltet. Beide
rechnen mit derselben Formel — sonst würde die Anzeige etwas anderes behaupten, als tatsächlich
passiert.

> **Status: in Entwicklung.** Die Automatik schaltet, ist aber noch nicht über längere Zeit an
> einer realen Anlage erprobt. Der Hauptschalter steht nach dem Einrichten bewusst auf **aus** —
> erst beobachten, dann scharfschalten.

## Installation

Braucht Home Assistant **2025.2** oder neuer (Config Subentries).

### HACS

1. HACS → Menü ⋮ → **Benutzerdefinierte Repositories**
2. `https://github.com/eltomato89/EnergyManagerIntegration` eintragen, Kategorie **Integration**
3. „Energy Manager" installieren, Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Energy Manager"

### Manuell

Den Ordner `custom_components/energy_manager` nach `/config/custom_components/` kopieren und
Home Assistant neu starten.

## Einrichtung

1. **Zählerquelle** wählen: ein bidirektionaler Netzsensor (>0 Bezug, <0 Einspeisung) oder
   getrennte Sensoren für Erzeugung und Hausverbrauch. Optional eine Hausbatterie.
2. **Verbraucher hinzufügen** — je einer über „Untereintrag hinzufügen" am Integrationseintrag.
   Pflicht ist nur die Schalt-Entität; Leistungssensor, Nennleistung und die Zeitfelder machen die
   Entscheidung genauer.
3. **Beobachten.** Der Überschuss-Sensor sollte mit der Anlage übereinstimmen, die Ampeln
   plausibel sein. Weicht etwas ab, stimmt die Konfiguration nicht — nicht die Rechnung.
4. Erst dann den **Hauptschalter** einschalten.

Die [Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard) ab v0.4.0 findet die
Integration von selbst und zeigt alles an. Verbraucher werden **nur hier** gepflegt.

## Sicherheitsnetze

Die Integration greift in eine reale Anlage ein. Sechs Mechanismen verhindern, dass sie das zum
falschen Zeitpunkt tut — jeder einzelne schließt einen Fall, der sonst als Pendeln oder
Fehlschaltung auffiele:

| Schutz | Wogegen |
| --- | --- |
| **Höchstens eine Schaltung je Durchlauf** | Drei Geräte gleichzeitig zuschalten hieße, dreimal mit demselben Budget zu rechnen |
| **Antizipation** | Zwischen dem Zuschalten und dem Zeitpunkt, an dem der Zähler es zeigt, vergehen Sekunden. In dieser Lücke würde derselbe Überschuss ein zweites Mal vergeben |
| **Beruhigungsfenster** (Standard 60 s) | Nach einer Schaltung bleibt dieses Gerät unangetastet, egal was der Überschuss macht. **Mindestens so lang wählen, wie der Leistungssensor braucht** — träge Steckdosen melden erst nach Minuten |
| **Glättung** (Standard 60 s) | Eine vorbeiziehende Wolke ist kein Grund abzuschalten. Solange das Mittelungsfenster erst halb gefüllt ist, wird gar nicht geschaltet |
| **Nichts beim Start** | Während HA hochfährt melden nicht alle Entitäten einen Zustand — die Anlage sähe aus wie nach einem Totalausfall |
| **Nichts bei unbrauchbarem Sensor** | Ein kWh-Zähler oder ein ausgefallener Sensor ist ein Konfigurationsfehler, kein Überschuss von 0 W |

Dazu kommen je Verbraucher die vier Zeitfelder (`turn_on_delay`, `turn_off_delay`, `min_runtime`,
`min_off_time`) und der eigene Automatik-Schalter.

## Was die Priorität bedeutet

Sie entscheidet zweierlei:

1. **Wer zuerst bedient wird**, wenn Überschuss frei wird.
2. **Wer wem weichen muss**, wenn er sonst gar nicht zum Zug käme.

Der zweite Punkt ist der wichtigere. Ein Beispiel: 800 W Überschuss, drei Verbraucher mit 1000 W,
500 W und 200 W Bedarf in dieser Rangfolge. Für den ersten reicht es nicht, also gehen der zweite
und der dritte an. Steigt der Überschuss auf 1100 W, sind trotzdem nur 400 W frei — der wichtigste
Verbraucher käme **nie** dran, obwohl die Anlage längst genug liefert.

Deshalb gilt: Reicht *verfügbarer Überschuss + Last der laufenden, niedriger priorisierten
Verbraucher* für einen wichtigeren, weichen diese. Es weichen so wenige wie möglich, die
unwichtigsten zuerst, und nur wenn es am Ende auch reicht — sonst hätte man abgeschaltet und
nichts gewonnen.

**Nicht verdrängt wird**, wer eine Mindestlaufzeit abarbeitet (ein angefangener Waschgang wird
nicht abgebrochen), wer nicht an der Automatik teilnimmt, wer unter Zwangsfreigabe läuft oder wer
gerade erst geschaltet wurde. Das Attribut `displaces` am Status-Sensor zeigt, wie viele für einen
Verbraucher weichen würden.

## Warum passiert gerade nichts?

Die häufigste Frage — und ohne Antwort ist eine ausbleibende Schaltung nicht von einem Fehler zu
unterscheiden. Zwei Stellen geben sie:

Der **Status-Sensor der Automatik** (`sensor.…_status`) für das Ganze:

| Wert | Bedeutung |
| --- | --- |
| `starting` | HA fährt noch hoch, oder das Mittelungsfenster ist zu dünn besetzt |
| `sensor_error` | Ein Zählersensor fehlt, ist ausgefallen oder misst keine Leistung |
| `paused` | Hauptschalter aus |
| `running` | Alles bereit |

Der **Status-Sensor jedes Verbrauchers** im Attribut `blocked_by` für den Einzelfall:

| Wert | Bedeutung |
| --- | --- |
| `not_managed` | Automatik-Schalter dieses Verbrauchers ist aus |
| `unavailable` | Die Schalt-Entität meldet keinen Zustand |
| `settling` | Beruhigungsfenster nach der letzten Schaltung läuft |
| `forced` | Zwangsfreigabe läuft — die Automatik hält sich fern |
| `turn_on_delay` / `turn_off_delay` | Die Bedingung gilt noch nicht lange genug |
| `min_runtime` / `min_off_time` | Sperrzeit läuft, siehe „gesperrt bis" |

Steht dort nichts, reicht schlicht der Überschuss nicht — das sagt dann der Ampelzustand
(`off_close`, `off_insufficient`).

## Was die Integration bereitstellt

Ein Hub-Gerät und je Verbraucher ein eigenes Gerät:

**Hub**

| Entität | Zweck |
| --- | --- |
| `switch.…_automatik` | Hauptschalter. Aus bedeutet: es wird nichts geschaltet |
| `sensor.…_ueberschuss` | Verfügbarer Überschuss nach Reserve und Ladestandsregel |
| `sensor.…_ueberschuss_roh` | Ungeglätteter Rohwert, für die Fehlersuche |
| `sensor.…_status` | Zustand der Automatik |

**Je Verbraucher**

| Entität | Zweck |
| --- | --- |
| `switch.<name>_automatik` | Nimmt dieser Verbraucher an der Automatik teil? |
| `number.<name>_prioritaet` | Rang, 1 = höchste |
| `sensor.<name>_status` | Ampelzustand, dieselben Werte wie in der Karte |
| `sensor.<name>_gesperrt_bis` | Wann die Sperre endet — exakt, nicht geschätzt |

Damit braucht es **keine Helfer-Variablen**. Die Karte liest und bedient diese Entitäten direkt.

## Dienste

Vier Dienste für das, was sich über Entitäten allein nicht ausdrücken lässt: eine **Dauer**. Der
Hauptschalter kennt nur an und aus.

| Dienst | Wofür |
| --- | --- |
| `energy_manager.force_on` | Schaltet einen Verbraucher sofort ein und hält ihn für die angegebene Zeit an — unabhängig vom Überschuss. Wirkt auch bei ausgeschaltetem Hauptschalter, denn es ist eine Bedienung, keine Automatikentscheidung |
| `energy_manager.clear_force` | Beendet eine Zwangsfreigabe vorzeitig. Schaltet nichts ab; ab dann entscheidet wieder der Überschuss |
| `energy_manager.pause` | Hält die Automatik an, auf Wunsch befristet („zwei Stunden Ruhe", etwa während einer Wartung) |
| `energy_manager.resume` | Schaltet sie wieder scharf |

`force_on` und `clear_force` zielen auf ein **Verbrauchergerät**; eine Entität desselben Geräts
funktioniert ebenso, weil eine Automatisierung meist eine `entity_id` zur Hand hat.

```yaml
action: energy_manager.force_on
target:
  device_id: <Wallbox>
data:
  duration: "01:30:00"
```

## Entwicklung

```bash
uv venv --python 3.13
uv pip install -r requirements_test.txt
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
```

Der Rechenkern (`surplus.py`, `smoothing.py`, `engine.py`) ist eine Portierung der Kartenlogik und
wird gegen dieselben Testfälle geprüft. Weicht ein Ergebnis ab, ist das ein Fehler — nicht eine
zulässige Abweichung.

## Lizenz

MIT
