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

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Richtet einen Konfigurationseintrag ein."""
    # Schritt 0: bewusst noch ohne Koordinator und Plattformen. Das Gerüst soll
    # sich erst gegen hassfest und die Testumgebung beweisen.
    _LOGGER.debug("Energy Manager wird eingerichtet: %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entlädt einen Konfigurationseintrag."""
    return True
