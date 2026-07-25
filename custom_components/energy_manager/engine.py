"""Bewertung der Verbraucher und Schaltentscheidung.

Die Ampellogik ist eine Portierung von ``src/lib/device-status.ts`` der Karte —
mit **einer bewussten Abweichung**, siehe :func:`build_views`.
"""

from __future__ import annotations

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

    return views
