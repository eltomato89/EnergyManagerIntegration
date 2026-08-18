"""Tagesziel: was ein Verbraucher bis zum Abend geschafft haben muss.

Manche Lasten haben einen Bedarf, der sich nicht vertagen lässt. Die Poolpumpe
muss ihre Umwälzung schaffen, ob die Sonne mitspielt oder nicht. Bisher konnte
die Automatik das nicht wissen: Sie schaltete nach Überschuss, und blieb der
aus, blieb die Pumpe aus.

Die Regel ist ein Vergleich, kein Regelkreis: Reicht die **verbleibende
Prognose** nicht mehr für die **noch fehlende Energie**, läuft der Verbraucher
auch ohne Überschuss weiter. Vorher nicht — solange es sich mit Sonne ausgeht,
gibt es keinen Grund, Netzstrom zu nehmen.

Der Tag beginnt mit dem **Sonnenaufgang** und nicht um Mitternacht. Bei einer
Anlage, deren Ertrag an der Sonne hängt, ist das der Schnitt, der die Sache
trifft: Ein Zähler, der um Mitternacht zurückgesetzt wird, zerschneidet keinen
Solartag, sondern liegt mitten in der Nacht mehrere Stunden vor dem ersten
Ertrag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.const import SUN_EVENT_SUNRISE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import CONSUMER_TYPE_MODULATING, FORECAST_MARGIN
from .models import ConsumerConfig, ConsumerRuntime
from .units import is_unavailable

# Umrechnungsfaktoren auf kWh, nach Kleinschreibung.
_ENERGY_FACTORS: dict[str, float] = {
    "wh": 1e-3,
    "kwh": 1.0,
    "mwh": 1e3,
}


def read_energy_kwh(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Liest einen Energiezähler und normalisiert ihn auf kWh.

    ``None``, sobald etwas nicht stimmt. Bewusst ohne Rückfall auf 0: Ein
    Leistungssensor statt eines Zählers ist ein Konfigurationsfehler, und als
    0 kWh gelesen sähe der Tageszähler aus, als hätte das Gerät nie gelaufen —
    die Automatik zöge daraus, dass es dringend laufen muss.
    """
    if not entity_id:
        return None

    state = hass.states.get(entity_id)
    if is_unavailable(state):
        return None
    assert state is not None

    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None

    unit = state.attributes.get("unit_of_measurement")
    if unit is None:
        return None
    factor = _ENERGY_FACTORS.get(unit.strip().lower())
    if factor is None:
        return None

    return value * factor


def solar_day_start(hass: HomeAssistant, now: float) -> float:
    """Beginn des laufenden Solartags als Zeitstempel.

    Der letzte Sonnenaufgang, nicht der nächste: Vor dem heutigen Aufgang läuft
    noch der Tag von gestern.

    In Polarnacht und Polartag gibt es keinen Sonnenaufgang; dann bleibt die
    örtliche Mitternacht. Das ist dort nicht schlechter, denn ein Solartag ist
    dort ohnehin kein sinnvoller Schnitt.
    """
    jetzt = dt_util.utc_from_timestamp(now)
    heute = dt_util.as_local(jetzt).date()

    aufgang = get_astral_event_date(hass, SUN_EVENT_SUNRISE, heute)
    if aufgang is None:
        return dt_util.start_of_local_day(jetzt).timestamp()

    if now >= aufgang.timestamp():
        return aufgang.timestamp()

    gestern = get_astral_event_date(hass, SUN_EVENT_SUNRISE, heute - timedelta(days=1))
    if gestern is None:
        return dt_util.start_of_local_day(jetzt).timestamp()
    return gestern.timestamp()


def target_kwh(consumer: ConsumerConfig, required_w: float) -> float:
    """Das Tagesziel in kWh, unabhängig davon, wie es eingetragen wurde.

    Bei einem regelbaren Verbraucher steht dort bereits eine Energie. Bei einem
    schaltbaren sind es Stunden, und die sind über die angenommene Leistung
    umzurechnen — mit **derselben Unsicherheit**, die auch die Ampel hat: Ohne
    eingetragene Nennleistung stammt der Wert aus der Statistik oder ist geraten.
    Deshalb weist ``required_source`` seine Herkunft aus.
    """
    if not consumer.daily_target or consumer.daily_target <= 0:
        return 0.0
    if consumer.consumer_type == CONSUMER_TYPE_MODULATING:
        return float(consumer.daily_target)
    return float(consumer.daily_target) * required_w / 1000.0


@dataclass(frozen=True, slots=True)
class DailyProgress:
    """Wie weit ein Verbraucher heute gekommen ist."""

    target_kwh: float
    done_kwh: float

    forecast_kwh: float | None
    """Verbleibende Prognose, oder ``None`` ohne brauchbaren Sensor."""

    @property
    def missing_kwh(self) -> float:
        return max(self.target_kwh - self.done_kwh, 0.0)

    @property
    def reached(self) -> bool:
        return self.missing_kwh <= 0

    @property
    def must_run(self) -> bool:
        """Reicht die Prognose nicht mehr für den Rest?

        Ohne Prognosesensor **nie**: Ein fehlender Wert ist kein Grund, ein Gerät
        auf Netzstrom laufen zu lassen. Er ist ein Konfigurationsfehler, und der
        gehört gemeldet statt in eine Entscheidung übersetzt.
        """
        if self.reached or self.forecast_kwh is None:
            return False
        return self.forecast_kwh < self.missing_kwh * FORECAST_MARGIN


def rebaseline(runtime: ConsumerRuntime, day_start: float, meter_kwh: float | None) -> None:
    """Setzt den Tageszähler auf den aktuellen Zählerstand.

    Zwei Anlässe, und der zweite ist der unauffällige: ein neuer Solartag, **und**
    ein Zählerstand unterhalb des gemerkten. Letzteres heißt, dass der Zähler
    zurückgesetzt wurde — bei einem Gerät, das vom Netz war, oder nach einem
    Austausch. Ohne diese Prüfung ergäbe die Differenz einen negativen Verbrauch,
    und das Tagesziel wäre für den Rest des Tages unerreichbar.
    """
    if meter_kwh is None:
        return
    neuer_tag = runtime.day_start_ts is None or runtime.day_start_ts < day_start
    zaehler_zurueck = runtime.day_start_kwh is not None and meter_kwh < runtime.day_start_kwh
    if neuer_tag or zaehler_zurueck:
        runtime.day_start_ts = day_start
        runtime.day_start_kwh = meter_kwh


def done_kwh(runtime: ConsumerRuntime, meter_kwh: float | None) -> float:
    """Was der Verbraucher seit dem Tagesbeginn verbraucht hat."""
    if meter_kwh is None or runtime.day_start_kwh is None:
        return 0.0
    return max(meter_kwh - runtime.day_start_kwh, 0.0)
