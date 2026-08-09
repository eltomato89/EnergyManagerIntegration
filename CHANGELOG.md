# Änderungsprotokoll

Dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [0.4.0b3] — 2026-08-09

### Behoben

- **„Unknown error occurred" beim Neukonfigurieren.** Der Ablauf endete auf dem Batterie-Formular
  mit `async_create_entry` — Home Assistant verbietet das in einem `reconfigure`-Ablauf und wirft
  dort seit Längerem eine Ausnahme (`Creates a new entry in a 'reconfigure' flow`). Der Eintrag
  wird nun aktualisiert statt neu angelegt. Betrifft jeden, der die neue **Maximale Ladeleistung**
  eintragen wollte: Sie liegt in den Eintragsdaten und ist nur über „Neu konfigurieren"
  erreichbar.

- **Die Folgeformulare beim Neukonfigurieren waren leer.** Sensoren und Schalter mussten erneut
  ausgewählt werden, und die Felder mit Vorgabewert — Entladeverhalten, Batteriereserve — fielen
  dabei stillschweigend auf ihre Vorgabe zurück. Alle Schritte sind jetzt mit dem bisherigen Stand
  vorbelegt; ein geleertes Feld bleibt leer, und ein Wechsel des Zählermodus lässt keine Reste des
  anderen Modus zurück.

### Geändert

- **Batteriereserve und maximale Ladeleistung zählten doppelt.** Die Reserve wird schon vor dem
  ersten Verbraucher vom Überschuss abgezogen; die Ladeleistung kam an ihrem Rang noch einmal
  obendrauf. Bei 800 W Reserve und 2000 W Ladeleistung entzog die Batterie den Verbrauchern 2800 W,
  obwohl 2000 W eingetragen waren. Die Reserve wird nun auf die Ladeleistung **angerechnet**: An
  ihrem Rang fordert die Batterie nur noch die Differenz an, und insgesamt bekommt sie nie mehr als
  die eingetragene Ladeleistung.

- **Zwei Angaben ohne Wirkung werden jetzt abgewiesen.** Die maximale Ladeleistung braucht eine
  Batterie-Entität, der Mindestladestand einen Ladestandssensor. Ohne sie stand eine Zahl im
  Formular, die stillschweigend nichts tat.

- **Die Beschreibung der Nennleistung war irreführend.** „Maximale Leistungsaufnahme" las sich wie
  eine Obergrenze für den Betrieb; das Feld dient ausschließlich als Ersatzwert für den Bedarf,
  solange keine Einschaltschwelle eingetragen ist.

### Hinzugefügt

- Attribut `battery_headroom_w` am Überschuss-Sensor: was nach der Batterie noch für tiefer
  priorisierte Verbraucher bleibt. Ohne diese Angabe ließ sich in der Karte nicht nachvollziehen,
  warum ein Verbraucher unter der Batterie leer ausgeht.

### Entfernt

- Vier nie befüllte Platzhalterfelder an `ConsumerConfig` (`steps`, `step_entity`, `window_start`,
  `window_end`) und die ungenutzte Konstante `CONF_PRIORITY`. Ohne Formularfeld und ohne Leser
  waren sie nur ein Versprechen im Code.

## [0.3.3] — 2026-07-27

### Geändert

- **README auf Englisch**, samt Hinweis darauf, dass die Entitäts-IDs der Sprache der Instanz
  folgen: Auf einer deutschen Instanz heißen dieselben Sensoren `sensor.…_ueberschuss` und
  `sensor.<name>_gesperrt_bis`.

<sub>Ohne diese Version zeigt HACS weiterhin die deutsche Fassung: Es liest die README aus dem
installierten Release-Tag, nicht aus dem Standard-Branch.</sub>

## [0.3.2] — 2026-07-26

### Geändert

- **Alle Nutzertexte auf ein sachliches Register gebracht.** Die Ampelzustände lasen sich wie eine
  Notenbeurteilung („reicht nicht", „fast ausreichend"); sie benennen nun den Zustand und den
  Begriff, um den es geht — „Überschuss unzureichend", „Überschuss knapp", „Einschaltbereit",
  „In Betrieb, gedeckt". Identisch zur [Karte](https://github.com/eltomato89/EnergyManagerCard),
  sonst zeigte die Kartenzeile etwas anderes als der Sensor, aus dem sie stammt.

- Ebenso überarbeitet: die Anrede-Imperative in den Beschreibungen, „Mindest-Aus-Zeit" zu
  „Mindestpause", und Formulierungen wie „einfach leer lassen" oder „damit Wolken keine Schaltflut
  auslösen".

- Vier Prüfungen halten das Register künftig fest, darunter die Deckungsgleichheit beider Sprachen:
  ein Text, der nur in einer existiert, erschien bisher als roher Schlüssel.

## [0.3.1] — 2026-07-26

### Behoben

- **Bereitschaftsleistung wurde als Bedarf übernommen.** Ein Luftentfeuchter mit Hygrostat zieht im
  Standby zwei Watt, obwohl sein Schalter an ist. Daraus wurde `required_w: 2` — die Automatik
  hielt das Gerät für mit zwei Watt zuschaltbar. Es wäre angelaufen, hätte seine echten paar
  hundert Watt gezogen, und das Ergebnis wäre Netzbezug gewesen.

  Messwerte unter 50 W gelten jetzt als Bereitschaftsbetrieb und werden nicht als Bedarf gewertet;
  stattdessen greift die Schätzung aus der Statistik oder der Vorgabewert. Derselbe Fall betrifft
  eine Waschmaschine nach dem Programm oder ein Klimagerät ohne Kühlbedarf.

- **Zwei Verbraucher für dasselbe Gerät werden abgelehnt.** Die Automatik hätte es doppelt
  verplant: den Bedarf zweimal abgerechnet, es für zweimal schaltbar gehalten und zwei getrennte
  Sperrzeiten geführt. Passiert leicht, wenn die Entitäts-ID nicht zum Anzeigenamen passt.

- **Eine 0 bei der Nennleistung wird nicht mehr gespeichert.** Sie sah im Attribut wie eine Angabe
  aus, wirkte aber nicht. Bei `hysteresis` und den Zeitfeldern bleibt 0 erhalten — dort bedeutet
  sie „aus".

## [0.3.0] — 2026-07-26

### Neu — ändert das Schaltverhalten

- **Fehlt die Nennleistung, wird sie aus der Statistik geschätzt.** Bisher rechnete die Automatik
  dann mit einem festen Vorgabewert von 500 W — bei einer Wärmepumpe ein Bruchteil, bei einer
  Umwälzpumpe ein Vielfaches. Herangezogen wird das Maximum der letzten sieben Tage, einmal
  täglich neu; der Mittelwert taugt nicht, weil in ihn alle Stunden eingehen, in denen das Gerät
  aus war. Eingetragene Werte werden nie überstimmt.

  Das Attribut `required_source` am Status-Sensor zeigt die Herkunft: `min_power`, `max_power`,
  `measured`, `estimated` oder `default`. Ohne diese Angabe ist ein geratener Wert nicht von einem
  eingetragenen zu unterscheiden.

- Der Hilfetext zur **Beruhigungszeit** sagt jetzt, wonach sie sich richtet: nach der Trägheit des
  Leistungssensors. Ist der Sensor langsamer als das Fenster, sieht die Automatik danach wieder
  den vollen Überschuss, obwohl die Last längst läuft.

## [0.2.0] — 2026-07-26

### Neu — ändert das Schaltverhalten

- **Priorität setzt sich jetzt wirklich durch.** Bisher bedeutete sie nur „wer wird zuerst
  bedient, wenn Überschuss frei wird". Zwei kleine Verbraucher konnten damit einen großen mit
  höherem Rang **dauerhaft aussperren**: Bei 800 W Überschuss gehen ein 500-W- und ein
  200-W-Gerät an; steigt der Überschuss auf 1100 W, sind trotzdem nur 400 W frei — für den
  1000-W-Verbraucher an Position 1 reicht das nie.

  Reicht nun *verfügbarer Überschuss + Last der laufenden, niedriger priorisierten Verbraucher*
  für einen wichtigeren, weichen diese. Es weichen so wenige wie möglich, die unwichtigsten
  zuerst, und nur wenn es am Ende auch reicht.

  **Nicht verdrängt wird**, wer eine Mindestlaufzeit abarbeitet, nicht an der Automatik teilnimmt,
  unter Zwangsfreigabe läuft oder gerade erst geschaltet wurde. Abschalten und Einschalten
  geschehen im selben Durchlauf — sonst könnte dazwischen ein Dritter den frei gewordenen
  Überschuss belegen.

- Der Status-Sensor jedes Verbrauchers hat ein Attribut `displaces`: wie viele für ihn weichen
  würden. Es erklärt den sonst überraschenden Fall, dass ein Gerät angeht, obwohl der Überschuss
  allein nicht reicht.

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
