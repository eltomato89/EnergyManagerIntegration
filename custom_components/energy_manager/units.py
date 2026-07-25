"""Einheitennormalisierung auf Watt.

Portierung von ``src/lib/units.ts`` und ``src/lib/state.ts`` der Karte. Zwei
Leistungssensoren derselben Anlage haben nicht zwangsläufig dieselbe Einheit —
Wechselrichter melden gern kW, Steckdosen W. Intern wird deshalb ausschließlich
in Watt gerechnet.
"""

from __future__ import annotations

import math

from homeassistant.core import HomeAssistant, State

from .const import UNAVAILABLE_STATES
from .models import Reading, ReadingReason

# Umrechnungsfaktoren auf Watt, nach Kleinschreibung.
_POWER_FACTORS: dict[str, float] = {
    "w": 1.0,
    "kw": 1e3,
    "mw": 1e6,
    "gw": 1e9,
}

# Einheiten, die eindeutig keine Momentanleistung messen.
_NON_POWER_UNITS = frozenset(
    {
        "wh",
        "kwh",
        "mwh",
        "gwh",
        "%",
        "a",
        "v",
        "va",
        "var",
        "hz",
        "°c",
        "°f",
        "k",
    }
)

# Zustände, die ein Verbraucher als "an" melden kann. climate und water_heater
# nutzen eigene Begriffe.
_ON_STATES = frozenset({"on", "open", "heat", "cool", "auto", "heat_cool"})


def power_factor(unit: str | None) -> tuple[float | None, bool]:
    """Faktor zur Umrechnung in Watt.

    Gibt ``(faktor, ist_falsche_einheit)`` zurück. ``(None, True)`` bedeutet:
    die Einheit ist bekannt, misst aber keine Leistung. Das ist der häufigste
    Konfigurationsfehler — ein kWh-Zähler statt eines W-Sensors — und darf
    niemals stillschweigend als 0 W durchgehen.
    """
    if unit is None:
        return None, False

    trimmed = unit.strip()
    if not trimmed:
        return None, False

    # Milliwatt vor dem Kleinschreiben abfangen, sonst kollidiert es mit
    # Megawatt. Bei Mehrdeutigkeit gewinnt bewusst MW: ein als mW gemeldeter
    # Wert läge um den Faktor 1e9 daneben, ein MW-Wert nur um 1e-9.
    if trimmed == "mW":
        return 1e-3, False
    if trimmed == "MW":
        return 1e6, False

    key = trimmed.lower()
    if (factor := _POWER_FACTORS.get(key)) is not None:
        return factor, False

    return None, key in _NON_POWER_UNITS


def is_unavailable(state: State | None) -> bool:
    """Ist der Zustand nicht verwertbar?"""
    return state is None or state.state in UNAVAILABLE_STATES


def is_on(state: State | None) -> bool:
    """Gilt der Verbraucher als eingeschaltet?"""
    return state is not None and state.state in _ON_STATES


def read_power_w(hass: HomeAssistant, entity_id: str | None) -> Reading:
    """Liest einen Leistungssensor und normalisiert ihn auf Watt.

    Bewusst ohne Rückfall auf 0: ein fehlender oder falsch konfigurierter
    Sensor muss als solcher erkennbar bleiben, sonst rechnet die Automatik
    stillschweigend mit einem falschen Überschuss und schaltet danach.
    """
    if not entity_id:
        return Reading(w=None, reason=ReadingReason.MISSING)

    state = hass.states.get(entity_id)
    if state is None:
        return Reading(w=None, reason=ReadingReason.MISSING)

    if is_unavailable(state):
        return Reading(w=None, reason=ReadingReason.UNAVAILABLE)

    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return Reading(w=None, reason=ReadingReason.NAN)

    if not math.isfinite(value):
        return Reading(w=None, reason=ReadingReason.NAN)

    unit = state.attributes.get("unit_of_measurement")
    factor, wrong_unit = power_factor(unit)

    if wrong_unit:
        return Reading(w=None, reason=ReadingReason.WRONG_UNIT, unit=unit)

    if factor is None:
        # Keine Einheit angegeben. Sehr viele Template-Sensoren lassen sie weg,
        # deshalb W annehmen — aber markieren, damit es auffällt.
        return Reading(w=value, assumed_unit=True)

    return Reading(w=value * factor, unit=unit)


def read_percent(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Liest einen Prozentwert und klemmt ihn auf 0..100."""
    if not entity_id:
        return None

    state = hass.states.get(entity_id)
    if is_unavailable(state):
        return None

    try:
        value = float(state.state)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value):
        return None

    return min(100.0, max(0.0, value))


def invert(reading: Reading, do_invert: bool) -> Reading:
    """Kehrt das Vorzeichen um, ohne aus None eine 0 zu machen."""
    if not do_invert or reading.w is None:
        return reading
    return Reading(
        w=-reading.w,
        reason=reading.reason,
        assumed_unit=reading.assumed_unit,
        unit=reading.unit,
    )


def combine_battery(charge: Reading, discharge: Reading) -> Reading:
    """Setzt die Batterieleistung aus zwei stets positiven Sensoren zusammen.

    Ergebnis nach Konvention: >0 = Laden, <0 = Entladen. Liefert nur einer der
    beiden einen Wert, zählt dieser allein.
    """
    if charge.w is None and discharge.w is None:
        return Reading(w=None, reason=charge.reason or discharge.reason or ReadingReason.MISSING)

    return Reading(w=(charge.w or 0.0) - (discharge.w or 0.0))


def round_w(value: float) -> float:
    """Rundet auf ganze Watt — hält Gleitkommarauschen aus den Zuständen."""
    return float(round(value))
