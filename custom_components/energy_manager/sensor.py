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
        """Alles, was eine Anzeige über die Energielage wissen muss.

        Der tatsächliche Zählerwert steht hier, weil sich ein Defizit sonst wie
        Netzbezug in gleicher Höhe liest. Ladestand und Mittelungsfenster kommen
        dazu, damit die Karte diese Sensoren nicht ein zweites Mal benennen muss.
        """
        data = self.coordinator.data
        if data is None:
            return {}

        surplus = data.surplus
        return {
            "grid_w": surplus.grid_w,
            "battery_w": surplus.battery_w,
            "battery_soc": self.coordinator.battery_soc(),
            "battery_correction_w": surplus.battery_correction,
            "degraded": surplus.degraded,
            "coverage": round(data.coverage, 2),
            "smoothing_window": self.coordinator.smoothing_window,
            "automation_enabled": self.coordinator.automation_enabled,
            # Weicht der Wert kurz nach einer Schaltung vom Zähler ab, steht
            # hier warum: die neue Last ist schon abgezogen, aber noch nicht
            # gemessen.
            "anticipated_w": surplus.anticipated_w,
            "may_switch": data.may_switch,
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
        """Der Zustand, den der Nutzer zuerst nachschaut, wenn nichts passiert.

        Die Reihenfolge ist die der Dringlichkeit: Ein Sensorfehler wiegt
        schwerer als der Hauptschalter, denn dann kann gar nicht entschieden
        werden — auch nicht nach dem Scharfschalten.
        """
        data = self.coordinator.data
        if data is None or not data.started:
            return "starting"
        if not data.surplus.usable:
            return "sensor_error"
        if not self.coordinator.automation_enabled:
            return "paused"
        # Automatik an, Sensoren gut — aber das Mittelungsfenster ist noch zu
        # dünn besetzt, um darauf zu schalten.
        if not data.may_switch:
            return "starting"
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
        """Alles, was eine Anzeige über diesen Verbraucher wissen muss.

        Bewusst vollständig: Die Energy Manager Card stellt die Verbraucher
        allein aus diesen Attributen dar. Ohne ``switch_entity`` müsste sie
        dieselbe Liste ein zweites Mal führen — genau die doppelte Pflege, die
        die Integration abschaffen soll.
        """
        view = self.view
        if view is None:
            return {}

        consumer = view.config
        return {
            # Zuordnung — hierüber findet eine Anzeige das eigentliche Gerät.
            "consumer_id": consumer.subentry_id,
            "consumer_name": consumer.name,
            "switch_entity": consumer.switch_entity,
            "power_entity": consumer.power_entity,
            # Bewertung
            "rank": view.rank + 1,
            "managed": view.managed,
            "is_on": view.is_on,
            "power_w": view.power_w,
            "required_w": view.required_w,
            "headroom_w": view.headroom_w,
            # Grenzwerte, damit die Anzeige "max. 2,0 kW" schreiben kann,
            # ohne die Konfiguration zu kennen.
            "min_power": consumer.min_power,
            "max_power": consumer.max_power,
            # Warum hier gerade nichts geschieht, obwohl es sinnvoll wäre.
            # Ohne diese Angabe ist "die Automatik tut nichts" nicht von einem
            # Fehler zu unterscheiden — die häufigste Rückfrage überhaupt.
            "blocked_by": self._blocked_by(),
        }

    def _blocked_by(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.blockers.get(self._subentry_id)


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
