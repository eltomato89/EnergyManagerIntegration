"""Stellgrößen: Priorität je Verbraucher und die Vorgaben der Batterie."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_CONSUMER
from .coordinator import EnergyManagerCoordinator
from .entity import BatteryEntity, ConsumerEntity
from .models import BatteryConfig, ConsumerConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt je Verbraucher eine Prioritäts-Entität an."""
    coordinator: EnergyManagerCoordinator = entry.runtime_data

    for subentry_id, battery in coordinator.batteries.items():
        entitaeten: list[NumberEntity] = []

        # Den Rang gibt es nur mit eingetragener Ladeleistung: Ohne sie nimmt
        # der Speicher an der Reihenfolge nicht teil, und ein Rang, den niemand
        # liest, sähe aus wie eine Einstellung.
        if battery.shiftable:
            entitaeten.append(BatteryPriorityNumber(coordinator, battery))

        # Die Ladevorranggrenze hängt an einer anderen Bedingung: Sie wirkt in
        # der Überschussformel und braucht dafür nur einen Ladestand — die
        # Ladeleistung ist hier ohne Belang. Ohne Ladestandssensor gibt es sie
        # nicht, denn dann bliebe sie ohne Wirkung, und ein wirkungsloser
        # Regler ist schlimmer als keiner.
        if battery.soc_entity:
            entitaeten.append(BatteryMinSocNumber(coordinator, battery))

        if entitaeten:
            # Ohne config_subentry_id schriebe Home Assistant die Zuordnung des
            # Registry-Eintrags wieder auf None zurück — der Vorgabewert dort
            # ist None und kein "unverändert".
            async_add_entities(entitaeten, config_subentry_id=subentry_id)

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


class BatteryPriorityNumber(BatteryEntity, NumberEntity):
    """Rang eines Speichers als verschiebbare Last. 1 bedeutet: zuerst laden.

    Über denselben Weg wie die Verbraucher-Prioritäten bedienbar, damit das
    Sortieren im Dashboard die Speicher einschließen kann.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:sort-numeric-variant"

    def __init__(self, coordinator: EnergyManagerCoordinator, battery: BatteryConfig) -> None:
        super().__init__(coordinator, battery, "priority")

    @property
    def native_value(self) -> float:
        return self.coordinator.battery_priority

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_battery_priority(value)


class BatteryMinSocNumber(BatteryEntity, NumberEntity):
    """Ladestand, unterhalb dessen das Laden der Batterie Vorrang hat.

    Als Entität und nicht als Formularfeld, weil die Grenze keine Beschreibung
    der Anlage ist, sondern eine Vorgabe, die sich mit dem Tag ändert: Bei guter
    Vorhersage darf ein Verbraucher schon am Vormittag bei niedrigem Ladestand
    anlaufen, an einem trüben Tag nicht. Als Feld ließe sich das nicht
    automatisieren.

    0 schaltet die Regel ab — dann liegt jeder Ladestand über der Grenze.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: EnergyManagerCoordinator, battery: BatteryConfig) -> None:
        super().__init__(coordinator, battery, "min_soc")

    @property
    def native_value(self) -> float:
        return self.coordinator.battery_min_soc

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_battery_min_soc(value)
