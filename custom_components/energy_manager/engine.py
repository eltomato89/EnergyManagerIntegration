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
from .ladder import build_ladder, read_level
from .models import (
    BatteryLoad,
    BatteryView,
    ConsumerConfig,
    ConsumerRuntime,
    ConsumerView,
    DeviceStatus,
    Ladder,
    Level,
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


def cap_ladder(ladder: Ladder, observed_max: float | None) -> Ladder:
    """Begrenzt die Leiter auf das, was der Verbraucher tatsächlich erreicht.

    Der Sollwert anzukommen heißt nicht, dass die Last ihm folgt. Ein Fahrzeug
    lädt mit 10 A, obwohl 16 A angeboten sind; ein anderes ist fertig und nimmt
    nichts mehr. Ohne diese Grenze fordert die Automatik dauerhaft eine Stufe an,
    die nicht erreicht wird — und die Budget-Kaskade legt die Differenz für einen
    Verbraucher zurück, der sie nie abruft. Tiefer priorisierte gehen leer aus,
    und die Leistung wird eingespeist statt genutzt.

    **Eine Stufe über dem Beobachteten bleibt erlaubt.** Sonst wäre die Grenze
    selbsterfüllend: Was nie angefordert wird, wird nie erreicht, und ein
    Fahrzeug, das nach dem Vorwärmen mehr könnte, käme nicht mehr hoch. Übrig
    bleibt eine Reservierung von höchstens einer Stufe.
    """
    if observed_max is None or observed_max <= 0:
        return ladder

    erreicht = sum(1 for level in ladder.levels if level.w <= observed_max)
    grenze = erreicht + 1
    if grenze >= ladder.count:
        return ladder
    return Ladder(levels=ladder.levels[:grenze], source=ladder.source)


def reachable_w(
    budget: float,
    is_currently_on: bool,
    power_w: float | None,
    current: Level | None,
) -> float:
    """Wie viel Leistung dieser Verbraucher insgesamt ziehen könnte.

    Hier steckt die Feinheit der ganzen Stufenregelung. Die bestehende Regel
    „ein laufender Verbraucher verbraucht kein Budget, sein Verbrauch steckt im
    gemessenen Überschuss" gilt weiter — sie ist nur neu zu formulieren:

    * **Aus**: Erreichbar ist das freie Budget. Nichts davon ist verplant.
    * **Läuft**: Erreichbar ist der eigene Ist-Verbrauch **plus** das freie
      Budget. Der Ist-Verbrauch steckt schon im Messwert, das Budget ist der
      Kopfraum darüber hinaus.

    Ein Ausdruck für beide Richtungen: Fällt das Budget negativ aus, ergibt
    dieselbe Rechnung von selbst eine niedrigere Stufe.

    Ohne Leistungssensor tritt die zuletzt gestellte Stufe an die Stelle des
    Messwerts — die eigene Anforderung ist die beste vorliegende Schätzung
    dessen, was das Gerät zieht.
    """
    if not is_currently_on:
        return budget

    if power_w is not None:
        return power_w + budget
    if current is not None:
        return current.w + budget
    return budget


def choose_level(
    ladder: Ladder,
    reachable: float,
    current: Level | None,
    hysteresis: float,
) -> Level | None:
    """Die Stufe, auf der der Verbraucher laufen soll.

    ``None`` heißt: nicht einmal die kleinste Stufe passt. Dann ist zu
    **schalten**, nicht zu drosseln — das entscheidet :func:`decide_for`.

    Die Hysterese hält die Leiter an einer Stufengrenze ruhig: Ein Wechsel
    unterbleibt, solange er weniger als das Totband ausmacht. Das Abschalten
    bremst sie bewusst nicht — ein Defizit zu beenden ist dringender, als eine
    Stufe zu halten, und die Zeitbedingung dafür liegt ohnehin in
    ``turn_off_delay``.
    """
    target = ladder.at_or_below(reachable)
    if target is None or current is None:
        return target

    if abs(target.w - current.w) <= hysteresis:
        return current
    return target


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

        ladder = build_ladder(hass, consumer)
        observed_max = coordinator.observed_max(consumer.subentry_id)
        capped = False
        if ladder is not None:
            gekappt = cap_ladder(ladder, observed_max)
            capped = gekappt.count < ladder.count
            ladder = gekappt

        level = read_level(hass, consumer, ladder) if ladder is not None else None
        target: Level | None = None

        if ladder is not None:
            # Bei einem regelbaren Verbraucher ist der Bedarf die kleinste Stufe:
            # weniger lässt sich nicht anfordern, mehr ist nicht nötig, um ihn
            # überhaupt anlaufen zu lassen.
            required_w = ladder.min_w
            source = "ladder"
        else:
            required_w = resolve_required_w(consumer, power_w, estimated_w)
            source = required_source(consumer, power_w, estimated_w)

        if not entity_available or budget is None:
            status = DeviceStatus.UNAVAILABLE
        elif consumer.modulating and ladder is None:
            # Als regelbar eingerichtet, aber ohne verwertbares Raster — etwa
            # weil die Steuerentität fehlt, keine Einheit trägt oder gerade nicht
            # antwortet. Ohne Raster ist nicht zu entscheiden, welche Stufe
            # anzufordern wäre, und geraten wird hier nicht.
            status = DeviceStatus.UNAVAILABLE
        elif ladder is not None:
            target = choose_level(
                ladder,
                reachable_w(budget, currently_on, power_w, level),
                level,
                consumer.hysteresis or 0,
            )
            status, budget = _rate_modulating(
                ladder, level, target, currently_on, managed, power_w, budget
            )
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
                required_source=source,
                ladder=ladder,
                level=level,
                target=target,
                observed_max_w=observed_max,
                level_capped=capped,
            )
        )
        rank += 1

    # Steht die Batterie ganz hinten (niedrigste Priorität), fällt sie durch die
    # Schleife nicht ab — hier nachholen.
    if battery is not None and battery_at >= len(ordered):
        battery_view, budget = _build_battery_view(battery, budget, rank)

    assign_displaceable(views, coordinator, now)
    return views, battery_view


def _rate_modulating(
    ladder: Ladder,
    level: Level | None,
    target: Level | None,
    currently_on: bool,
    managed: bool,
    power_w: float | None,
    budget: float,
) -> tuple[DeviceStatus, float]:
    """Ampelzustand und verbleibendes Budget eines regelbaren Verbrauchers.

    **Die riskanteste Rechnung der Stufenregelung.** Ein Vorzeichenfehler hier
    stürzt nicht ab und erscheint in keinem Log — er führt dazu, dass
    Verbraucher weiter unten in der Rangfolge gelegentlich falsch geschaltet
    werden. Deshalb steht sie als eigene Funktion da und nicht in der Schleife.

    Die Regel ist dieselbe wie bisher, nur feiner aufgelöst:

    * **Aus**: Die Zielstufe wird ganz vom Budget abgezogen. Nichts davon steckt
      im Messwert.
    * **Läuft**: Nur der **Mehrbedarf** gegenüber dem Ist-Verbrauch wird
      abgezogen. Was das Gerät schon zieht, ist im gemessenen Überschuss
      enthalten und würde doppelt zählen.

    Eine Drosselung gibt bewusst **nichts** frei: Die Leistung ist noch nicht
    zurückgeflossen, der Zähler zeigt sie weiter, und pro Durchlauf findet ohnehin
    nur eine Handlung statt. Ein tiefer priorisierter Verbraucher, der sie sofort
    zugeteilt bekäme, würde denselben Überschuss ein zweites Mal vergeben.
    """
    if currently_on:
        if target is None:
            # Nicht einmal die kleinste Stufe passt — hier hilft nur Abschalten.
            return DeviceStatus.ON_DEFICIT, budget

        basis = power_w if power_w is not None else (level.w if level is not None else 0.0)
        if managed:
            budget -= max(target.w - basis, 0.0)

        if level is not None and target.w < level.w:
            # Muss heruntergehen: gerade wird mehr gezogen als gedeckt ist.
            return DeviceStatus.ON_DEFICIT, budget
        return DeviceStatus.ON_OK, budget

    if target is not None:
        if managed:
            budget -= target.w
        return DeviceStatus.OFF_READY, budget

    if budget >= ladder.min_w * CLOSE_THRESHOLD_RATIO:
        return DeviceStatus.OFF_CLOSE, budget
    return DeviceStatus.OFF_INSUFFICIENT, budget


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

    Bei einem regelbaren Verbraucher tritt die **gestellte Stufe** an die Stelle
    der Schätzung: ``required_w`` ist dort die kleinste Stufe und wäre viel zu
    niedrig — eine Wallbox auf 11 kW gäbe scheinbar nur 4,1 kW frei, und die
    Verdrängung fiele aus, obwohl sie gereicht hätte.
    """
    if view.power_w is not None and view.power_w > 0:
        return view.power_w
    if view.level is not None:
        return view.level.w
    return view.required_w


def throttled_power(view: ConsumerView) -> float:
    """Wie viel frei wird, wenn dieser Verbraucher auf seine kleinste Stufe geht.

    0, wenn nichts zu holen ist: kein Raster, keine gelesene Stufe, oder er läuft
    schon unten. Grundlage ist die **gestellte** Stufe und nicht der Messwert —
    was tatsächlich frei wird, ist die Differenz der beiden Sollwerte. Ein Gerät,
    das seinem Sollwert ohnehin nicht folgt, gibt weniger her, und genau dafür
    begrenzt :func:`cap_ladder` die Leiter.
    """
    if view.ladder is None or view.level is None:
        return 0.0
    return max(view.level.w - view.ladder.min_w, 0.0)


def may_be_displaced(view: ConsumerView, runtime: ConsumerRuntime, now: float) -> bool:
    """Darf dieser laufende Verbraucher für einen wichtigeren weichen?

    Die Ausnahmen sind dieselben, die auch sonst vor einer Schaltung schützen —
    eine Verdrängung ist kein Freibrief, die Mindestlaufzeit zu übergehen.
    """
    if not may_be_touched(view, runtime, now):
        return False
    # Mindestlaufzeit: ein angefangener Waschgang wird nicht abgebrochen.
    return not (view.locked_until is not None and now < view.locked_until)


def may_be_throttled(view: ConsumerView, runtime: ConsumerRuntime, now: float) -> bool:
    """Darf dieser laufende Verbraucher für einen wichtigeren gedrosselt werden?

    Dieselben Ausnahmen wie beim Weichen, **ohne** die Mindestlaufzeit: Sie
    schützt davor, ein Gerät zu früh abzuschalten. Gedrosselt läuft es weiter,
    nur schwächer — ein angefangener Waschgang wird davon nicht abgebrochen.

    Die Haltezeit zwischen zwei Stufen gilt dagegen auch hier: Sie ist der
    Schutz davor, die Leiter im Takt der Auswertung zu bewegen, und eine
    Verdrängung ist kein Grund, ihn zu übergehen.
    """
    if not may_be_touched(view, runtime, now):
        return False
    if _level_hold(view.config, runtime, now) is not None:
        return False
    return throttled_power(view) > 0


def may_be_touched(view: ConsumerView, runtime: ConsumerRuntime, now: float) -> bool:
    """Die Ausnahmen, die für jeden Eingriff von außen gelten."""
    if not view.is_on or not view.available:
        return False
    if not view.managed:
        return False
    # Wer ausdrücklich in Ruhe gelassen werden soll, wird auch nicht verdrängt
    # oder gedrosselt: Sonst wäre die Übersteuerung nur halb wirksam.
    if is_forced(runtime, now) or is_manual(runtime, now):
        return False
    return not (runtime.settle_until is not None and now < runtime.settle_until)


def assign_displaceable(
    views: list[ConsumerView],
    coordinator: EnergyManagerCoordinator,
    now: float,
) -> None:
    """Bestimmt je Verbraucher, wer für ihn zurückstecken müsste.

    Nur für ausgeschaltete, die es aus eigener Kraft nicht schaffen. Gesammelt
    wird von der niedrigsten Priorität aufwärts und nur so viel wie nötig — wer
    am wenigsten wichtig ist, steckt zuerst zurück, und niemand wird ohne Not
    angefasst.

    **Drosseln geht vor Abschalten.** Ein regelbarer Verbraucher gibt zuerst nur
    die Differenz bis zu seiner kleinsten Stufe her und läuft dabei weiter. Reicht
    das zusammengenommen nicht, wird in einem zweiten Durchgang aus Drosseln
    Abschalten — wieder von unten, und nur so weit wie nötig. Ohne diesen zweiten
    Durchgang ginge Können verloren: Bisher gab ein verdrängter regelbarer
    Verbraucher seine ganze Leistung her, und ein Fall, der heute aufgeht, würde
    aufhören zu funktionieren.
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

        _assign_one(view, views[index + 1 :], coordinator, now, fehlend)


def _assign_one(
    view: ConsumerView,
    kandidaten: list[ConsumerView],
    coordinator: EnergyManagerCoordinator,
    now: float,
    fehlend: float,
) -> None:
    """Sammelt für **einen** Verbraucher, wer zurückstecken müsste."""
    drosseln: list[tuple[ConsumerView, float]] = []
    abschalten: list[tuple[ConsumerView, float]] = []
    gewinn = 0.0

    # Erster Durchgang, von hinten: die unwichtigsten zuerst, und jeder mit dem
    # gelindesten Mittel, das er anzubieten hat.
    for kandidat in reversed(kandidaten):
        runtime = coordinator.runtime_for(kandidat.config.subentry_id)

        if may_be_throttled(kandidat, runtime, now):
            beitrag = throttled_power(kandidat)
            drosseln.append((kandidat, beitrag))
        elif may_be_displaced(kandidat, runtime, now):
            beitrag = displaced_power(kandidat)
            abschalten.append((kandidat, beitrag))
        else:
            continue

        gewinn += beitrag
        if gewinn >= fehlend:
            break

    # Zweiter Durchgang: aus Drosseln wird Abschalten, solange es nicht reicht.
    # Wieder von unten — die zuletzt gesammelten sind die unwichtigsten.
    if gewinn < fehlend:
        for eintrag in list(drosseln):
            kandidat, gedrosselt = eintrag
            runtime = coordinator.runtime_for(kandidat.config.subentry_id)
            if not may_be_displaced(kandidat, runtime, now):
                continue
            drosseln.remove(eintrag)
            abschalten.append((kandidat, displaced_power(kandidat)))
            gewinn += displaced_power(kandidat) - gedrosselt
            if gewinn >= fehlend:
                break

    # Nur übernehmen, wenn es am Ende auch reicht. Sonst hätte man abgeschaltet
    # und trotzdem nichts gewonnen.
    if gewinn < fehlend:
        return

    view.displaceable = tuple(kandidat.config.subentry_id for kandidat, _ in abschalten)
    view.throttleable = tuple(kandidat.config.subentry_id for kandidat, _ in drosseln)


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
    MANUAL = "manual"
    """Nach einem Eingriff von außen hält sich die Automatik befristet fern."""
    MIN_RUNTIME = "min_runtime"
    MIN_OFF_TIME = "min_off_time"
    TURN_ON_DELAY = "turn_on_delay"
    TURN_OFF_DELAY = "turn_off_delay"
    LEVEL_HOLD = "level_hold"
    """Haltezeit zwischen zwei Stufenwechseln läuft noch."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Was mit einem Verbraucher geschehen soll."""

    subentry_id: str
    turn_on: bool
    """Richtung der Laständerung, nicht zwangsläufig ein Schaltvorgang.

    Bei einem Stufenwechsel steht hier, ob die Last steigt oder fällt. So greifen
    dieselben Verzögerungsfelder und dieselbe Vorfahrtsregel wie beim Schalten —
    ohne einen zweiten Satz Bedingungen daneben.
    """

    reason: str

    displaces: tuple[str, ...] = ()
    """Verbraucher, die dafür weichen müssen.

    Zusammen mit dem Einschalten **eine** Handlung: Erst abschalten, dann
    einschalten, im selben Durchlauf. Verteilt auf mehrere Durchläufe könnte
    dazwischen ein anderer den frei gewordenen Überschuss belegen — dann wären
    die einen aus und der andere trotzdem nicht an.
    """

    throttles: tuple[str, ...] = ()
    """Verbraucher, die dafür auf ihre kleinste Stufe heruntergehen.

    Zusammen mit ``displaces`` und dem Einschalten **eine** Handlung. Das
    gelindere Mittel: Diese laufen weiter, nur schwächer.
    """

    level: Level | None = None
    """Die zu stellende Stufe, sofern der Verbraucher regelbar ist."""

    level_only: bool = False
    """Nur die Stufe stellen; am Schalter ändert sich nichts."""


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
    # Ein Stufenwechsel nach oben zählt als Einschaltwunsch, einer nach unten als
    # Ausschaltwunsch: Dieselbe Bedingung, dieselbe Verzögerung, ein Satz Felder.
    wants_on = view.step_up or (
        not view.is_on
        and (
            view.status is DeviceStatus.OFF_READY
            or bool(view.displaceable)
            or bool(view.throttleable)
        )
    )
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


def is_manual(runtime: ConsumerRuntime, now: float) -> bool:
    """Wirkt für diesen Verbraucher gerade eine manuelle Übersteuerung?"""
    return runtime.manual_until is not None and now < runtime.manual_until


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

    # Ein Eingriff von außen ist ebenso eine Ansage des Nutzers, nur ohne
    # Serviceaufruf: am Gerät, in der Oberfläche oder aus einer fremden
    # Automation. Befristet, damit eine Fehlerkennung von selbst abläuft.
    if is_manual(runtime, now):
        return None, Blocker.MANUAL

    if (blocker := check_blockers(view, runtime, now)) is not None:
        return None, blocker

    consumer = view.config

    # Einschalten oder hochstufen: Der Überschuss muss lange genug gereicht haben.
    if runtime.on_condition_since is not None:
        elapsed = now - runtime.on_condition_since
        if elapsed < consumer.turn_on_delay:
            return None, Blocker.TURN_ON_DELAY

        if view.step_up:
            # Ein laufendes Gerät höher zu stellen ist kein Einschalten: Die
            # Sperre aus der letzten Schaltung ist hier eine Mindestlaufzeit und
            # spricht nicht dagegen. Gebremst wird nur durch die Haltezeit.
            if (blocker := _level_hold(consumer, runtime, now)) is not None:
                return None, blocker
            return (
                Decision(
                    consumer.subentry_id,
                    True,
                    "level_up",
                    level=view.target,
                    level_only=True,
                ),
                None,
            )

        # Sperre aus der letzten Ausschaltung.
        if view.locked_until is not None and now < view.locked_until:
            return None, Blocker.MIN_OFF_TIME

        if view.displaceable or view.throttleable:
            return (
                Decision(
                    consumer.subentry_id,
                    True,
                    "displaces_lower_priority",
                    displaces=view.displaceable,
                    throttles=view.throttleable,
                    level=view.target,
                ),
                None,
            )
        return (
            Decision(consumer.subentry_id, True, "surplus_sufficient", level=view.target),
            None,
        )

    # Ausschalten oder herunterstufen: Das Defizit muss lange genug angehalten haben.
    if runtime.off_condition_since is not None:
        elapsed = now - runtime.off_condition_since
        if elapsed < consumer.turn_off_delay:
            return None, Blocker.TURN_OFF_DELAY

        if view.step_down:
            # Drosseln statt abschalten. Die Mindestlaufzeit schützt davor, ein
            # Gerät zu früh **abzuschalten** — es läuft weiter, nur schwächer,
            # und deshalb steht sie hier nicht im Weg.
            if (blocker := _level_hold(consumer, runtime, now)) is not None:
                return None, blocker
            return (
                Decision(
                    consumer.subentry_id,
                    False,
                    "level_down",
                    level=view.target,
                    level_only=True,
                ),
                None,
            )

        if view.locked_until is not None and now < view.locked_until:
            return None, Blocker.MIN_RUNTIME
        return Decision(consumer.subentry_id, False, "deficit_persists"), None

    return None, None


def _level_hold(
    consumer: ConsumerConfig,
    runtime: ConsumerRuntime,
    now: float,
) -> Blocker | None:
    """Ist die Haltezeit seit dem letzten Stufenwechsel abgelaufen?

    Ohne sie wanderte die Leiter im Takt der Auswertung auf und ab: Jede
    Stufenänderung verändert den Überschuss, auf den die nächste sich stützt, und
    das Beruhigungsfenster allein greift dafür zu grob — es sperrt das Gerät
    vollständig, auch für das Abschalten.
    """
    if not consumer.level_hold or runtime.last_level_ts is None:
        return None
    if now - runtime.last_level_ts >= consumer.level_hold:
        return None
    return Blocker.LEVEL_HOLD


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
