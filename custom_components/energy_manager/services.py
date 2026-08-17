"""Dienste für den Eingriff von Hand.

Was sich über Entitäten allein nicht ausdrücken lässt, weil ihm eine **Dauer**
fehlt: eine Zwangsfreigabe, eine befristete Pause und deren vorzeitiges Ende.
Der Hauptschalter kann nur an oder aus.

Dazu das vorzeitige Ende einer manuellen Übersteuerung. Bewusst ein eigener
Dienst neben ``clear_force``: Die beiden Sperren haben verschiedene Gründe, und
wer eine Zwangsfreigabe beendet, meint nicht dasselbe.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import EnergyManagerCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_FORCE_ON = "force_on"
SERVICE_CLEAR_FORCE = "clear_force"
SERVICE_CLEAR_MANUAL = "clear_manual"
SERVICE_PAUSE = "pause"
SERVICE_RESUME = "resume"

ATTR_DURATION = "duration"

# Ein Verbraucher wird über sein Gerät oder eine seiner Entitäten angesprochen.
# Beides, weil HA-Nutzer im Dienste-Werkzeug mal das eine, mal das andere
# vorfinden — und weil eine Automatisierung meist eine entity_id zur Hand hat.
_TARGET_SCHEMA = {
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
}

_DURATION = vol.All(cv.time_period, vol.Range(min=timedelta(seconds=1)))

FORCE_ON_SCHEMA = vol.Schema({**_TARGET_SCHEMA, vol.Required(ATTR_DURATION): _DURATION})

CLEAR_FORCE_SCHEMA = vol.Schema(_TARGET_SCHEMA)

CLEAR_MANUAL_SCHEMA = vol.Schema(_TARGET_SCHEMA)

PAUSE_SCHEMA = vol.Schema({vol.Optional(ATTR_DURATION): _DURATION})

RESUME_SCHEMA = vol.Schema({})


def _coordinator(hass: HomeAssistant) -> EnergyManagerCoordinator:
    """Die einzige Instanz. ``single_config_entry`` garantiert das."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entries[0].runtime_data


def _subentry_ids(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Löst das Ziel eines Aufrufs in Verbraucher-Kennungen auf.

    Ein Verbraucher ist ein Gerät, dessen Kennung im Geräteregister die
    ``subentry_id`` ist. Über eine Entität führt der Weg einen Schritt weiter
    über deren Gerät.
    """
    devices = dr.async_get(hass)
    entities = er.async_get(hass)

    device_ids: set[str] = set(call.data.get(ATTR_DEVICE_ID) or [])
    for entity_id in call.data.get(ATTR_ENTITY_ID) or []:
        entry = entities.async_get(entity_id)
        if entry is None or entry.device_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_target",
                translation_placeholders={"target": entity_id},
            )
        device_ids.add(entry.device_id)

    if not device_ids:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")

    coordinator = _coordinator(hass)
    found: list[str] = []

    for device_id in device_ids:
        device = devices.async_get(device_id)
        if device is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_target",
                translation_placeholders={"target": device_id},
            )
        # Die zweite Hälfte des Identifikators ist die subentry_id — beim Hub
        # ist es die entry_id, und der ist kein Verbraucher.
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier in coordinator.consumers:
                found.append(identifier)
                break
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_consumer",
                translation_placeholders={"target": device.name or device_id},
            )

    return found


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Registriert die Dienste. Einmal je HA-Start, nicht je Eintrag."""

    async def force_on(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        seconds = call.data[ATTR_DURATION].total_seconds()
        for subentry_id in _subentry_ids(hass, call):
            await coordinator.async_force_on(subentry_id, seconds)

    async def clear_force(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        for subentry_id in _subentry_ids(hass, call):
            await coordinator.async_clear_force(subentry_id)

    async def clear_manual(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        for subentry_id in _subentry_ids(hass, call):
            await coordinator.async_clear_manual(subentry_id)

    async def pause(call: ServiceCall) -> None:
        duration: Any = call.data.get(ATTR_DURATION)
        await _coordinator(hass).async_pause(None if duration is None else duration.total_seconds())

    async def resume(_call: ServiceCall) -> None:
        await _coordinator(hass).async_set_automation(True)

    for name, handler, schema in (
        (SERVICE_FORCE_ON, force_on, FORCE_ON_SCHEMA),
        (SERVICE_CLEAR_FORCE, clear_force, CLEAR_FORCE_SCHEMA),
        (SERVICE_CLEAR_MANUAL, clear_manual, CLEAR_MANUAL_SCHEMA),
        (SERVICE_PAUSE, pause, PAUSE_SCHEMA),
        (SERVICE_RESUME, resume, RESUME_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema)


@callback
def async_remove_services(hass: HomeAssistant) -> None:
    """Entfernt die Dienste, wenn der letzte Eintrag geht."""
    for name in (
        SERVICE_FORCE_ON,
        SERVICE_CLEAR_FORCE,
        SERVICE_CLEAR_MANUAL,
        SERVICE_PAUSE,
        SERVICE_RESUME,
    ):
        hass.services.async_remove(DOMAIN, name)
