"""Zustandshaltung und Regeltakt.

Bewusst ein Koordinator **ohne** ``update_interval``: es gibt nichts
abzufragen. Der Zustand wird aus zwei Richtungen angestoßen — durch
Zustandsänderungen der beobachteten Sensoren und durch einen langsamen Takt für
Bedingungen, die ohne Sensoränderung ablaufen (Mindestlaufzeiten, Sperrzeiten).
Beide münden in dieselbe Auswertung.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_MODE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CONSUMPTION_ENTITY,
    CONF_CONSUMPTION_INCLUDES_BATTERY,
    CONF_GRID_ENTITY,
    CONF_INVERT_GRID,
    CONF_METER_MODE,
    CONF_POWER_ENTITY,
    CONF_PRODUCTION_ENTITY,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DEBOUNCE_COOLDOWN,
    DEFAULT_SMOOTHING_WINDOW,
    DOMAIN,
    METER_MODE_GRID,
    STORAGE_MINOR_VERSION,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    SUBENTRY_TYPE_CONSUMER,
    TICK_INTERVAL,
)
from .models import (
    ConsumerConfig,
    ConsumerRuntime,
    ConsumerView,
    ManagerState,
    Reading,
    ReadingReason,
    SurplusResult,
)
from .smoothing import TimeWeightedWindow
from .surplus import SurplusInput, apply_reserve, compute_surplus
from .units import combine_battery, invert, read_percent, read_power_w

_LOGGER = logging.getLogger(__name__)

_NO_READING = Reading(w=None, reason=ReadingReason.MISSING)


class EnergyManagerCoordinator(DataUpdateCoordinator[ManagerState]):
    """Hält den berechneten Zustand und stößt die Auswertung an."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=None,
            always_update=False,
        )
        self.consumers: dict[str, ConsumerConfig] = {}
        self.runtime: dict[str, ConsumerRuntime] = {}
        # Hauptschalter. Standardmäßig aus: nach dem Einrichten soll erst
        # beobachtet und dann bewusst scharfgeschaltet werden.
        self.automation_enabled = False

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}",
            minor_version=STORAGE_MINOR_VERSION,
            atomic_writes=True,
        )
        self._window = TimeWeightedWindow(self._smoothing_window())
        self._lock = asyncio.Lock()
        self._unsub_state: CALLBACK_TYPE | None = None
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=DEBOUNCE_COOLDOWN,
            immediate=False,
            function=self._async_evaluate,
        )
        # Solange False, wird nichts geschaltet. HA meldet beim Start nicht für
        # jede Entität einen Zustand; auf halb gefüllten Daten zu schalten wäre
        # der schlimmste Zeitpunkt für eine Fehlentscheidung.
        self._started = False

    # -- Einrichtung ---------------------------------------------------------

    async def async_load(self) -> None:
        """Lädt den Laufzeitzustand und die Verbraucher."""
        stored = await self._store.async_load() or {}
        self.runtime = {
            key: ConsumerRuntime.from_dict(value)
            for key, value in (stored.get("consumers") or {}).items()
        }
        self.automation_enabled = bool(stored.get("automation_enabled", False))
        self.reload_consumers()

    @callback
    def reload_consumers(self) -> None:
        """Liest die Verbraucher aus den Subentries."""
        entry = self.config_entry
        assert entry is not None

        self.consumers = {
            subentry_id: ConsumerConfig.from_subentry(subentry_id, dict(subentry.data))
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_TYPE_CONSUMER
        }

        # Laufzeitzustand für neue Verbraucher anlegen, für entfernte verwerfen.
        for subentry_id in self.consumers:
            self.runtime.setdefault(subentry_id, ConsumerRuntime())
        for subentry_id in list(self.runtime):
            if subentry_id not in self.consumers:
                del self.runtime[subentry_id]

    @callback
    def async_setup_listeners(self) -> None:
        """Hängt sich an Zustandsänderungen und den Takt."""
        entry = self.config_entry
        assert entry is not None

        self._resubscribe()

        entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._handle_tick,
                timedelta(seconds=TICK_INTERVAL),
                name=f"{DOMAIN} tick",
                cancel_on_shutdown=True,
            )
        )
        entry.async_on_unload(self._unsubscribe_states)

        if self.hass.is_running:
            self._started = True
        else:
            entry.async_on_unload(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._handle_ha_started
                )
            )

    @callback
    def _resubscribe(self) -> None:
        """Beobachtet genau die Entitäten, die den Zustand beeinflussen."""
        self._unsubscribe_states()

        tracked = self.tracked_entities()
        if not tracked:
            return

        self._unsub_state = async_track_state_change_event(
            self.hass, sorted(tracked), self._handle_state_event
        )

    @callback
    def _unsubscribe_states(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None

    @callback
    def tracked_entities(self) -> set[str]:
        """Alle Entitäten, deren Änderung eine neue Auswertung rechtfertigt."""
        data = dict(self.config_entry.data) if self.config_entry else {}
        ids: set[str] = set()

        for key in (
            CONF_GRID_ENTITY,
            CONF_PRODUCTION_ENTITY,
            CONF_CONSUMPTION_ENTITY,
            CONF_BATTERY_SOC_ENTITY,
            CONF_BATTERY_POWER_ENTITY,
            CONF_BATTERY_CHARGE_ENTITY,
            CONF_BATTERY_DISCHARGE_ENTITY,
        ):
            if entity_id := data.get(key):
                ids.add(entity_id)

        for consumer in self.consumers.values():
            ids.add(consumer.switch_entity)
            if consumer.power_entity:
                ids.add(consumer.power_entity)

        return ids

    # -- Auslöser ------------------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None:
            return

        old_state = event.data["old_state"]
        # Reine Attributänderungen ignorieren: ein Leistungssensor mit
        # wechselnden Attributen würde sonst dauernd auslösen.
        if old_state is not None and old_state.state == new_state.state:
            return

        self.config_entry.async_create_background_task(
            self.hass, self._debouncer.async_call(), name=f"{DOMAIN} evaluate"
        )

    async def _handle_tick(self, _now: Any) -> None:
        """Zeitbedingungen prüfen — ohne Entprellung, der Takt ist langsam genug."""
        await self._async_evaluate()

    @callback
    def _handle_ha_started(self, _event: Event) -> None:
        self._started = True
        self.config_entry.async_create_background_task(
            self.hass, self._async_evaluate(), name=f"{DOMAIN} first evaluate"
        )

    async def async_request_refresh_now(self) -> None:
        """Sofortige Auswertung, etwa nach einer Bedienung."""
        await self._async_evaluate()

    # -- Auswertung ----------------------------------------------------------

    async def _async_evaluate(self) -> None:
        """Berechnet den Zustand neu.

        Idempotent: mehrfaches Aufrufen mit denselben Eingangswerten ändert
        nichts. Die Sperre verhindert, dass Takt und Zustandsereignis
        gleichzeitig hineinlaufen.
        """
        async with self._lock:
            now = dt_util.utcnow().timestamp()
            surplus = self._compute(now)

            state = ManagerState(
                surplus=surplus,
                consumers=self._build_views(surplus),
                coverage=self._window.coverage(now),
                running=self._started,
            )
            self.async_set_updated_data(state)

    def _compute(self, now: float) -> SurplusResult:
        """Überschuss aus den aktuellen Zuständen, geglättet."""
        instant = self._instant_surplus()
        self._window.set_window(self._smoothing_window())
        self._window.push(instant.raw, now)

        if self._smoothing_window() <= 0 or instant.raw is None:
            return instant

        smoothed = self._window.value(now)
        if smoothed is None:
            return instant

        data = dict(self.config_entry.data) if self.config_entry else {}
        # Geglättet wird der Rohwert; Reserve und Ladestandsregel greifen
        # danach, sonst laufen sie dem Mittelungsfenster hinterher.
        available = apply_reserve(
            smoothed,
            read_percent(self.hass, data.get(CONF_BATTERY_SOC_ENTITY)),
            data.get(CONF_BATTERY_MIN_SOC),
            data.get(CONF_BATTERY_RESERVE_W, 0),
        )

        return SurplusResult(
            raw=round(smoothed),
            available=None if available is None else round(available),
            battery_correction=instant.battery_correction,
            grid_w=instant.grid_w,
            battery_w=instant.battery_w,
            degraded=instant.degraded,
            errors=instant.errors,
        )

    def _instant_surplus(self) -> SurplusResult:
        """Momentanwert ohne Glättung."""
        data = dict(self.config_entry.data) if self.config_entry else {}

        battery = self._battery_reading(data)
        return compute_surplus(
            SurplusInput(
                mode=data.get(CONF_METER_MODE, METER_MODE_GRID),
                grid=invert(
                    read_power_w(self.hass, data.get(CONF_GRID_ENTITY)),
                    data.get(CONF_INVERT_GRID, False),
                ),
                production=read_power_w(self.hass, data.get(CONF_PRODUCTION_ENTITY)),
                consumption=read_power_w(self.hass, data.get(CONF_CONSUMPTION_ENTITY)),
                battery=battery,
                battery_configured=self._has_battery(data),
                battery_mode=data.get(CONF_BATTERY_MODE, "charge_only"),
                battery_soc=read_percent(self.hass, data.get(CONF_BATTERY_SOC_ENTITY)),
                consumption_includes_battery=data.get(CONF_CONSUMPTION_INCLUDES_BATTERY, False),
                battery_min_soc=data.get(CONF_BATTERY_MIN_SOC),
                battery_reserve_w=data.get(CONF_BATTERY_RESERVE_W, 0),
            )
        )

    def _battery_reading(self, data: dict[str, Any]) -> Reading:
        if data.get(CONF_BATTERY_CHARGE_ENTITY) or data.get(CONF_BATTERY_DISCHARGE_ENTITY):
            return combine_battery(
                read_power_w(self.hass, data.get(CONF_BATTERY_CHARGE_ENTITY)),
                read_power_w(self.hass, data.get(CONF_BATTERY_DISCHARGE_ENTITY)),
            )
        return invert(
            read_power_w(self.hass, data.get(CONF_BATTERY_POWER_ENTITY)),
            data.get(CONF_BATTERY_INVERT, False),
        )

    @staticmethod
    def _has_battery(data: dict[str, Any]) -> bool:
        return any(
            data.get(key)
            for key in (
                CONF_BATTERY_POWER_ENTITY,
                CONF_BATTERY_CHARGE_ENTITY,
                CONF_BATTERY_DISCHARGE_ENTITY,
                CONF_BATTERY_SOC_ENTITY,
            )
        )

    def _build_views(self, surplus: SurplusResult) -> list[ConsumerView]:
        """Bewertet die Verbraucher. Die Ampellogik folgt in Schritt 5."""
        from .engine import build_views  # lokal, um einen Ringbezug zu vermeiden

        return build_views(self.hass, self, surplus.available)

    # -- Hilfen für Entitäten ------------------------------------------------

    def view_for(self, subentry_id: str) -> ConsumerView | None:
        if self.data is None:
            return None
        for view in self.data.consumers:
            if view.config.subentry_id == subentry_id:
                return view
        return None

    def runtime_for(self, subentry_id: str) -> ConsumerRuntime:
        return self.runtime.setdefault(subentry_id, ConsumerRuntime())

    def priorities(self) -> dict[str, float]:
        """Rang je Verbraucher."""
        return {key: value.priority for key, value in self.runtime.items()}

    def is_managed(self, subentry_id: str) -> bool:
        """Nimmt dieser Verbraucher an der Automatik teil?"""
        return self.runtime_for(subentry_id).managed

    async def async_set_priority(self, subentry_id: str, value: float) -> None:
        """Setzt den Rang und wertet sofort neu aus."""
        self.runtime_for(subentry_id).priority = value
        self.schedule_save()
        await self.async_request_refresh_now()

    async def async_set_managed(self, subentry_id: str, value: bool) -> None:
        """Schaltet die Teilnahme an der Automatik."""
        self.runtime_for(subentry_id).managed = value
        self.schedule_save()
        await self.async_request_refresh_now()

    async def async_set_automation(self, value: bool) -> None:
        """Schaltet die Automatik insgesamt."""
        self.automation_enabled = value
        self.schedule_save()
        await self.async_request_refresh_now()

    def _smoothing_window(self) -> float:
        entry = self.config_entry
        if entry is None:
            return DEFAULT_SMOOTHING_WINDOW
        return float(entry.options.get(CONF_SMOOTHING_WINDOW, DEFAULT_SMOOTHING_WINDOW))

    # -- Speichern -----------------------------------------------------------

    @callback
    def schedule_save(self) -> None:
        """Schreibt den Laufzeitzustand verzögert."""
        self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "automation_enabled": self.automation_enabled,
            "consumers": {key: value.as_dict() for key, value in self.runtime.items()},
        }

    async def async_remove_storage(self) -> None:
        """Räumt beim Entfernen der Integration auf."""
        await self._store.async_remove()


def consumer_power(hass: HomeAssistant, consumer: ConsumerConfig) -> Reading:
    """Aktuelle Leistungsaufnahme eines Verbrauchers."""
    if not consumer.power_entity:
        return _NO_READING
    return read_power_w(hass, consumer.power_entity)


__all__ = [
    "CONF_POWER_ENTITY",
    "CONF_SWITCH_ENTITY",
    "EnergyManagerCoordinator",
    "consumer_power",
]
