"""Energy Manager — PV-Überschusssteuerung.

Die Integration berechnet den PV-Überschuss und schaltet Verbraucher nach
Priorität. Sie ist das Gegenstück zur Energy Manager Card, die dieselben Werte
anzeigt und bedient.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import EnergyManagerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type EnergyManagerConfigEntry = ConfigEntry[EnergyManagerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyManagerConfigEntry) -> bool:
    """Richtet einen Konfigurationseintrag ein."""
    coordinator = EnergyManagerCoordinator(hass, entry)
    await coordinator.async_load()

    # Kein async_config_entry_first_refresh: es gibt nichts abzufragen, was
    # fehlschlagen könnte. Der erste Zustand wird direkt berechnet.
    await coordinator.async_request_refresh_now()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator.async_setup_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: EnergyManagerConfigEntry) -> None:
    """Lädt neu, wenn sich Optionen oder Verbraucher geändert haben.

    Ein Neuladen ist der einfachste Weg, Entitäten für neue Verbraucher
    anzulegen und für entfernte verschwinden zu lassen.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EnergyManagerConfigEntry) -> bool:
    """Entlädt einen Konfigurationseintrag."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EnergyManagerConfigEntry) -> None:
    """Räumt den gespeicherten Laufzeitzustand mit weg."""
    coordinator = EnergyManagerCoordinator(hass, entry)
    await coordinator.async_remove_storage()
