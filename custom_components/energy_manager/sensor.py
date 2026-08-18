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
from .models import ConsumerConfig, ConsumerView, DeviceStatus


def _as_iso(timestamp: float | None) -> str | None:
    """Zeitstempel für ein Attribut. ISO, damit eine Anzeige damit rechnen kann."""
    if timestamp is None:
        return None
    return dt_util.utc_from_timestamp(timestamp).isoformat()


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
        attrs: dict[str, object] = {
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

        # Nimmt die Batterie als verschiebbare Last teil, bekommt die Karte hier
        # alles, um sie als eigene Zeile darzustellen — Rang, Ampelzustand und
        # reservierte Ladeleistung. Ohne diese Felder gibt es keine Batteriezeile
        # und das Verhalten bleibt wie zuvor.
        battery = data.battery
        if battery is not None:
            attrs.update(
                {
                    "battery_load": True,
                    "battery_rank": battery.rank + 1,
                    "battery_priority": battery.priority,
                    "battery_status": battery.status.value,
                    "battery_max_charge_w": battery.max_charge_w,
                    "battery_claim_w": round(battery.claim_w),
                    # Was nach der Batterie noch für tiefer priorisierte
                    # Verbraucher bleibt. Ohne diese Angabe ließe sich in der
                    # Anzeige nicht nachvollziehen, warum ein Verbraucher unter
                    # der Batterie leer ausgeht.
                    "battery_headroom_w": (
                        None if battery.headroom_w is None else round(battery.headroom_w)
                    ),
                    "battery_charging_w": battery.charging_w,
                    "battery_full": battery.full,
                    "battery_soc_entity": self.coordinator.battery_soc_entity,
                }
            )

        return attrs


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
        runtime = self.coordinator.runtime_for(self._subentry_id)
        return {
            **self._level_attributes(view),
            # Zuordnung — hierüber findet eine Anzeige das eigentliche Gerät.
            "consumer_id": consumer.subentry_id,
            "consumer_name": consumer.name,
            "switch_entity": consumer.switch_entity,
            "power_entity": consumer.power_entity,
            # Verhaltenstyp. Noch ohne Wirkung auf die Automatik; die Karte kann
            # ihn schon lesen und muss ihn später nicht nachrüsten.
            "consumer_type": consumer.consumer_type,
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
            # Woher required_w stammt. Ohne diese Angabe ist ein geratener Wert
            # nicht von einem eingetragenen zu unterscheiden — und niemand käme
            # auf die Idee, die Nennleistung nachzutragen.
            "required_source": view.required_source,
            # Warum hier gerade nichts geschieht, obwohl es sinnvoll wäre.
            # Ohne diese Angabe ist "die Automatik tut nichts" nicht von einem
            # Fehler zu unterscheiden — die häufigste Rückfrage überhaupt.
            "blocked_by": self._blocked_by(),
            # Wie viele laufende Verbraucher für diesen zurückstecken würden.
            # Erklärt den sonst überraschenden Fall, dass ein Gerät angeht,
            # obwohl der Überschuss allein nicht reicht. Getrennt gezählt, weil
            # es ein Unterschied ist, ob ein anderes Gerät ausgeht oder nur
            # heruntergeht.
            "displaces": len(view.displaceable),
            "throttles": len(view.throttleable),
            # Wann zuletzt jemand anders geschaltet hat — und wohin. Reine
            # Diagnose: die Automatik richtet sich nicht danach. Ausgewiesen,
            # damit sich vor dem Einschalten einer befristeten Übersteuerung
            # ablesen lässt, wie oft der Fall bei diesem Gerät überhaupt
            # auftritt. Bei taktenden Geräten ist das häufig, und dann ist eine
            # Übersteuerung die falsche Antwort.
            "last_foreign_change": _as_iso(runtime.last_foreign_change),
            "last_foreign_to": runtime.last_foreign_to,
            # Wie lange sich die Automatik nach dem Eingriff noch fernhält.
            # Damit kann eine Anzeige einen Countdown führen, ohne die
            # eingetragene Dauer zu kennen.
            "manual_until": _as_iso(runtime.manual_until),
            **self._daily_attributes(view),
        }

    def _blocked_by(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.blockers.get(self._subentry_id)

    @staticmethod
    def _daily_attributes(view: ConsumerView) -> dict[str, object]:
        """Der Tagesfortschritt — nur bei eingetragenem Ziel.

        Weggelassen statt mit Nullen befüllt, wie bei den Stufenangaben: An ihrem
        Vorhandensein erkennt eine Anzeige, dass es ein Ziel zu zeigen gibt.
        """
        if view.daily_target_kwh <= 0:
            return {}
        return {
            "daily_target_kwh": round(view.daily_target_kwh, 2),
            "daily_done_kwh": round(view.daily_done_kwh, 2),
            # Verbleibende Prognose. None heißt: kein brauchbarer Sensor, und
            # dann greift die Regel gar nicht.
            "daily_forecast_kwh": (
                None if view.daily_forecast_kwh is None else round(view.daily_forecast_kwh, 2)
            ),
            # Läuft der Verbraucher gerade unabhängig vom Überschuss? Die
            # Antwort auf „warum zieht der Netzstrom".
            "must_run": view.must_run,
        }

    @staticmethod
    def _level_attributes(view: ConsumerView) -> dict[str, object]:
        """Die Stufenangaben — nur bei einem regelbaren Verbraucher.

        Vollständig weggelassen statt mit ``None`` befüllt, damit eine Anzeige an
        ihrem Vorhandensein erkennt, dass es hier etwas zu regeln gibt. Dasselbe
        Muster wie bei den ``battery_*``-Angaben am Überschuss-Sensor.
        """
        ladder = view.ladder
        if ladder is None:
            return {}

        return {
            "control_entity": view.config.control_entity,
            # Woher das Raster stammt: number_w, number_a oder select. Ein
            # abgeleitetes Raster ist damit von einer eingetragenen Zuordnung zu
            # unterscheiden.
            "level_source": ladder.source,
            "level_count": ladder.count,
            "min_level_w": ladder.min_w,
            "max_level_w": ladder.max_w,
            # Die gestellte Stufe und ihre Position, 1-basiert wie rank.
            "level_w": None if view.level is None else view.level.w,
            "level_index": None if view.level is None else ladder.index_of(view.level) + 1,
            # Die Stufe, auf die die Automatik gehen würde. Weicht sie von
            # level_w ab, steht ein Stufenwechsel an — und blocked_by sagt,
            # warum er noch nicht stattgefunden hat.
            "setpoint_w": None if view.target is None else view.target.w,
            # Was das Gerät seit dem Einschalten höchstens erreicht hat, und ob
            # die Leiter deswegen beschnitten ist. Die Antwort auf „warum geht
            # sie nicht höher": Nicht der Überschuss fehlt, das Gerät nimmt nicht
            # mehr. Ohne diesen Ausweis sähe es aus wie ein Gerät mit weniger
            # Stufen.
            "observed_max_w": view.observed_max_w,
            "level_capped": view.level_capped,
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
