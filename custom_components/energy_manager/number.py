"""Priorität je Verbraucher."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_CONSUMER
from .coordinator import EnergyManagerCoordinator
from .entity import ConsumerEntity
from .models import ConsumerConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt je Verbraucher eine Prioritäts-Entität an."""
    coordinator: EnergyManagerCoordinator = entry.runtime_data

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_CONSUMER:
            continue
        consumer = coordinator.consumers.get(subentry_id)
        if consumer is None:
            continue

        async_add_entities(
            [ConsumerPriorityNumber(coordinator, consumer)],
            config_subentry_id=subentry_id,
        )


class ConsumerPriorityNumber(ConsumerEntity, NumberEntity):
    """Rang eines Verbrauchers. 1 bedeutet: zuerst einschalten."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:sort-numeric-variant"

    def __init__(self, coordinator: EnergyManagerCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "priority")

    @property
    def native_value(self) -> float:
        # Der Wert lebt im Koordinator, nicht in der Entität: die Automatik
        # braucht ihn auch dann, wenn die Entität noch nicht geladen ist.
        return self.coordinator.runtime_for(self._subentry_id).priority

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_priority(self._subentry_id, value)
