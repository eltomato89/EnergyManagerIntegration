"""Datenmodelle.

Die Feldnamen entsprechen denen der Energy Manager Card. Das ist Absicht: wer
beides einsetzt, soll dieselben Begriffe wiederfinden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .const import (
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_HYSTERESIS,
    CONF_LEVEL_HOLD,
    CONF_LEVEL_MAP,
    CONF_MAX_POWER,
    CONF_MIN_LEVEL_W,
    CONF_MIN_OFF_TIME,
    CONF_MIN_POWER,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_PHASES,
    CONF_POWER_ENTITY,
    CONF_SWITCH_ENTITY,
    CONF_TURN_OFF_DELAY,
    CONF_TURN_ON_DELAY,
    CONSUMER_TYPE_MODULATING,
    CONSUMER_TYPE_SWITCH,
)


def has_battery(data: dict[str, Any]) -> bool:
    """Ist überhaupt eine Batterie eingerichtet?

    Maßstab ist eine Entität, die etwas über die Batterie sagt. Reine
    Zahlenangaben wie Mindestladestand oder Ladeleistung genügen nicht: Ohne
    Messwert lässt sich weder erkennen, ob geladen wird, noch ob die Batterie
    voll ist.
    """
    return any(
        data.get(key)
        for key in (
            CONF_BATTERY_POWER_ENTITY,
            CONF_BATTERY_CHARGE_ENTITY,
            CONF_BATTERY_DISCHARGE_ENTITY,
            CONF_BATTERY_SOC_ENTITY,
        )
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
class Level:
    """Eine Leistungsstufe: was sie zieht und was dafür zu schreiben ist."""

    w: float
    """Leistung in Watt."""

    command: float | str
    """Der Wert für die Steuerentität — Ampere, Watt oder ein Optionsschlüssel.

    Getrennt von ``w`` gehalten, weil beides auseinanderfällt: Die Entscheidung
    fällt in Watt, geschrieben wird in der Sprache des Geräts.
    """


@dataclass(frozen=True, slots=True)
class Ladder:
    """Alle Stufen eines Verbrauchers, aufsteigend nach Leistung.

    Ohne die Null: Das Abschalten geht über ``switch_entity`` und ist keine
    Stufe. Bei einer Wallbox lässt sich über den Ladestrom ohnehin nicht
    beenden — sein Minimum liegt bei 6 A.

    Zusammengesetzt wird sie in ``ladder.py`` aus den Attributen der
    Steuerentität; hier steht nur, was man mit ihr tun kann.
    """

    levels: tuple[Level, ...]
    source: str
    """Woher das Raster stammt. Für die Anzeige, damit ein abgeleitetes Raster
    von einer eingetragenen Zuordnung zu unterscheiden ist."""

    @property
    def min_w(self) -> float:
        """Kleinste Stufe — was mindestens nötig ist, um überhaupt anzulaufen."""
        return self.levels[0].w

    @property
    def max_w(self) -> float:
        return self.levels[-1].w

    @property
    def count(self) -> int:
        return len(self.levels)

    def at_or_below(self, watts: float) -> Level | None:
        """Die höchste Stufe, die in ``watts`` noch hineinpasst.

        ``None``, wenn nicht einmal die kleinste passt — dann ist der
        Verbraucher nicht zu betreiben, und zwar unabhängig davon, ob er läuft.
        """
        found: Level | None = None
        for level in self.levels:
            if level.w <= watts:
                found = level
            else:
                break
        return found

    def nearest(self, watts: float) -> Level:
        """Die Stufe, die ``watts`` am nächsten kommt."""
        return min(self.levels, key=lambda level: abs(level.w - watts))

    def for_command(self, command: float | str) -> Level | None:
        """Die Stufe, die zu einem Wert der Steuerentität gehört.

        Bei einer Auswahlliste muss der Schlüssel genau passen — eine unbekannte
        Option wird nicht geraten. Bei einer Zahl wird die nächstgelegene Stufe
        genommen: Der gestellte Wert liegt nach einem Eingriff von Hand oder nach
        dem Ausdünnen des Rasters nicht zwangsläufig darauf.
        """
        if isinstance(command, str):
            return next((level for level in self.levels if level.command == command), None)
        return min(self.levels, key=lambda level: abs(float(level.command) - command))

    def index_of(self, level: Level) -> int:
        return self.levels.index(level)


@dataclass(frozen=True, slots=True)
class ConsumerConfig:
    """Konfiguration eines Verbrauchers, aus einem Subentry gelesen."""

    subentry_id: str
    name: str
    switch_entity: str
    power_entity: str | None = None

    consumer_type: str = CONSUMER_TYPE_SWITCH
    """Verhaltenstyp. Siehe ``CONSUMER_TYPE_SWITCH``."""

    control_entity: str | None = None
    phases: int = 1
    level_map: dict[str, float] | None = None
    min_level_w: float | None = None
    level_hold: int = 0

    min_power: float | None = None
    max_power: float | None = None
    hysteresis: float = 0.0

    min_runtime: int = 0
    min_off_time: int = 0
    turn_on_delay: int = 0
    turn_off_delay: int = 0

    @property
    def modulating(self) -> bool:
        """Lässt sich dieser Verbraucher in Stufen fahren?"""
        return self.consumer_type == CONSUMER_TYPE_MODULATING

    @classmethod
    def from_subentry(cls, subentry_id: str, data: dict[str, Any]) -> ConsumerConfig:
        """Liest die Konfiguration aus den Subentry-Daten.

        Der Rückfallwert des Verhaltenstyps entscheidet über das Verhalten nach
        einem Update: Bestehenden Subentries fehlt der Schlüssel, und
        ``switch`` lässt sie unverändert. Eine Migration ist damit nicht nötig.
        """
        return cls(
            subentry_id=subentry_id,
            name=data[CONF_NAME],
            switch_entity=data[CONF_SWITCH_ENTITY],
            power_entity=data.get(CONF_POWER_ENTITY),
            consumer_type=data.get(CONF_CONSUMER_TYPE, CONSUMER_TYPE_SWITCH),
            control_entity=data.get(CONF_CONTROL_ENTITY),
            phases=int(data.get(CONF_PHASES, 1) or 1),
            level_map=data.get(CONF_LEVEL_MAP),
            min_level_w=data.get(CONF_MIN_LEVEL_W),
            level_hold=data.get(CONF_LEVEL_HOLD, 0),
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
    """Zwangsfreigabe bis zu diesem Zeitpunkt.

    Gesetzt vom Dienst ``force_on``, aufgehoben von ``clear_force``. Solange
    sie läuft, lässt die Automatik den Verbraucher in Ruhe.
    """

    last_foreign_change: float | None = None
    """Wann zuletzt jemand anders als diese Integration geschaltet hat.

    Reine Diagnose — hier hängt keine Entscheidung daran. Der Wert beantwortet
    über einige Wochen die Frage, wie oft der Fall je Gerät tatsächlich
    auftritt. Ohne diese Zahl wäre jede Vorgabe für eine befristete
    Übersteuerung geraten, und bei taktenden Geräten wie einem
    Warmwasserspeicher ist eine Fehlerkennung der Regelfall statt der Ausnahme.

    Getrennt von ``last_switch_ts``, das den eigenen Schaltzeitpunkt hält: zwei
    Felder, zwei Zwecke, keine Überlappung.
    """

    last_foreign_to: bool | None = None
    """Wohin dabei geschaltet wurde."""

    last_level_ts: float | None = None
    """Zeitpunkt des letzten Stufenwechsels — Grundlage für ``level_hold``.

    Getrennt von ``last_switch_ts``: Eine Haltezeit zwischen zwei Stufen ist
    etwas anderes als eine Mindestlaufzeit, und ein Stufenwechsel soll die
    Sperrzeiten des Ein- und Ausschaltens nicht zurücksetzen.
    """

    last_level_w: float | None = None
    """Die zuletzt gestellte Stufe in Watt.

    Nur zur Nachvollziehbarkeit: Was tatsächlich gilt, wird bei jedem Durchlauf
    aus der Steuerentität gelesen — sie kann auch von Hand verstellt worden sein.
    """

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
            "last_foreign_change": self.last_foreign_change,
            "last_foreign_to": self.last_foreign_to,
            "last_level_ts": self.last_level_ts,
            "last_level_w": self.last_level_w,
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
            last_foreign_change=data.get("last_foreign_change"),
            last_foreign_to=data.get("last_foreign_to"),
            last_level_ts=data.get("last_level_ts"),
            last_level_w=data.get("last_level_w"),
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
    ladder, default. Ein geratener Wert soll als solcher erkennbar sein."""

    ladder: Ladder | None = None
    """Die erreichbaren Stufen, sofern der Verbraucher regelbar ist."""

    level: Level | None = None
    """Die Stufe, auf der er gerade steht — gelesen aus der Steuerentität."""

    target: Level | None = None
    """Die Stufe, auf der er laufen soll. ``None`` heißt: keine passt, also aus."""

    observed_max_w: float | None = None
    """Die höchste Leistung, die dieser Verbraucher seit dem Einschalten erreicht hat.

    Nicht dasselbe wie der Sollwert: Ein Fahrzeug lädt mit 10 A, obwohl 16 A
    angeboten sind. ``None``, solange nichts beobachtet wurde oder kein
    Leistungssensor vorliegt.
    """

    level_capped: bool = False
    """Wurde die Leiter deswegen beschnitten?

    Die Antwort auf „warum geht sie nicht höher". Ohne diesen Ausweis sähe eine
    gekappte Leiter aus wie ein Gerät mit weniger Stufen.
    """

    displaceable: tuple[str, ...] = ()
    """Laufende Verbraucher niedrigerer Priorität, die für diesen weichen könnten.

    Leer, solange das nicht nötig oder nicht möglich ist. Gefüllt nur, wenn ihre
    zusammen freiwerdende Leistung tatsächlich reicht — sonst würde man
    abschalten und trotzdem nichts gewinnen.

    Ohne diesen Weg bliebe die Priorität eine bloße Reihenfolge beim Zuschalten:
    Zwei kleine Verbraucher könnten einen großen mit höherem Rang dauerhaft
    aussperren, egal wie weit der Überschuss steigt.
    """

    @property
    def step_up(self) -> bool:
        """Soll die Stufe angehoben werden?

        Verglichen wird mit der **gestellten** Stufe, nicht mit dem Messwert: Der
        Sollwert ist das, was diese Integration steuert.

        Auch dann wahr, wenn gar keine Stufe gelesen werden konnte, der
        Verbraucher aber läuft — dann ist der Sollwert überhaupt erst zu setzen.
        Das ist der Fall einer Auswahlliste, die auf „aus" steht, während der
        Schalter an ist.
        """
        if not self.is_on or self.target is None:
            return False
        return self.level is None or self.target.w > self.level.w

    @property
    def step_down(self) -> bool:
        """Soll die Stufe gesenkt werden?

        Nur bei bekannter aktueller Stufe: Ohne sie gibt es nichts zu senken.
        """
        if not self.is_on or self.target is None or self.level is None:
            return False
        return self.target.w < self.level.w


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
    """Obergrenze dessen, was die Batterie insgesamt aus dem Überschuss zieht."""

    reserve_w: float = 0.0
    """Bereits vor der Reihenfolge abgezogene Leistung (``battery_reserve_w``).

    Sie zählt auf die Ladeleistung an, statt zu ihr hinzuzukommen: Die Reserve
    greift in :func:`~.surplus.apply_reserve` noch vor dem ersten Verbraucher,
    die Batterie hat sie an ihrem Rang also schon sicher. Ohne diese Anrechnung
    stünden zwei Felder nebeneinander, die dasselbe meinen, und die Batterie
    bekäme beides.
    """

    soc: float | None = None
    """Ladestand in Prozent, oder None ohne Sensor."""

    charging_w: float | None = None
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
    """Insgesamt für die Batterie zurückgelegte Leistung, höchstens ``max_charge_w``.

    Enthält die Reserve, die schon vor der Reihenfolge abgezogen wurde. Der Teil,
    den die Batterie tatsächlich aus dem Budget der Reihenfolge nimmt, ist
    ``claim_w`` abzüglich der Reserve.
    """

    charging_w: float | None
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
