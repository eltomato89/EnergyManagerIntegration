# Änderungsprotokoll

Dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [0.1.0] — 2026-07-26

Erste Fassung. Die Integration rechnet, entscheidet und schaltet.

> **Noch nicht über längere Zeit an einer realen Anlage erprobt.** Alle Belege stammen aus Tests.
> Der Hauptschalter steht nach dem Einrichten deshalb bewusst auf **aus** — erst beobachten, dann
> scharfschalten.

### Was sie tut

- **Überschuss berechnen** aus einem bidirektionalen Netzsensor oder getrennten Sensoren für
  Erzeugung und Verbrauch, mit Hausbatterie, Reserve, Ladestandsregel und zeitgewichteter Glättung.
- **Verbraucher nach Priorität schalten.** Jeder Verbraucher ist ein Config-Subentry und wird zu
  einem eigenen Gerät mit vier Entitäten: Priorität, Automatik-Teilnahme, Ampelzustand und
  „gesperrt bis".
- **Die vier Zeitfelder durchsetzen** (`turn_on_delay`, `turn_off_delay`, `min_runtime`,
  `min_off_time`) — auf Grundlage eines eigenen, über Neustarts hinweg gespeicherten
  Laufzeitzustands. `last_changed` der Schalt-Entität taugt dafür nicht: Manuelles Schalten und ein
  Neustart setzen es zurück, und über die Verzögerungen sagt es prinzipbedingt nichts aus.
- **Vier Dienste** für befristete Eingriffe: `force_on`, `clear_force`, `pause`, `resume`.

### Sicherungen gegen Fehlschaltungen

Jede schließt einen Fall, der sonst als Pendeln oder Fehlverhalten auffiele:

- Höchstens **eine Schaltung je Durchlauf** — jede verändert den Überschuss, auf den die nächste
  Entscheidung baut.
- **Antizipation:** Die gerade geschaltete Leistung wird sofort vom Überschuss abgezogen. Zwischen
  dem Zuschalten und dem Zeitpunkt, an dem Zähler und Glättung es zeigen, vergehen Sekunden — in
  dieser Lücke würde derselbe Überschuss ein zweites Mal vergeben. Das Beruhigungsfenster allein
  reicht dagegen nicht: es schützt nur den gerade geschalteten Verbraucher, nicht den nächsten.
- **Beruhigungsfenster** je Verbraucher (Standard 60 s).
- **Kein Schalten auf einem halb gefüllten Mittelungsfenster.**
- **Kein Schalten während des HA-Starts** — dann melden nicht alle Entitäten einen Zustand.
- **Kein Schalten bei unbrauchbarem Sensor.** Ein kWh-Zähler ist ein Konfigurationsfehler, kein
  Überschuss von 0 W.

### Zusammenspiel mit der Karte

Die [Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard) ab **v0.4.0** findet die
Integration von selbst und zeigt sie an. Verbraucher werden dann **nur noch hier** gepflegt.

Beide Seiten rechnen nachweislich identisch: Aus der TypeScript-Referenz der Karte sind knapp 200
Fälle generiert, gegen die die Python-Portierung antritt (`tests/test_parity.py`). Weicht ein
Ergebnis ab, ist das ein Fehler — keine zulässige Abweichung.

### Bekannte Einschränkung

Die Budget-Verteilung weicht **bewusst** von der Karte ab: Die Karte reserviert Budget auch für
nicht verwaltete Verbraucher (richtig für die Anzeige), die Integration nicht (richtig für die
Schaltentscheidung). Der gemeldete Ampelzustand ist auf beiden Seiten derselbe.
