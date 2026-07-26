"""Nennleistung aus der aufgezeichneten Statistik schätzen.

Ohne eingetragene Nennleistung musste die Automatik bisher mit einem festen
Vorgabewert rechnen. Der ist fast immer falsch: Eine Wärmepumpe zieht ein
Vielfaches, eine Umwälzpumpe einen Bruchteil. Beides führt zu Fehlschaltungen —
zu früh eingeschaltet gibt Netzbezug, zu spät verschenkt Überschuss.

Herangezogen wird das **Maximum** der aufgezeichneten Werte, nicht der
Mittelwert: Gesucht ist, was das Gerät im Betrieb zieht. In den Mittelwert
gingen alle Stunden ein, in denen es aus war.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import ESTIMATE_MIN_W, ESTIMATE_WINDOW_DAYS

_LOGGER = logging.getLogger(__name__)


async def async_estimate_power(hass: HomeAssistant, statistic_id: str) -> float | None:
    """Höchste aufgezeichnete Leistung der letzten Tage, in Watt.

    ``None``, wenn sich nichts ermitteln lässt — dann bleibt es beim
    Vorgabewert. Gründe dafür sind normal und kein Fehler: kein Recorder, ein
    Sensor ohne ``state_class`` (dann führt HA keine Statistik), ein frisch
    angelegter Sensor ohne Aufzeichnung, oder ein Gerät, das im Zeitraum nie
    lief.
    """
    try:
        from homeassistant.components.recorder import get_instance, statistics
    except ImportError:  # pragma: no cover - Recorder ist Teil jeder Standardinstallation
        return None

    end = dt_util.utcnow()
    start = end - timedelta(days=ESTIMATE_WINDOW_DAYS)

    def _query() -> dict[str, float | None]:
        return statistics.statistic_during_period(
            hass,
            start,
            end,
            statistic_id,
            types={"max"},
            # Erzwingt Watt: Ein Sensor in kW liefert sonst Zahlen, die um den
            # Faktor 1000 danebenliegen — und das fiele erst beim Schalten auf.
            units={"power": "W"},
        )

    try:
        result = await get_instance(hass).async_add_executor_job(_query)
    except Exception:
        _LOGGER.debug("Statistik für %s nicht abrufbar", statistic_id, exc_info=True)
        return None

    maximum = result.get("max") if result else None
    if maximum is None:
        return None

    value = float(maximum)
    # Ein Standby-Wert von wenigen Watt ist keine Nennleistung. Ihn zu
    # übernehmen wäre schlimmer als der Vorgabewert: Die Automatik hielte das
    # Gerät für beliebig zuschaltbar.
    if value < ESTIMATE_MIN_W:
        return None

    return round(value)
