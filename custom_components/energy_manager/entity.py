"""Gemeinsame Basisklassen der Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EnergyManagerCoordinator
from .models import ConsumerConfig, ConsumerView


class EnergyManagerEntity(CoordinatorEntity[EnergyManagerCoordinator]):
    """Entität am Hub-Gerät."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EnergyManagerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        # Die entry_id ist stabil und vom Nutzer nicht änderbar — anders als
        # Name oder Entitäts-ID, aus denen abgeleitete Schlüssel bei jedem
        # Umbenennen neue Entitäten erzeugen würden.
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Energy Manager",
            manufacturer="Energy Manager",
            model="Überschusssteuerung",
            entry_type=DeviceEntryType.SERVICE,
        )


class ConsumerEntity(CoordinatorEntity[EnergyManagerCoordinator]):
    """Entität am Gerät eines Verbrauchers."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyManagerCoordinator,
        consumer: ConsumerConfig,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._subentry_id = consumer.subentry_id
        self._attr_unique_id = f"{consumer.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, consumer.subentry_id)},
            name=consumer.name,
            manufacturer="Energy Manager",
            model="Verbraucher",
            entry_type=DeviceEntryType.SERVICE,
            # Hängt das Verbraucher-Gerät unter den Hub.
            via_device=(DOMAIN, coordinator.config_entry.entry_id),
        )

    @property
    def consumer(self) -> ConsumerConfig | None:
        """Aktuelle Konfiguration dieses Verbrauchers."""
        return self.coordinator.consumers.get(self._subentry_id)

    @property
    def view(self) -> ConsumerView | None:
        """Aktuelle Bewertung dieses Verbrauchers."""
        return self.coordinator.view_for(self._subentry_id)

    @property
    def available(self) -> bool:
        """Verschwindet der Verbraucher aus der Konfiguration, ist er weg."""
        return super().available and self.consumer is not None
