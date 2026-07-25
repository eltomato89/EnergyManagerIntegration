"""Überschuss, Status und Sperrzeit."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SUBENTRY_TYPE_CONSUMER
from .coordinator import EnergyManagerCoordinator
from .entity import ConsumerEntity, EnergyManagerEntity
from .models import ConsumerConfig, DeviceStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Legt die Sensoren an."""
    coordinator: EnergyManagerCoordinator = entry.runtime_data

    async_add_entities(
        [
            SurplusSensor(coordinator),
            RawSurplusSensor(coordinator),
            ManagerStatusSensor(coordinator),
        ]
    )

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_CONSUMER:
            continue
        consumer = coordinator.consumers.get(subentry_id)
        if consumer is None:
            continue

        async_add_entities(
            [
                ConsumerStatusSensor(coordinator, consumer),
                ConsumerLockedUntilSensor(coordinator, consumer),
            ],
            config_subentry_id=subentry_id,
        )


class SurplusSensor(EnergyManagerEntity, SensorEntity):
    """Verfügbarer Überschuss — nach Reserve, Ladestandsregel und Glättung.

    Negativ bedeutet ein Defizit gegenüber der Erzeugung, **nicht** Netzbezug in
    gleicher Höhe: die Batterie kann einen Teil davon stützen.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EnergyManagerCoordinator) -> None:
        super().__init__(coordinator, "surplus")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.surplus.available if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Diagnose — vor allem der tatsächliche Zählerwert.

        Ohne ihn liest sich ein Defizit wie Netzbezug in gleicher Höhe.
        """
        if self.coordinator.data is None:
            return {}
        surplus = self.coordinator.data.surplus
        return {
            "grid_w": surplus.grid_w,
            "battery_w": surplus.battery_w,
            "battery_correction_w": surplus.battery_correction,
            "degraded": surplus.degraded,
            "coverage": round(self.coordinator.data.coverage, 2),
            "errors": [e.value for e in surplus.errors],
        }


class RawSurplusSensor(EnergyManagerEntity, SensorEntity):
    """Ungeglätteter Rohwert, für die Fehlersuche."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: EnergyManagerCoordinator) -> None:
        super().__init__(coordinator, "surplus_raw")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.surplus.raw if self.coordinator.data else None


class ManagerStatusSensor(EnergyManagerEntity, SensorEntity):
    """Zustand der Automatik."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = ["starting", "running", "paused", "sensor_error"]

    def __init__(self, coordinator: EnergyManagerCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data is None or not data.running:
            return "starting"
        # Fehlende oder falsch konfigurierte Sensoren wiegen schwerer als der
        # Hauptschalter: sie bedeuten, dass gar nicht entschieden werden kann.
        if not data.surplus.usable:
            return "sensor_error"
        if not self.coordinator.automation_enabled:
            return "paused"
        return "running"


class ConsumerStatusSensor(ConsumerEntity, SensorEntity):
    """Ampelzustand eines Verbrauchers. Werte identisch zur Karte."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [status.value for status in DeviceStatus]

    def __init__(self, coordinator: EnergyManagerCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "status")

    @property
    def native_value(self) -> str | None:
        view = self.view
        return view.status.value if view else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        view = self.view
        if view is None:
            return {}
        return {
            "rank": view.rank + 1,
            "managed": view.managed,
            "power_w": view.power_w,
            "required_w": view.required_w,
            "headroom_w": view.headroom_w,
        }


class ConsumerLockedUntilSensor(ConsumerEntity, SensorEntity):
    """Wann die Sperre endet.

    Erfüllt die Zusage an die Karte: sie kann ihre Schätzung aus
    ``last_changed`` durch diesen exakten Wert ersetzen.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:lock-clock"

    def __init__(self, coordinator: EnergyManagerCoordinator, consumer: ConsumerConfig) -> None:
        super().__init__(coordinator, consumer, "locked_until")

    @property
    def native_value(self) -> datetime | None:
        view = self.view
        if view is None or view.locked_until is None:
            return None
        return dt_util.utc_from_timestamp(view.locked_until)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        view = self.view
        return {"lock_kind": view.lock_kind} if view else {}
