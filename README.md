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

## Sicherheitsnetze

Die Integration greift in eine reale Anlage ein. Sechs Mechanismen verhindern, dass sie das zum
falschen Zeitpunkt tut — jeder einzelne schließt einen Fall, der sonst als Pendeln oder
Fehlschaltung auffiele:

| Schutz | Wogegen |
| --- | --- |
| **Höchstens eine Schaltung je Durchlauf** | Drei Geräte gleichzeitig zuschalten hieße, dreimal mit demselben Budget zu rechnen |
| **Antizipation** | Zwischen dem Zuschalten und dem Zeitpunkt, an dem der Zähler es zeigt, vergehen Sekunden. In dieser Lücke würde derselbe Überschuss ein zweites Mal vergeben |
| **Beruhigungsfenster** (Standard 60 s) | Nach einer Schaltung bleibt dieses Gerät unangetastet, egal was der Überschuss macht |
| **Glättung** (Standard 60 s) | Eine vorbeiziehende Wolke ist kein Grund abzuschalten. Solange das Mittelungsfenster erst halb gefüllt ist, wird gar nicht geschaltet |
| **Nichts beim Start** | Während HA hochfährt melden nicht alle Entitäten einen Zustand — die Anlage sähe aus wie nach einem Totalausfall |
| **Nichts bei unbrauchbarem Sensor** | Ein kWh-Zähler oder ein ausgefallener Sensor ist ein Konfigurationsfehler, kein Überschuss von 0 W |

Dazu kommen je Verbraucher die vier Zeitfelder (`turn_on_delay`, `turn_off_delay`, `min_runtime`,
`min_off_time`) und der eigene Automatik-Schalter.

**Warum passiert gerade nichts?** Der Status-Sensor jedes Verbrauchers hat ein Attribut
`blocked_by` mit genau dieser Antwort — ohne das ist eine ausbleibende Schaltung nicht von einem
Fehler zu unterscheiden.

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
