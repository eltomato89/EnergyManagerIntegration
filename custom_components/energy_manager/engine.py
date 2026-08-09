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

from .const import BATTERY_FULL_SOC, CLOSE_THRESHOLD_RATIO, DEFAULT_REQUIRED_W, STANDBY_W
from .models import (
    BatteryLoad,
    BatteryView,
    ConsumerConfig,
    ConsumerRuntime,
    ConsumerView,
    DeviceStatus,
)
from .units import is_on, is_unavailable, read_power_w, round_w

if TYPE_CHECKING:
    from .coordinator import EnergyManagerCoordinator


def resolve_required_w(
    consumer: ConsumerConfig,
    power_w: float | None,
    estimated_w: float | None = None,
) -> float:
    """Wie viel Leistung ein Verbraucher voraussichtlich zieht.

    Reihenfolge, von der verlässlichsten Quelle zur schwächsten:

    1. **Einschaltschwelle** — die ausdrückliche Ansage des Nutzers.
    2. **Nennleistung** — ebenfalls eingetragen.
    3. **Ist-Wert**, wenn das Gerät läuft und dabei mehr als
       Bereitschaftsleistung zieht. Dann ist die Frage ohnehin beantwortet.
    4. **Geschätzt** aus der aufgezeichneten Statistik. Greift genau im
       kritischen Fall: Gerät aus, nichts eingetragen — und trotzdem muss
       entschieden werden, ob der Überschuss reicht.
    5. **Vorgabewert.** Ein Notnagel, mehr nicht.

    Der Bereitschaftsbetrieb ist bewusst ausgenommen, siehe ``STANDBY_W``: Ein
    Gerät, dessen Schalter an ist und das dabei zwei Watt zieht, ist nicht mit
    zwei Watt zuschaltbar.
    """
    if consumer.min_power is not None and consumer.min_power > 0:
        return float(consumer.min_power)
    if consumer.max_power is not None and consumer.max_power > 0:
        return float(consumer.max_power)
    if power_w is not None and power_w >= STANDBY_W:
        return power_w
    if estimated_w is not None and estimated_w > 0:
        return float(estimated_w)
    return float(DEFAULT_REQUIRED_W)


def required_source(
    consumer: ConsumerConfig,
    power_w: float | None,
    estimated_w: float | None = None,
) -> str:
    """Woher der Bedarfswert stammt — für die Anzeige.

    Ohne diese Angabe ist ein geratener Wert nicht von einem eingetragenen zu
    unterscheiden, und niemand käme auf die Idee, die Nennleistung nachzutragen.
    """
    if consumer.min_power is not None and consumer.min_power > 0:
        return "min_power"
    if consumer.max_power is not None and consumer.max_power > 0:
        return "max_power"
    if power_w is not None and power_w >= STANDBY_W:
        return "measured"
    if estimated_w is not None and estimated_w > 0:
        return "estimated"
    return "default"


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


def battery_claim(
    battery: BatteryLoad,
    budget: float | None,
) -> tuple[float, float, DeviceStatus, bool]:
    """Wie viel Überschuss die Batterie bekommt.

    Gibt zwei Beträge zurück: was an ihrem **Rang** aus dem Budget genommen wird
    und was ihr **insgesamt** zusteht. Die beiden fallen auseinander, sobald eine
    Batteriereserve eingetragen ist: Die greift schon vor dem ersten Verbraucher,
    die Batterie hat sie an ihrem Rang also bereits sicher. Angerechnet statt
    addiert, sonst bekäme sie beides und zwei Felder mit derselben Bedeutung
    zögen den Verbrauchern doppelt Leistung ab.

    Die Batterie ist eine gierige Last: Sie fordert bis zu ihrer maximalen
    Ladeleistung, aber nie mehr als gerade übrig ist. So sinkt der Anspruch bei
    Netzbezug von selbst auf 0 — die Batterie drängt tiefer priorisierte
    Verbraucher nur dann heraus, wenn tatsächlich Überschuss zu holen ist, und
    schiebt niemanden ohne Not ins Defizit.

    Ist der Ladestand am Anschlag, fordert sie nichts mehr an: Was sie nicht mehr
    aufnimmt, gehört wieder den tieferen Verbrauchern.
    """
    full = battery.soc is not None and battery.soc >= BATTERY_FULL_SOC
    # Was über die Ladeleistung hinausgeht, ist keine Reserve für die Batterie
    # mehr, sondern schlicht zu viel; als Anrechnung zählt höchstens die
    # Ladeleistung selbst.
    gesichert = min(battery.reserve_w, battery.max_charge_w)

    if budget is None:
        return 0.0, gesichert, DeviceStatus.UNAVAILABLE, full
    if full:
        return 0.0, gesichert, DeviceStatus.ON_OK, True

    offen = max(battery.max_charge_w - gesichert, 0.0)
    aus_budget = min(offen, max(budget, 0.0))
    gesamt = gesichert + aus_budget

    if gesamt <= 0:
        # Höher priorisierte Verbraucher haben alles belegt (oder es herrscht
        # Defizit): die Batterie wartet.
        status = DeviceStatus.OFF_INSUFFICIENT
    elif gesamt >= battery.max_charge_w:
        # Volle Ladeleistung zurückgelegt.
        status = DeviceStatus.ON_OK
    else:
        # Bekommt etwas, aber nicht die volle Ladeleistung.
        status = DeviceStatus.ON_DEFICIT
    return aus_budget, gesamt, status, False


def _battery_insert_index(
    ordered: list[ConsumerConfig],
    priorities: dict[str, float],
    battery_priority: float,
) -> int:
    """Position der Batterie in der nach Priorität sortierten Verbraucherliste.

    Bei Gleichstand steht die Batterie **hinter** den Verbrauchern desselben
    Rangs — sie wird erst eingefügt, sobald ein Verbraucher echt niedriger
    priorisiert ist.
    """
    for index, consumer in enumerate(ordered):
        if priorities.get(consumer.subentry_id, 999.0) > battery_priority:
            return index
    return len(ordered)


def build_views(
    hass: HomeAssistant,
    coordinator: EnergyManagerCoordinator,
    available_w: float | None,
) -> tuple[list[ConsumerView], BatteryView | None]:
    """Bewertet alle Verbraucher in Prioritätsreihenfolge.

    Nimmt die Batterie als verschiebbare Last teil (siehe
    :class:`~.models.BatteryLoad`), wird sie an ihrem Rang in dieselbe
    Budget-Kaskade eingehängt: Verbraucher **über** ihr bekommen den Überschuss
    zuerst und bleiben eingeschaltet, die Batterie reserviert danach ihre
    Ladeleistung, und nur was dann noch übrig ist, steht Verbrauchern **unter**
    ihr zur Verfügung. Geschaltet wird die Batterie dabei nicht.

    **Abweichung von der Karte:** Dort reserviert jeder Verbraucher mit Status
    ``off_ready`` Budget, auch wenn er nicht an der Automatik teilnimmt. Für die
    reine Anzeige ist das richtig. Für die Schaltentscheidung wäre es falsch —
    ein manuell verwalteter Verbraucher würde Budget blockieren, das die
    Automatik vergeben könnte. Der gemeldete Status bleibt identisch, nur die
    Verteilung unterscheidet sich.
    """
    now = dt_util.utcnow().timestamp()
    priorities = coordinator.priorities()
    battery = coordinator.battery_load()
    budget = available_w
    views: list[ConsumerView] = []
    battery_view: BatteryView | None = None

    ordered = order_consumers(coordinator.consumers, priorities)
    battery_at = (
        _battery_insert_index(ordered, priorities, battery.priority) if battery is not None else -1
    )

    rank = 0
    for position, consumer in enumerate(ordered):
        if battery is not None and position == battery_at:
            battery_view, budget = _build_battery_view(battery, budget, rank)
            rank += 1

        state = hass.states.get(consumer.switch_entity)
        entity_available = state is not None and not is_unavailable(state)
        currently_on = entity_available and is_on(state)

        managed = coordinator.is_managed(consumer.subentry_id)

        power = read_power_w(hass, consumer.power_entity)
        power_w = None if power.w is None else round_w(power.w)
        estimated_w = coordinator.estimated_power(consumer.subentry_id)
        required_w = resolve_required_w(consumer, power_w, estimated_w)

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
                required_source=required_source(consumer, power_w, estimated_w),
            )
        )
        rank += 1

    # Steht die Batterie ganz hinten (niedrigste Priorität), fällt sie durch die
    # Schleife nicht ab — hier nachholen.
    if battery is not None and battery_at >= len(ordered):
        battery_view, budget = _build_battery_view(battery, budget, rank)

    assign_displaceable(views, coordinator, now)
    return views, battery_view


def _build_battery_view(
    battery: BatteryLoad,
    budget: float | None,
    rank: int,
) -> tuple[BatteryView, float | None]:
    """Bewertet die Batterie-Last und zieht ihren Anspruch vom Budget ab.

    Vom Budget geht nur der Teil ab, der an diesem Rang zusätzlich beansprucht
    wird; die Reserve ist vorher schon abgezogen worden.
    """
    aus_budget, gesamt, status, full = battery_claim(battery, budget)
    remaining = None if budget is None else budget - aus_budget
    view = BatteryView(
        rank=rank,
        priority=battery.priority,
        max_charge_w=battery.max_charge_w,
        claim_w=gesamt,
        charging_w=battery.charging_w,
        status=status,
        headroom_w=remaining,
        full=full,
    )
    return view, remaining


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
