"""Hauptschalter und Automatik-Schalter je Verbraucher."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_CONSUMER
from .coordinator import EnergyManagerCoordinator
from .entity import ConsumerEntity, EnergyManagerEntity
from .models import ConsumerConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt den Hauptschalter und je Verbraucher einen Automatik-Schalter an."""
    coordinator: EnergyManagerCoordinator = entry.runtime_data

    async_add_entities([AutomationMasterSwitch(coordinator)])

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_CONSUMER:
            continue
        consumer = coordinator.consumers.get(subentry_id)
        if consumer is None:
            continue

        async_add_entities(
            [ConsumerAutomationSwitch(coordinator, consumer)],
            config_subentry_id=subentry_id,
        )


class AutomationMasterSwitch(EnergyManagerEntity, SwitchEntity):
    """Hauptschalter. Aus bedeutet: es wird nichts geschaltet."""

    _attr_icon = "mdi:robot"

    def __init__(self, coordinator: EnergyManagerCoordinator) -> None:
        super().__init__(coordinator, "automation")

    @property
    def is_on(self) -> bool:
        return self.coordinator.automation_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_automation(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_automation(False)


class ConsumerAutomationSwitch(ConsumerEntity, SwitchEntity):
    """Nimmt dieser Verbraucher an der Automatik teil?

    Schaltet **nicht** das Gerät selbst — dafür ist die Geräte-Entität
    zuständig. Aus bedeutet: die Automatik lässt diesen Verbraucher in Ruhe.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator: EnergyManagerCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "managed")

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_managed(self._subentry_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_managed(self._subentry_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_managed(self._subentry_id, False)
