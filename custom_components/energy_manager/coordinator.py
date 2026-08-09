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
from dataclasses import replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STARTED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_MAX_CHARGE_W,
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
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DEBOUNCE_COOLDOWN,
    DEFAULT_BATTERY_PRIORITY,
    DEFAULT_SETTLE_TIME,
    DEFAULT_SMOOTHING_WINDOW,
    DOMAIN,
    ESTIMATE_INTERVAL,
    METER_MODE_GRID,
    MIN_COVERAGE,
    STORAGE_MINOR_VERSION,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    SUBENTRY_TYPE_CONSUMER,
    TICK_INTERVAL,
)
from .engine import Decision, Evaluation, anticipated_w, build_views, decide
from .estimate import async_estimate_power
from .models import (
    BatteryLoad,
    BatteryView,
    ConsumerConfig,
    ConsumerRuntime,
    ConsumerView,
    ManagerState,
    Reading,
    ReadingReason,
    SurplusResult,
    has_battery,
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
        # Rang der Batterie als verschiebbare Last. Getrennt vom Verbraucher-
        # Laufzeitzustand, weil die Batterie kein Subentry ist und beim
        # Neuladen der Verbraucher nicht mit weggeräumt werden darf.
        self._battery_priority = DEFAULT_BATTERY_PRIORITY

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

        # Befristete Eingriffe von Hand. Beide werden beim Entladen abgeräumt —
        # ein Timer, der auf einen entladenen Eintrag zurückgreift, wäre ein
        # Fehler beim nächsten Auslösen.
        self._force_timers: dict[str, CALLBACK_TYPE] = {}
        self._pause_timer: CALLBACK_TYPE | None = None

        # Geschätzte Nennleistung je Verbraucher, aus der Statistik. Nur für
        # die, bei denen nichts eingetragen ist — und bewusst gepuffert: Die
        # Abfrage geht an die Datenbank und gehört nicht in jeden Durchlauf.
        self._estimated: dict[str, float] = {}

    # -- Einrichtung ---------------------------------------------------------

    async def async_load(self) -> None:
        """Lädt den Laufzeitzustand und die Verbraucher."""
        stored = await self._store.async_load() or {}
        self.runtime = {
            key: ConsumerRuntime.from_dict(value)
            for key, value in (stored.get("consumers") or {}).items()
        }
        self.automation_enabled = bool(stored.get("automation_enabled", False))
        self._battery_priority = float(stored.get("battery_priority", DEFAULT_BATTERY_PRIORITY))
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
        entry.async_on_unload(self._cancel_timers)

        entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._handle_estimate_tick,
                timedelta(seconds=ESTIMATE_INTERVAL),
                name=f"{DOMAIN} estimate",
                cancel_on_shutdown=True,
            )
        )

        if self.hass.is_running:
            self._started = True
            entry.async_create_background_task(
                self.hass, self._first_run(), name=f"{DOMAIN} initial estimate"
            )
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
    def _cancel_timers(self) -> None:
        """Bricht alle befristeten Eingriffe ab."""
        for cancel in self._force_timers.values():
            cancel()
        self._force_timers.clear()
        if self._pause_timer is not None:
            self._pause_timer()
            self._pause_timer = None

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

    async def _handle_estimate_tick(self, _now: Any) -> None:
        await self.async_update_estimates()

    async def async_update_estimates(self) -> None:
        """Holt die Nennleistung aus der Statistik, wo sie fehlt.

        Nur für Verbraucher ohne eingetragene Leistung: Wer sie angegeben hat,
        soll nicht von einer Schätzung überstimmt werden.
        """
        for subentry_id, consumer in self.consumers.items():
            if consumer.min_power or consumer.max_power:
                self._estimated.pop(subentry_id, None)
                continue
            if not consumer.power_entity:
                continue

            geschaetzt = await async_estimate_power(self.hass, consumer.power_entity)
            if geschaetzt is None:
                continue

            if self._estimated.get(subentry_id) != geschaetzt:
                _LOGGER.info(
                    "%s: Nennleistung aus der Statistik geschätzt — %.0f W",
                    consumer.name,
                    geschaetzt,
                )
            self._estimated[subentry_id] = geschaetzt

    def estimated_power(self, subentry_id: str) -> float | None:
        """Geschätzte Nennleistung, oder None."""
        return self._estimated.get(subentry_id)

    async def _handle_tick(self, _now: Any) -> None:
        """Zeitbedingungen prüfen — ohne Entprellung, der Takt ist langsam genug."""
        await self._async_evaluate()

    @callback
    def _handle_ha_started(self, _event: Event) -> None:
        self._started = True
        self.config_entry.async_create_background_task(
            self.hass, self._first_run(), name=f"{DOMAIN} first evaluate"
        )

    async def _first_run(self) -> None:
        """Erst schätzen, dann bewerten.

        In dieser Reihenfolge, damit die erste Entscheidung schon auf den
        geschätzten Werten beruht statt auf dem Vorgabewert.
        """
        await self.async_update_estimates()
        await self._async_evaluate()

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
            measured = self._compute(now)
            surplus = self._with_anticipation(measured, anticipated_w(self.runtime, now))
            views, battery = self._build_views(surplus)

            # Immer auswerten, auch bei ausgeschalteter Automatik: Die
            # Bedingungszähler müssen mitlaufen, damit nach dem Scharfschalten
            # nicht bei null begonnen wird, und die Begründungen erscheinen in
            # den Status-Attributen. Nur ausgeführt wird dann nichts.
            evaluation = decide(views, self.runtime, now)
            may_switch = self._may_switch(surplus, now)
            self._publish(surplus, views, battery, evaluation, now, may_switch)

            if not may_switch or evaluation.action is None:
                return

            await self._execute(evaluation.action, views, now)

            # Der eben veröffentlichte Zustand ist bereits überholt: Die
            # Schaltung ist erfolgt, das Budget dahinter vergeben. Ohne diese
            # zweite Runde zeigte die Anzeige bis zum nächsten Ereignis Budget
            # an, das es nicht mehr gibt — und der Zähler bestätigt es erst in
            # einigen Sekunden.
            after = self._with_anticipation(measured, anticipated_w(self.runtime, now))
            after_views, after_battery = self._build_views(after)
            self._publish(after, after_views, after_battery, evaluation, now, may_switch)

    @callback
    def _publish(
        self,
        surplus: SurplusResult,
        views: list[ConsumerView],
        battery: BatteryView | None,
        evaluation: Evaluation,
        now: float,
        may_switch: bool,
    ) -> None:
        self.async_set_updated_data(
            ManagerState(
                surplus=surplus,
                consumers=views,
                battery=battery,
                coverage=self._window.coverage(now),
                started=self._started,
                may_switch=may_switch,
                blockers={key: str(value) for key, value in evaluation.blockers.items()},
            )
        )

    def _may_switch(self, surplus: SurplusResult, now: float) -> bool:
        """Darf jetzt überhaupt geschaltet werden?

        Vier Bedingungen, die alle gelten müssen. Jede einzelne davon würde ein
        Schalten zum falschen Zeitpunkt erlauben:

        - **Hauptschalter an.** Sonst beobachtet die Integration nur.
        - **HA ist durchgestartet.** Während des Starts meldet nicht jede
          Entität einen Zustand; auf halb gefüllten Daten zu entscheiden ist der
          schlechteste denkbare Zeitpunkt.
        - **Der Überschuss ist brauchbar.** Ein ausgefallener oder falsch
          konfigurierter Sensor darf nicht als 0 W durchgehen.
        - **Das Mittelungsfenster ist gefüllt.** Direkt nach dem Start stützt
          sich der Mittelwert auf wenige Sekunden und schwankt entsprechend.
        """
        if not self.automation_enabled or not self._started:
            return False
        if not surplus.usable:
            return False
        return self._window.coverage(now) >= MIN_COVERAGE

    async def _execute(self, action: Decision, views: list[ConsumerView], now: float) -> None:
        """Führt eine Entscheidung aus und merkt sie sich.

        Eine Verdrängung umfasst mehrere Schaltvorgänge, ist aber **eine**
        Handlung: Erst weichen die niedriger priorisierten, dann kommt der
        wichtigere. Auf mehrere Durchläufe verteilt könnte dazwischen ein
        Dritter den frei gewordenen Überschuss belegen — dann wären die einen
        aus und der andere trotzdem nicht an.
        """
        by_id = {v.config.subentry_id: v for v in views}
        view = by_id.get(action.subentry_id)
        if view is None:
            return

        for subentry_id in action.displaces:
            opfer = by_id.get(subentry_id)
            if opfer is None:
                continue
            _LOGGER.info(
                "%s weicht für %s (%.0f W werden frei)",
                opfer.config.name,
                view.config.name,
                opfer.power_w or opfer.required_w,
            )
            await self._switch(opfer, turn_on=False, now=now)

        _LOGGER.info(
            "%s wird %s (%s, %.0f W)",
            view.config.name,
            "eingeschaltet" if action.turn_on else "ausgeschaltet",
            action.reason,
            view.required_w,
        )
        await self._switch(view, turn_on=action.turn_on, now=now)

    async def _switch(self, view: ConsumerView, *, turn_on: bool, now: float) -> None:
        """Schaltet einen Verbraucher und schreibt seinen Laufzeitzustand fort."""
        runtime = self.runtime_for(view.config.subentry_id)

        # Erst merken, dann schalten. Andersherum könnte das Zustandsereignis
        # der eigenen Schaltung eine zweite Auswertung anstoßen, bevor das
        # Beruhigungsfenster steht.
        runtime.last_switch_ts = now
        runtime.last_switch_to = turn_on
        runtime.settle_until = now + self._settle_time()
        # Beim Einschalten fehlt die neue Last im Messwert, beim Ausschalten
        # ist die alte noch enthalten — deshalb das umgekehrte Vorzeichen.
        runtime.anticipated_w = view.required_w if turn_on else -(view.power_w or view.required_w)
        runtime.on_condition_since = None
        runtime.off_condition_since = None
        self.schedule_save()

        await self.hass.services.async_call(
            HA_DOMAIN,
            SERVICE_TURN_ON if turn_on else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: view.config.switch_entity},
            blocking=True,
        )

    def _settle_time(self) -> float:
        entry = self.config_entry
        if entry is None:
            return float(DEFAULT_SETTLE_TIME)
        return float(entry.options.get(CONF_SETTLE_TIME, DEFAULT_SETTLE_TIME))

    def _compute(self, now: float) -> SurplusResult:
        """Der gemessene Überschuss, geglättet.

        Ohne Antizipation — die kommt getrennt dazu, weil sie sich innerhalb
        eines Durchlaufs ändert: nach einer Schaltung gilt ein anderer Wert als
        davor, obwohl derselbe Messwert zugrunde liegt.
        """
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

    @staticmethod
    def _with_anticipation(surplus: SurplusResult, pending: float) -> SurplusResult:
        """Zieht die gerade geschaltete, noch nicht gemessene Last ab.

        Bewusst auf dem ausgewiesenen Wert und nicht nur intern: "verfügbarer
        Überschuss" heißt "was sich noch zusätzlich einschalten lässt". Eine
        gerade zugeschaltete Last gehört abgezogen, ob der Zähler sie schon
        zeigt oder nicht — sonst zeigte die Karte für eine knappe Minute Budget
        an, das längst vergeben ist.

        ``raw`` bleibt unangetastet: er ist der Diagnosewert und soll den
        Messwert wiedergeben.
        """
        if not pending or surplus.available is None:
            return surplus
        return replace(
            surplus,
            available=round(surplus.available - pending),
            anticipated_w=round(pending),
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
                battery_configured=has_battery(data),
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

    def _build_views(self, surplus: SurplusResult) -> tuple[list[ConsumerView], BatteryView | None]:
        """Bewertet die Verbraucher in Prioritätsreihenfolge."""
        return build_views(self.hass, self, surplus.available)

    def battery_load(self) -> BatteryLoad | None:
        """Die Batterie als verschiebbare Last, sofern sie mitspielt.

        Nur wenn eine Batterie konfiguriert ist **und** eine maximale
        Ladeleistung eingetragen wurde. Ohne die Ladeleistung bleibt die
        Batterie wie bisher ein reiner Korrekturterm im Überschuss und taucht in
        der Reihenfolge nicht auf.
        """
        data = dict(self.config_entry.data) if self.config_entry else {}
        max_charge = data.get(CONF_BATTERY_MAX_CHARGE_W)
        if not has_battery(data) or not max_charge:
            return None

        reading = self._battery_reading(data)
        return BatteryLoad(
            priority=self._battery_priority,
            max_charge_w=float(max_charge),
            reserve_w=float(data.get(CONF_BATTERY_RESERVE_W) or 0.0),
            soc=read_percent(self.hass, data.get(CONF_BATTERY_SOC_ENTITY)),
            charging_w=reading.w,
        )

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

    async def async_force_on(self, subentry_id: str, seconds: float) -> None:
        """Schaltet sofort ein und hält es für die Dauer an.

        Das Einschalten geschieht hier und nicht über die Engine: Eine
        Zwangsfreigabe ist eine Anweisung des Nutzers, keine
        Automatikentscheidung. Sie wirkt deshalb auch bei ausgeschaltetem
        Hauptschalter — der ist der Not-Aus für die *Automatik*, nicht für die
        Bedienung.

        Die Automatik hält sich in dieser Zeit fern; siehe ``is_forced``.
        """
        now = dt_util.utcnow().timestamp()
        runtime = self.runtime_for(subentry_id)
        runtime.force_until = now + seconds
        # Verzögerungszähler zurücksetzen: Nach dem Ende der Freigabe soll die
        # Automatik neu bewerten und nicht auf einem alten Stand aufsetzen.
        runtime.on_condition_since = None
        runtime.off_condition_since = None
        self.schedule_save()

        consumer = self.consumers.get(subentry_id)
        if consumer is None:
            return

        _LOGGER.info("%s wird für %.0f s zwangsfreigegeben", consumer.name, seconds)
        await self.hass.services.async_call(
            HA_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: consumer.switch_entity},
            blocking=True,
        )
        await self.async_request_refresh_now()

        # Nach Ablauf einmal auswerten, sonst bliebe das Gerät bis zum nächsten
        # Ereignis an — der Takt käme zwar auch, aber erst nach bis zu 10 s.
        self._schedule_force_end(subentry_id, seconds)

    @callback
    def _schedule_force_end(self, subentry_id: str, seconds: float) -> None:
        cancel = self._force_timers.pop(subentry_id, None)
        if cancel is not None:
            cancel()

        async def _ended(_now: Any) -> None:
            self._force_timers.pop(subentry_id, None)
            await self._async_evaluate()

        self._force_timers[subentry_id] = async_call_later(self.hass, seconds + 1, _ended)

    async def async_clear_force(self, subentry_id: str) -> None:
        """Beendet eine Zwangsfreigabe vorzeitig.

        Ausgeschaltet wird dabei nicht: Ob das Gerät weiterlaufen darf,
        entscheidet ab jetzt wieder der Überschuss.
        """
        self.runtime_for(subentry_id).force_until = None
        if (cancel := self._force_timers.pop(subentry_id, None)) is not None:
            cancel()
        self.schedule_save()
        await self.async_request_refresh_now()

    async def async_pause(self, seconds: float | None = None) -> None:
        """Hält die Automatik an — auf Wunsch befristet.

        Der Hauptschalter kann nur an oder aus. "Zwei Stunden Ruhe" ist der
        häufigere Wunsch, etwa während einer Wartung.
        """
        await self.async_set_automation(False)

        if (cancel := self._pause_timer) is not None:
            cancel()
            self._pause_timer = None
        if seconds is None:
            return

        async def _ended(_now: Any) -> None:
            self._pause_timer = None
            await self.async_set_automation(True)

        _LOGGER.info("Automatik pausiert für %.0f s", seconds)
        self._pause_timer = async_call_later(self.hass, seconds, _ended)

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

    @property
    def smoothing_window(self) -> float:
        """Mittelungsfenster in Sekunden — auch für die Anzeige."""
        return self._smoothing_window()

    def battery_soc(self) -> float | None:
        """Aktueller Ladestand, oder None ohne Batterie."""
        data = dict(self.config_entry.data) if self.config_entry else {}
        return read_percent(self.hass, data.get(CONF_BATTERY_SOC_ENTITY))

    @property
    def battery_soc_entity(self) -> str | None:
        """Entität des Ladestands, damit die Karte den Detail-Dialog öffnen kann."""
        data = dict(self.config_entry.data) if self.config_entry else {}
        return data.get(CONF_BATTERY_SOC_ENTITY)

    @property
    def battery_priority(self) -> float:
        """Rang der Batterie als verschiebbare Last, 1 = höchste."""
        return self._battery_priority

    async def async_set_battery_priority(self, value: float) -> None:
        """Setzt den Rang der Batterie und wertet sofort neu aus."""
        self._battery_priority = value
        self.schedule_save()
        await self.async_request_refresh_now()

    # -- Speichern -----------------------------------------------------------

    @callback
    def schedule_save(self) -> None:
        """Schreibt den Laufzeitzustand verzögert."""
        self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "automation_enabled": self.automation_enabled,
            "battery_priority": self._battery_priority,
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
