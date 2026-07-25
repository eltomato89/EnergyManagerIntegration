# Energy Manager — Integration

Home-Assistant-Integration, die den PV-Überschuss berechnet und Verbraucher **nach Priorität
automatisch schaltet**.

Sie ist das Gegenstück zur [Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard):
Die Karte zeigt an und lässt manuell bedienen, die Integration entscheidet und schaltet. Beide
rechnen mit derselben Formel — sonst würde die Anzeige etwas anderes behaupten, als tatsächlich
passiert.

> **Status: in Entwicklung.** Diese Fassung enthält erst das Grundgerüst.

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
