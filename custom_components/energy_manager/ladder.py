"""Die erreichbaren Leistungsstufen eines regelbaren Verbrauchers.

Das Stufenraster wird **aus der Steuerentität gelesen**, nicht konfiguriert:
Eine ``number`` führt ``min``, ``max``, ``step`` und ihre Einheit als Attribute,
ein ``select`` seine ``options``. Konfiguriert wird nur, was Home Assistant nicht
wissen kann — die Übersetzung in Watt.

Bewusst bei jedem Durchlauf neu gelesen und nicht gepuffert: Manche
Wallbox-Integrationen verengen ihr ``max`` im Betrieb, weil das Fahrzeug seine
Grenze meldet oder ein Lastmanagement eingreift. Wer das Raster einmal merkt,
verschenkt diese Angabe und stellt Stufen, die es gerade nicht gibt.
"""

from __future__ import annotations

import math
from typing import Final

from homeassistant.core import HomeAssistant, State

from .const import MAX_LEVELS, NOMINAL_VOLTAGE
from .models import ConsumerConfig, Ladder, Level
from .units import is_unavailable, power_factor, round_w

CONTROL_NUMBER_W: Final = "number_w"
CONTROL_NUMBER_A: Final = "number_a"
CONTROL_SELECT: Final = "select"


def build_ladder(hass: HomeAssistant, consumer: ConsumerConfig) -> Ladder | None:
    """Liest das Stufenraster aus der Steuerentität.

    ``None``, sobald etwas nicht stimmt — fehlende Entität, unbrauchbarer
    Zustand, unbekannte Einheit, keine verwertbare Stufe. Bewusst kein Rückfall
    auf einen angenommenen Wert: Ein Verbraucher ohne Raster ist nicht regelbar,
    und das gehört als solches gemeldet statt geraten.
    """
    if not consumer.modulating or not consumer.control_entity:
        return None

    state = hass.states.get(consumer.control_entity)
    if is_unavailable(state):
        return None
    assert state is not None

    domain = consumer.control_entity.split(".", 1)[0]
    if domain == "select":
        ladder = _from_select(state, consumer)
    elif domain == "number":
        ladder = _from_number(state, consumer)
    else:
        return None

    return _apply_minimum(ladder, consumer.min_level_w)


def read_level(hass: HomeAssistant, consumer: ConsumerConfig, ladder: Ladder) -> Level | None:
    """Auf welcher Stufe der Verbraucher gerade steht.

    Gelesen wird die **Stellgröße**, nicht der Leistungssensor: Sie sagt, was
    angefordert ist. Ob die Last dem folgt, ist eine andere Frage — ein Fahrzeug,
    das bei 7 A stehen bleibt, obwohl 10 A angeboten sind, wäre daran nicht zu
    erkennen. Diese zweite Prüfung ist bewusst noch nicht hier.
    """
    if not consumer.control_entity:
        return None

    state = hass.states.get(consumer.control_entity)
    if is_unavailable(state):
        return None
    assert state is not None

    if ladder.source == CONTROL_SELECT:
        return ladder.for_command(state.state)

    try:
        return ladder.for_command(float(state.state))
    except (TypeError, ValueError):
        return None


def _from_number(state: State, consumer: ConsumerConfig) -> Ladder | None:
    """Raster einer ``number``: aus min, max, step und der Einheit.

    Die Einheit entscheidet über die Umrechnung, und sie **muss** vorhanden
    sein. Anders als bei einem Leistungssensor darf sie hier nicht angenommen
    werden: Eine als Watt gelesene Ampere-Entität ergäbe eine Leiter von 6 bis
    16 W, die immer passt — und die Automatik schriebe 16, während das Gerät
    16 A zieht. Ein stiller Fehler um den Faktor 690.
    """
    kind, factor = _control_unit(state.attributes.get("unit_of_measurement"))
    if kind is None:
        return None

    # Dreiphasig zieht dieselbe Stromstufe die dreifache Leistung.
    per_unit = consumer.phases * NOMINAL_VOLTAGE if kind == CONTROL_NUMBER_A else factor

    minimum = _number_attr(state, "min")
    maximum = _number_attr(state, "max")
    step = _number_attr(state, "step")

    if consumer.max_level_w and consumer.max_level_w > 0:
        grenze = _cap_to_grid(consumer.max_level_w / per_unit, minimum, step)
        if minimum is not None and grenze < minimum:
            # Die Grenze liegt unter dem, was die Entität überhaupt annimmt.
            # Ein Konfigurationsfehler, und kein Anlass, sie zu übergehen.
            return None
        maximum = grenze if maximum is None else min(maximum, grenze)

    # Die Obergrenze wirkt **vor** dem Ausdünnen: Sonst lägen die Stufen über
    # einen Bereich verteilt, den das Gerät gar nicht bedient, und im nutzbaren
    # Teil bliebe nur ein Bruchteil der Auflösung übrig.
    values = _grid(minimum, maximum, step)
    if not values:
        return None

    levels = tuple(Level(w=round_w(value * per_unit), command=value) for value in values)
    return Ladder(levels=levels, source=kind)


def _cap_to_grid(grenze: float, minimum: float | None, step: float | None) -> float:
    """Rundet eine Obergrenze auf den nächsttieferen Rasterpunkt der Entität ab.

    Die Grenze ist in Watt eingetragen und trifft das Raster der Stellgröße
    selten genau. Ein Wert dazwischen wäre keine Stellgröße, die das Gerät
    annimmt — bei 1-A-Schritten stünden sonst 10,14 A auf der Leiter.

    Abgerundet und nicht auf: Die Grenze ist eine Obergrenze.
    """
    if not step or step <= 0:
        return grenze
    basis = minimum if minimum is not None else 0.0
    if grenze <= basis:
        return grenze
    return basis + math.floor((grenze - basis) / step) * step


def _from_select(state: State, consumer: ConsumerConfig) -> Ladder | None:
    """Raster eines ``select``: aus den Optionen und der eingetragenen Zuordnung.

    Optionen ohne Zuordnung fallen heraus, statt geschätzt zu werden — eine
    Auswahlliste sagt nichts über Leistung. Eine Option mit 0 W ist die
    Aus-Stellung und keine Stufe; abgeschaltet wird über ``switch_entity``.
    """
    options = state.attributes.get("options") or []
    mapping = consumer.level_map or {}

    obergrenze = consumer.max_level_w if consumer.max_level_w and consumer.max_level_w > 0 else None
    levels = [
        Level(w=round_w(float(mapping[option])), command=option)
        for option in options
        if option in mapping
        and float(mapping[option]) > 0
        and (obergrenze is None or float(mapping[option]) <= obergrenze)
    ]
    if not levels:
        return None

    levels.sort(key=lambda level: level.w)
    return Ladder(levels=tuple(levels), source=CONTROL_SELECT)


def describe(ladder: Ladder) -> str:
    """Kurzfassung des Rasters für die Oberfläche.

    Bewusst nur Zahlen und Einheit, ohne Wortlaut: Der Text entsteht im Code und
    landet als Platzhalter in einer übersetzten Zeile — ein deutsches oder
    englisches Wort darin wäre in der anderen Sprache falsch.
    """
    return f"{ladder.count} @ {ladder.min_w:.0f}-{ladder.max_w:.0f} W"


def control_kind(unit: str | None) -> str | None:
    """Art der Stellgröße, oder ``None``, wenn die Einheit nicht verwertbar ist."""
    kind, _factor = _control_unit(unit)
    return kind


def _control_unit(unit: str | None) -> tuple[str | None, float]:
    """Wie der Wert der Steuerentität zu lesen ist.

    Gibt ``(art, faktor)`` zurück; ``(None, 0)`` heißt: nicht verwertbar. Ampere
    wird hier ausdrücklich zugelassen, obwohl ``power_factor`` es als
    Nicht-Leistung führt — für einen Messwert ist es das auch, für eine
    Stellgröße nicht.
    """
    if unit is not None and unit.strip().lower() == "a":
        return CONTROL_NUMBER_A, 1.0

    factor, _wrong = power_factor(unit)
    if factor is None:
        return None, 0.0
    return CONTROL_NUMBER_W, factor


def _number_attr(state: State, key: str) -> float | None:
    try:
        value = float(state.attributes[key])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _grid(minimum: float | None, maximum: float | None, step: float | None) -> list[float]:
    """Die Stellwerte zwischen ``minimum`` und ``maximum``.

    Ein zu feines Raster wird ausgedünnt (siehe ``MAX_LEVELS``), das Maximum
    dabei aber immer behalten: Es ist die Stufe, auf die es am meisten ankommt,
    und beim Ausdünnen fällt es sonst als erstes heraus.
    """
    if maximum is None or maximum <= 0:
        return []
    if minimum is None or minimum < 0:
        minimum = 0.0
    if minimum >= maximum:
        return [maximum]

    span = maximum - minimum
    if step is None or step <= 0:
        # Ohne brauchbare Schrittweite bleiben die beiden Enden.
        return [minimum, maximum] if minimum > 0 else [maximum]

    count = int(span / step) + 1
    if count > MAX_LEVELS:
        step *= math.ceil(count / MAX_LEVELS)
        count = int(span / step) + 1

    values = [minimum + index * step for index in range(count)]
    # Nur echte Stufen: eine 0 wäre das Abschalten und gehört nicht hierher.
    values = [value for value in values if value > 0]

    if not values:
        return [maximum]

    if values[-1] < maximum - 1e-9:
        # Geht die Spanne nicht glatt in der Schrittweite auf, fehlt oben das
        # Maximum. Ist die Grenze schon erreicht, tritt es an die Stelle der
        # obersten Stufe statt hinzuzukommen: Die liegt bauartbedingt weniger
        # als eine Schrittweite darunter, das Maximum ist die wichtigere Angabe.
        if len(values) >= MAX_LEVELS:
            values[-1] = maximum
        else:
            values.append(maximum)

    return values


def _apply_minimum(ladder: Ladder | None, min_level_w: float | None) -> Ladder | None:
    """Streicht Stufen unter der eingetragenen Untergrenze.

    Bleibt keine übrig, ist der Verbraucher nicht regelbar: Die Untergrenze
    liegt dann über allem, was das Gerät kann, und das ist ein
    Konfigurationsfehler — kein Anlass, die Grenze stillschweigend zu übergehen.
    """
    if ladder is None or not min_level_w or min_level_w <= 0:
        return ladder

    bleiben = tuple(level for level in ladder.levels if level.w >= min_level_w)
    if not bleiben:
        return None
    return Ladder(levels=bleiben, source=ladder.source)
