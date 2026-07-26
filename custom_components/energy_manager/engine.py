"""Bewertung der Verbraucher und Schaltentscheidung.

Die Ampellogik ist eine Portierung von ``src/lib/device-status.ts`` der Karte —
mit **einer bewussten Abweichung**, siehe :func:`build_views`.

Die Schaltentscheidung (:func:`decide`) kommt hinzu: sie wertet die vier
Zeitfelder aus und liefert höchstens **eine** Aktion je Durchlauf.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CLOSE_THRESHOLD_RATIO, DEFAULT_REQUIRED_W
from .models import ConsumerConfig, ConsumerRuntime, ConsumerView, DeviceStatus
from .units import is_on, is_unavailable, read_power_w, round_w

if TYPE_CHECKING:
    from .coordinator import EnergyManagerCoordinator


def resolve_required_w(consumer: ConsumerConfig, power_w: float | None) -> float:
    """Wie viel Leistung ein Verbraucher voraussichtlich zieht.

    Reihenfolge: ausdrückliche Einschaltschwelle, dann Nennleistung, dann der
    aktuell gemessene Wert, zuletzt ein konservativer Vorgabewert.
    """
    if consumer.min_power is not None and consumer.min_power > 0:
        return float(consumer.min_power)
    if consumer.max_power is not None and consumer.max_power > 0:
        return float(consumer.max_power)
    if power_w is not None and power_w > 0:
        return power_w
    return float(DEFAULT_REQUIRED_W)


def order_consumers(
    consumers: dict[str, ConsumerConfig],
    priorities: dict[str, float],
) -> list[ConsumerConfig]:
    """Sortiert nach Priorität, 1 = höchste.

    Bei gleichem Wert entscheidet der Name, damit die Reihenfolge nicht bei
    jedem Durchlauf springt.
    """
    return sorted(
        consumers.values(),
        key=lambda c: (priorities.get(c.subentry_id, 999.0), c.name),
    )


def anticipated_w(runtimes: dict[str, ConsumerRuntime], now: float) -> float:
    """Summe der gerade geschalteten Leistung, die im Messwert noch fehlt.

    Positiv, wenn per Saldo zugeschaltet wurde — dieser Betrag ist vom
    gemessenen Überschuss abzuziehen, weil der Zähler ihn noch nicht zeigt.

    Ohne diese Korrektur reagiert die Automatik auf ihre eigene Wirkung: Sie
    schaltet 2 kW zu, sieht sekundenlang unverändert 2 kW Überschuss und
    schaltet weiter. Bei mehreren Verbrauchern reicht das Beruhigungsfenster
    allein nicht, denn es schützt nur den gerade geschalteten.
    """
    total = 0.0
    for runtime in runtimes.values():
        if runtime.settle_until is not None and now < runtime.settle_until:
            total += runtime.anticipated_w
    return total


def compute_lock(
    consumer: ConsumerConfig,
    runtime: ConsumerRuntime,
    is_currently_on: bool,
    now: float,
) -> tuple[float | None, str | None]:
    """Ende der aktiven Sperre und ihre Art.

    Grundlage ist der **selbst festgehaltene** Schaltzeitpunkt, nicht
    ``last_changed`` der Entität: das wird durch manuelles Schalten und durch
    einen Neustart zurückgesetzt und wäre als Sperrgrundlage wertlos.
    """
    if runtime.last_switch_ts is None:
        return None, None

    limit = consumer.min_runtime if is_currently_on else consumer.min_off_time
    if not limit or limit <= 0:
        return None, None

    # Die Sperre gilt nur für die Richtung, in die zuletzt geschaltet wurde.
    if runtime.last_switch_to is not None and runtime.last_switch_to != is_currently_on:
        return None, None

    until = runtime.last_switch_ts + limit
    if until <= now:
        return None, None

    return until, "min_runtime" if is_currently_on else "min_off_time"


def build_views(
    hass: HomeAssistant,
    coordinator: EnergyManagerCoordinator,
    available_w: float | None,
) -> list[ConsumerView]:
    """Bewertet alle Verbraucher in Prioritätsreihenfolge.

    **Abweichung von der Karte:** Dort reserviert jeder Verbraucher mit Status
    ``off_ready`` Budget, auch wenn er nicht an der Automatik teilnimmt. Für die
    reine Anzeige ist das richtig. Für die Schaltentscheidung wäre es falsch —
    ein manuell verwalteter Verbraucher würde Budget blockieren, das die
    Automatik vergeben könnte. Der gemeldete Status bleibt identisch, nur die
    Verteilung unterscheidet sich.
    """
    now = dt_util.utcnow().timestamp()
    priorities = coordinator.priorities()
    budget = available_w
    views: list[ConsumerView] = []

    for rank, consumer in enumerate(order_consumers(coordinator.consumers, priorities)):
        state = hass.states.get(consumer.switch_entity)
        entity_available = state is not None and not is_unavailable(state)
        currently_on = entity_available and is_on(state)

        managed = coordinator.is_managed(consumer.subentry_id)

        power = read_power_w(hass, consumer.power_entity)
        power_w = None if power.w is None else round_w(power.w)
        required_w = resolve_required_w(consumer, power_w)

        if not entity_available or budget is None:
            status = DeviceStatus.UNAVAILABLE
        elif currently_on:
            # Laufende Verbraucher verbrauchen kein Budget: ihr Verbrauch ist im
            # gemessenen Überschuss bereits enthalten, sie würden doppelt zählen.
            status = (
                DeviceStatus.ON_OK
                if budget >= -(consumer.hysteresis or 0)
                else DeviceStatus.ON_DEFICIT
            )
        elif budget >= required_w:
            status = DeviceStatus.OFF_READY
            if managed:
                budget -= required_w
        elif budget >= required_w * CLOSE_THRESHOLD_RATIO:
            status = DeviceStatus.OFF_CLOSE
        else:
            status = DeviceStatus.OFF_INSUFFICIENT

        locked_until, lock_kind = compute_lock(
            consumer, coordinator.runtime_for(consumer.subentry_id), currently_on, now
        )

        views.append(
            ConsumerView(
                config=consumer,
                rank=rank,
                is_on=currently_on,
                available=entity_available,
                managed=managed,
                power_w=power_w,
                required_w=required_w,
                status=status,
                headroom_w=budget,
                locked_until=locked_until,
                lock_kind=lock_kind,
            )
        )

    assign_displaceable(views, coordinator, now)
    return views


def displaced_power(view: ConsumerView) -> float:
    """Wie viel Leistung frei wird, wenn dieser Verbraucher weicht.

    Der gemessene Wert, nicht der geschätzte Bedarf: Was tatsächlich fließt,
    ist auch das, was zurückkommt. Ohne Leistungssensor bleibt nur die
    Schätzung.
    """
    if view.power_w is not None and view.power_w > 0:
        return view.power_w
    return view.required_w


def may_be_displaced(view: ConsumerView, runtime: ConsumerRuntime, now: float) -> bool:
    """Darf dieser laufende Verbraucher für einen wichtigeren weichen?

    Die Ausnahmen sind dieselben, die auch sonst vor einer Schaltung schützen —
    eine Verdrängung ist kein Freibrief, die Mindestlaufzeit zu übergehen.
    """
    if not view.is_on or not view.available:
        return False
    if not view.managed:
        return False
    if is_forced(runtime, now):
        return False
    if runtime.settle_until is not None and now < runtime.settle_until:
        return False
    # Mindestlaufzeit: ein angefangener Waschgang wird nicht abgebrochen.
    return not (view.locked_until is not None and now < view.locked_until)


def assign_displaceable(
    views: list[ConsumerView],
    coordinator: EnergyManagerCoordinator,
    now: float,
) -> None:
    """Bestimmt je Verbraucher, wer für ihn weichen müsste.

    Nur für ausgeschaltete, die es aus eigener Kraft nicht schaffen. Gesammelt
    wird von der niedrigsten Priorität aufwärts und nur so viel wie nötig — wer
    am wenigsten wichtig ist, weicht zuerst, und niemand wird ohne Not
    abgeschaltet.
    """
    for index, view in enumerate(views):
        if view.is_on or not view.available or not view.managed:
            continue
        if view.status is DeviceStatus.OFF_READY:
            continue  # Braucht niemanden zu verdrängen.
        if view.headroom_w is None:
            continue

        fehlend = view.required_w - view.headroom_w
        if fehlend <= 0:
            continue

        opfer: list[str] = []
        gewinn = 0.0
        # Von hinten: die unwichtigsten zuerst.
        for kandidat in reversed(views[index + 1 :]):
            runtime = coordinator.runtime_for(kandidat.config.subentry_id)
            if not may_be_displaced(kandidat, runtime, now):
                continue
            opfer.append(kandidat.config.subentry_id)
            gewinn += displaced_power(kandidat)
            if gewinn >= fehlend:
                break

        # Nur übernehmen, wenn es am Ende auch reicht. Sonst hätte man
        # abgeschaltet und trotzdem nichts gewonnen.
        if gewinn >= fehlend:
            view.displaceable = tuple(opfer)


# --- Schaltentscheidung -----------------------------------------------------


class Blocker(StrEnum):
    """Warum eine an sich sinnvolle Schaltung unterbleibt.

    Wird protokolliert und in den Status-Attributen ausgewiesen — ohne diese
    Begründung wirkt eine ausbleibende Schaltung wie ein Fehler.
    """

    NOT_MANAGED = "not_managed"
    UNAVAILABLE = "unavailable"
    SETTLING = "settling"
    FORCED = "forced"
    """Zwangsfreigabe läuft — die Automatik hält sich fern."""
    MIN_RUNTIME = "min_runtime"
    MIN_OFF_TIME = "min_off_time"
    TURN_ON_DELAY = "turn_on_delay"
    TURN_OFF_DELAY = "turn_off_delay"


@dataclass(frozen=True, slots=True)
class Decision:
    """Was mit einem Verbraucher geschehen soll."""

    subentry_id: str
    turn_on: bool
    reason: str

    displaces: tuple[str, ...] = ()
    """Verbraucher, die dafür weichen müssen.

    Zusammen mit dem Einschalten **eine** Handlung: Erst abschalten, dann
    einschalten, im selben Durchlauf. Verteilt auf mehrere Durchläufe könnte
    dazwischen ein anderer den frei gewordenen Überschuss belegen — dann wären
    die einen aus und der andere trotzdem nicht an.
    """


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Ergebnis eines Durchlaufs."""

    action: Decision | None = None
    blockers: dict[str, Blocker] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.blockers is None:
            object.__setattr__(self, "blockers", {})


def update_conditions(
    view: ConsumerView,
    runtime: ConsumerRuntime,
    now: float,
) -> None:
    """Schreibt fort, seit wann eine Bedingung ununterbrochen gilt.

    Der Kern der Verzögerungslogik: Sobald die Bedingung einmal nicht erfüllt
    ist, beginnt die Zeit von vorn. Nur so bedeutet "ununterbrochen" auch
    ununterbrochen — ein Zähler, der bei jeder Lücke stehen bliebe, würde die
    Verzögerung nach ein paar Wolken wirkungslos machen.

    "Einschaltbereit" heißt dabei auch: bereit, sobald niedriger priorisierte
    Verbraucher weichen. Ohne das liefe der Zähler für einen Verdränger nie an,
    und seine Einschaltverzögerung begänne erst nach dem Abschalten der anderen.
    """
    wants_on = not view.is_on and (view.status is DeviceStatus.OFF_READY or bool(view.displaceable))
    wants_off = view.is_on and view.status is DeviceStatus.ON_DEFICIT

    if wants_on:
        if runtime.on_condition_since is None:
            runtime.on_condition_since = now
    else:
        runtime.on_condition_since = None

    if wants_off:
        if runtime.off_condition_since is None:
            runtime.off_condition_since = now
    else:
        runtime.off_condition_since = None


def check_blockers(
    view: ConsumerView,
    runtime: ConsumerRuntime,
    now: float,
) -> Blocker | None:
    """Prüft, was gegen eine Schaltung spricht — unabhängig von der Richtung."""
    if not view.managed:
        return Blocker.NOT_MANAGED
    if not view.available:
        return Blocker.UNAVAILABLE
    if runtime.settle_until is not None and now < runtime.settle_until:
        return Blocker.SETTLING
    return None


def is_forced(runtime: ConsumerRuntime, now: float) -> bool:
    """Läuft für diesen Verbraucher gerade eine Zwangsfreigabe?"""
    return runtime.force_until is not None and now < runtime.force_until


def decide_for(
    view: ConsumerView,
    runtime: ConsumerRuntime,
    now: float,
) -> tuple[Decision | None, Blocker | None]:
    """Entscheidet für einen einzelnen Verbraucher."""
    # Zwangsfreigabe zuerst: Sie ist eine ausdrückliche Anweisung des Nutzers
    # und hat Vorrang vor jeder Wirtschaftlichkeitsrechnung. Eingeschaltet wird
    # dabei nicht hier — das tut der Service sofort. Die Automatik hält sich in
    # dieser Zeit nur davon fern, es wieder abzuschalten.
    if is_forced(runtime, now):
        return None, Blocker.FORCED

    if (blocker := check_blockers(view, runtime, now)) is not None:
        return None, blocker

    consumer = view.config

    # Einschalten: Der Überschuss muss lange genug gereicht haben.
    if runtime.on_condition_since is not None:
        elapsed = now - runtime.on_condition_since
        if elapsed < consumer.turn_on_delay:
            return None, Blocker.TURN_ON_DELAY
        # Sperre aus der letzten Ausschaltung.
        if view.locked_until is not None and now < view.locked_until:
            return None, Blocker.MIN_OFF_TIME

        if view.displaceable:
            return (
                Decision(
                    consumer.subentry_id,
                    True,
                    "displaces_lower_priority",
                    displaces=view.displaceable,
                ),
                None,
            )
        return Decision(consumer.subentry_id, True, "surplus_sufficient"), None

    # Ausschalten: Das Defizit muss lange genug angehalten haben.
    if runtime.off_condition_since is not None:
        elapsed = now - runtime.off_condition_since
        if elapsed < consumer.turn_off_delay:
            return None, Blocker.TURN_OFF_DELAY
        if view.locked_until is not None and now < view.locked_until:
            return None, Blocker.MIN_RUNTIME
        return Decision(consumer.subentry_id, False, "deficit_persists"), None

    return None, None


def decide(
    views: list[ConsumerView],
    runtimes: dict[str, ConsumerRuntime],
    now: float,
) -> Evaluation:
    """Wählt höchstens **eine** Aktion aus.

    Nur eine je Durchlauf, weil jede Schaltung den Überschuss verändert, auf den
    die nächste Entscheidung sich stützen würde. Wer drei Geräte gleichzeitig
    zuschaltet, rechnet dreimal mit demselben Budget.

    Ausschalten hat Vorrang vor Einschalten: ein anhaltendes Defizit zu beenden
    ist dringender, als zusätzlichen Überschuss zu nutzen.
    """
    blockers: dict[str, Blocker] = {}
    turn_on: Decision | None = None
    turn_off: Decision | None = None

    for view in views:
        runtime = runtimes.setdefault(view.config.subentry_id, ConsumerRuntime())
        update_conditions(view, runtime, now)

        decision, blocker = decide_for(view, runtime, now)
        if blocker is not None:
            blockers[view.config.subentry_id] = blocker
        if decision is None:
            continue

        # views ist bereits nach Priorität sortiert; der erste Treffer gewinnt.
        if decision.turn_on and turn_on is None:
            turn_on = decision
        elif not decision.turn_on and turn_off is None:
            turn_off = decision

    return Evaluation(action=turn_off or turn_on, blockers=blockers)
