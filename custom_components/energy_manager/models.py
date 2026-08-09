"""Datenmodelle.

Die Feldnamen entsprechen denen der Energy Manager Card. Das ist Absicht: wer
beides einsetzt, soll dieselben Begriffe wiederfinden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .const import (
    CONF_HYSTERESIS,
    CONF_MAX_POWER,
    CONF_MIN_OFF_TIME,
    CONF_MIN_POWER,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SWITCH_ENTITY,
    CONF_TURN_OFF_DELAY,
    CONF_TURN_ON_DELAY,
)


class ReadingReason(StrEnum):
    """Warum ein Messwert nicht verwertbar ist."""

    MISSING = "missing"
    """Entität nicht konfiguriert oder nicht in hass.states vorhanden."""

    UNAVAILABLE = "unavailable"
    """Zustand ist unavailable/unknown/leer."""

    NAN = "nan"
    """Zustand lässt sich nicht in eine endliche Zahl wandeln."""

    WRONG_UNIT = "wrong_unit"
    """Einheit misst keine Leistung — meist ein kWh-Zähler statt eines W-Sensors."""


@dataclass(frozen=True, slots=True)
class Reading:
    """Ein auf Watt normalisierter Messwert."""

    w: float | None
    reason: ReadingReason | None = None
    assumed_unit: bool = False
    """unit_of_measurement fehlte, W wurde angenommen."""

    unit: str | None = None

    @property
    def ok(self) -> bool:
        return self.w is not None


class SurplusError(StrEnum):
    """Warum sich der Überschuss nicht berechnen lässt."""

    MISSING_GRID = "missing_grid"
    MISSING_PRODUCTION = "missing_production"
    MISSING_CONSUMPTION = "missing_consumption"
    GRID_UNAVAILABLE = "grid_unavailable"
    PRODUCTION_UNAVAILABLE = "production_unavailable"
    CONSUMPTION_UNAVAILABLE = "consumption_unavailable"
    WRONG_UNIT = "wrong_unit"


@dataclass(frozen=True, slots=True)
class SurplusResult:
    """Ergebnis der Überschussberechnung."""

    raw: float | None
    """W vor Reserve; None = nicht berechenbar."""

    available: float | None
    """W nach Reserve und Ladestandsregel.

    Negativ bedeutet ein Defizit gegenüber der Erzeugung — **nicht**
    zwangsläufig Netzbezug in gleicher Höhe, denn die Batterie kann einen Teil
    davon stützen. Für den tatsächlichen Zählerwert siehe ``grid_w``.
    """

    battery_correction: float = 0.0
    grid_w: float | None = None
    battery_w: float | None = None
    degraded: bool = False
    """Batterie konfiguriert, liefert aber keinen Wert."""

    errors: tuple[SurplusError, ...] = ()

    anticipated_w: float = 0.0
    """Bereits von ``available`` abgezogene, noch nicht gemessene Last.

    Nur zur Nachvollziehbarkeit: Weicht der angezeigte Überschuss kurz nach
    einer Schaltung vom Zähler ab, steht hier warum.
    """

    @property
    def usable(self) -> bool:
        """Darf auf dieser Grundlage geschaltet werden?"""
        return self.available is not None and not self.errors


class DeviceStatus(StrEnum):
    """Ampelzustand eines Verbrauchers. Werte identisch zur Karte."""

    ON_OK = "on_ok"
    ON_DEFICIT = "on_deficit"
    OFF_READY = "off_ready"
    OFF_CLOSE = "off_close"
    OFF_INSUFFICIENT = "off_insufficient"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ConsumerConfig:
    """Konfiguration eines Verbrauchers, aus einem Subentry gelesen."""

    subentry_id: str
    name: str
    switch_entity: str
    power_entity: str | None = None

    min_power: float | None = None
    max_power: float | None = None
    hysteresis: float = 0.0

    min_runtime: int = 0
    min_off_time: int = 0
    turn_on_delay: int = 0
    turn_off_delay: int = 0

    # --- Später, siehe Plan "Vorgesehen, aber jetzt nicht gebaut" ------------
    # Ohne diese Felder müsste die Schaltentscheidung später umgebaut werden;
    # mit ihnen bleibt die Erweiterung additiv.

    steps: tuple[int, ...] | None = None
    """Leistungsstufen in W für regelbare Verbraucher. Noch ohne Funktion."""

    step_entity: str | None = None
    """Entität, die die Stufe setzt. Noch ohne Funktion."""

    window_start: str | None = None
    """Beginn des erlaubten Zeitfensters (HH:MM). Noch ohne Funktion."""

    window_end: str | None = None
    """Ende des erlaubten Zeitfensters (HH:MM). Noch ohne Funktion."""

    @classmethod
    def from_subentry(cls, subentry_id: str, data: dict[str, Any]) -> ConsumerConfig:
        """Liest die Konfiguration aus den Subentry-Daten."""
        return cls(
            subentry_id=subentry_id,
            name=data[CONF_NAME],
            switch_entity=data[CONF_SWITCH_ENTITY],
            power_entity=data.get(CONF_POWER_ENTITY),
            min_power=data.get(CONF_MIN_POWER),
            max_power=data.get(CONF_MAX_POWER),
            hysteresis=data.get(CONF_HYSTERESIS, 0.0),
            min_runtime=data.get(CONF_MIN_RUNTIME, 0),
            min_off_time=data.get(CONF_MIN_OFF_TIME, 0),
            turn_on_delay=data.get(CONF_TURN_ON_DELAY, 0),
            turn_off_delay=data.get(CONF_TURN_OFF_DELAY, 0),
        )


@dataclass
class ConsumerRuntime:
    """Laufzeitzustand eines Verbrauchers.

    Wird persistiert, weil sich die Zeitfelder sonst nicht verlässlich
    durchsetzen lassen: ``last_changed`` der Schalt-Entität wird durch manuelles
    Schalten und durch einen Neustart zurückgesetzt, und über die
    Verzögerungen sagt es prinzipbedingt nichts aus — die beziehen sich auf die
    *Bedingung*, nicht auf den Schaltzustand.
    """

    priority: float = 5.0
    """Rang, 1 = höchste. Wird über die number-Entität bedient."""

    managed: bool = True
    """Nimmt an der Automatik teil. Wird über die switch-Entität bedient."""

    last_switch_ts: float | None = None
    """Zeitpunkt der letzten von DIESER Integration ausgelösten Schaltung."""

    last_switch_to: bool | None = None
    """Wohin zuletzt geschaltet wurde."""

    on_condition_since: float | None = None
    """Seit wann die Einschaltbedingung ununterbrochen erfüllt ist."""

    off_condition_since: float | None = None
    """Seit wann die Ausschaltbedingung ununterbrochen erfüllt ist."""

    settle_until: float | None = None
    """Bis dahin wird dieser Verbraucher nicht angefasst."""

    anticipated_w: float = 0.0
    """Gerade geschaltete Leistung, die im Messwert noch nicht steckt.

    Positiv nach dem Ein-, negativ nach dem Ausschalten. Gilt bis
    ``settle_until`` und wird bis dahin vom Überschuss abgezogen.

    Ohne das reagiert die Automatik auf ihre eigene Wirkung: Zwischen dem
    Zuschalten und dem Zeitpunkt, an dem Zähler und Glättung das zeigen,
    vergehen Sekunden bis Minuten — in denen derselbe Überschuss ein zweites
    Mal vergeben würde.
    """

    force_until: float | None = None
    """Zwangsfreigabe bis zu diesem Zeitpunkt. Noch ohne Funktion."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "managed": self.managed,
            "last_switch_ts": self.last_switch_ts,
            "last_switch_to": self.last_switch_to,
            "on_condition_since": self.on_condition_since,
            "off_condition_since": self.off_condition_since,
            "settle_until": self.settle_until,
            "anticipated_w": self.anticipated_w,
            "force_until": self.force_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsumerRuntime:
        return cls(
            priority=data.get("priority", 5.0),
            managed=data.get("managed", True),
            last_switch_ts=data.get("last_switch_ts"),
            last_switch_to=data.get("last_switch_to"),
            on_condition_since=data.get("on_condition_since"),
            off_condition_since=data.get("off_condition_since"),
            settle_until=data.get("settle_until"),
            anticipated_w=data.get("anticipated_w", 0.0),
            force_until=data.get("force_until"),
        )


@dataclass(slots=True)
class ConsumerView:
    """Bewerteter Verbraucher — Grundlage für Anzeige und Entscheidung."""

    config: ConsumerConfig
    rank: int
    """Position in der Prioritätsreihenfolge, 0 = höchste."""

    is_on: bool = False
    available: bool = False
    managed: bool = True
    power_w: float | None = None
    required_w: float = 0.0
    status: DeviceStatus = DeviceStatus.UNAVAILABLE
    headroom_w: float | None = None
    locked_until: float | None = None
    lock_kind: str | None = None

    required_source: str = "default"
    """Woher ``required_w`` stammt: min_power, max_power, measured, estimated,
    default. Ein geratener Wert soll als solcher erkennbar sein."""

    displaceable: tuple[str, ...] = ()
    """Laufende Verbraucher niedrigerer Priorität, die für diesen weichen könnten.

    Leer, solange das nicht nötig oder nicht möglich ist. Gefüllt nur, wenn ihre
    zusammen freiwerdende Leistung tatsächlich reicht — sonst würde man
    abschalten und trotzdem nichts gewinnen.

    Ohne diesen Weg bliebe die Priorität eine bloße Reihenfolge beim Zuschalten:
    Zwei kleine Verbraucher könnten einen großen mit höherem Rang dauerhaft
    aussperren, egal wie weit der Überschuss steigt.
    """


@dataclass(frozen=True, slots=True)
class BatteryLoad:
    """Die Hausbatterie als verschiebbare Last.

    Nur befüllt, wenn eine Batterie konfiguriert ist **und** eine maximale
    Ladeleistung eingetragen wurde. Ohne beides ist die Batterie wie bisher nur
    ein Korrekturterm in der Überschussformel und nimmt an der
    Prioritätsreihenfolge nicht teil.
    """

    priority: float
    """Rang, 1 = höchste. Wird über die ``battery_priority``-number bedient."""

    max_charge_w: float
    """Höchste Ladeleistung, die die Batterie an ihrem Rang reserviert."""

    soc: float | None
    """Ladestand in Prozent, oder None ohne Sensor."""

    charging_w: float | None
    """Gemessene Ladeleistung (>0 lädt), oder None."""


@dataclass(slots=True)
class BatteryView:
    """Bewertete Batterie-Last — Grundlage für die Batterie-Zeile der Karte.

    Die Batterie wird **nicht** geschaltet (dazu fehlt der Integration ein
    Steuerweg); sie belegt nur an ihrem Rang Budget, das tiefer priorisierten
    Verbrauchern damit nicht mehr zur Verfügung steht. Höher priorisierte
    Verbraucher werden vor ihr bedient und bleiben eingeschaltet.
    """

    rank: int
    """Position in der gemeinsamen Reihenfolge, 0 = höchste."""

    priority: float
    max_charge_w: float
    claim_w: float
    """Tatsächlich reservierte Ladeleistung, ``min(max_charge_w, Budget)``."""

    charging_w: float | None
    soc: float | None
    status: DeviceStatus
    headroom_w: float | None
    """Budget, das nach der Batterie noch für tiefere Verbraucher bleibt."""

    full: bool
    """Ladestand am oberen Anschlag — die Batterie reserviert dann nichts."""


@dataclass(slots=True)
class ManagerState:
    """Was der Koordinator an die Entitäten weitergibt."""

    surplus: SurplusResult
    consumers: list[ConsumerView] = field(default_factory=list)
    battery: BatteryView | None = None
    """Die Batterie als verschiebbare Last, sofern sie mitspielt."""
    coverage: float = 0.0
    """Anteil des Mittelungsfensters mit gültigen Daten, 0..1."""

    started: bool = False
    """Home Assistant ist durchgestartet; alle Zustände liegen vor."""

    may_switch: bool = False
    """Alle Bedingungen zum Schalten sind erfüllt.

    Getrennt von ``started``, weil die Gründe verschieden sind und der Nutzer
    sie unterscheiden können muss: "startet noch" ist vorübergehend,
    "pausiert" ist eine Entscheidung, "sensor_error" ein Fehler.
    """

    blockers: dict[str, str] = field(default_factory=dict)
    """Je Verbraucher der Grund, warum eine sinnvolle Schaltung unterbleibt.

    Ohne diese Begründung sieht eine ausbleibende Schaltung wie ein Fehler aus —
    die häufigste Rückfrage bei jeder Überschusssteuerung.
    """
