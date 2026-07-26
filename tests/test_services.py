"""Die Dienste für den Eingriff von Hand.

Ihr gemeinsamer Zweck ist eine **Dauer** — die einzige Sache, die sich über
Entitäten allein nicht ausdrücken lässt. Entsprechend prüfen diese Tests vor
allem, was nach Ablauf der Frist geschieht.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)
from custom_components.energy_manager.services import (
    ATTR_DURATION,
    SERVICE_CLEAR_FORCE,
    SERVICE_FORCE_ON,
    SERVICE_PAUSE,
    SERVICE_RESUME,
)


def consumer_subentry(name: str, switch: str, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={CONF_NAME: name, CONF_SWITCH_ENTITY: switch, **extra},
    )


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
        subentries_data=[
            consumer_subentry("Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 2000})
        ],
    )


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def setup(hass: HomeAssistant, entry: MockConfigEntry, grid: str = "1000") -> None:
    """Standardmäßig OHNE Überschuss — so ist jede Schaltung eindeutig erzwungen."""
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": "W"})
    hass.states.async_set("switch.heizstab", STATE_OFF)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def geraet_id(hass: HomeAssistant, entry: MockConfigEntry, name: str) -> str:
    devices = dr.async_get(hass)
    device = next(
        d for d in dr.async_entries_for_config_entry(devices, entry.entry_id) if d.name == name
    )
    return device.id


def subentry_id(entry: MockConfigEntry) -> str:
    return next(iter(entry.runtime_data.consumers))


class TestRegistrierung:
    async def test_dienste_stehen_bereit(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        await setup(hass, entry)

        for name in (SERVICE_FORCE_ON, SERVICE_CLEAR_FORCE, SERVICE_PAUSE, SERVICE_RESUME):
            assert hass.services.has_service(DOMAIN, name), name

    async def test_dienste_verschwinden_beim_entladen(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        await setup(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(DOMAIN, SERVICE_FORCE_ON)


class TestForceOn:
    async def test_schaltet_sofort_ein_trotz_defizit(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Kern des Dienstes: Er übergeht die Überschussrechnung."""
        await setup(hass, entry)
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()
        schaltungen["on"].clear()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [geraet_id(hass, entry, "Heizstab")], ATTR_DURATION: {"minutes": 30}},
            blocking=True,
        )

        assert [c.data["entity_id"] for c in schaltungen["on"]] == ["switch.heizstab"]

    async def test_wirkt_auch_bei_ausgeschalteter_automatik(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Hauptschalter ist der Not-Aus für die Automatik, nicht für den Nutzer."""
        await setup(hass, entry)
        assert entry.runtime_data.automation_enabled is False

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [geraet_id(hass, entry, "Heizstab")], ATTR_DURATION: {"minutes": 30}},
            blocking=True,
        )

        assert [c.data["entity_id"] for c in schaltungen["on"]] == ["switch.heizstab"]

    async def test_automatik_schaltet_waehrenddessen_nicht_ab(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Ohne diesen Schutz wäre die Freigabe nach dem nächsten Takt vorbei."""
        await setup(hass, entry)
        coordinator = entry.runtime_data
        await coordinator.async_set_automation(True)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [geraet_id(hass, entry, "Heizstab")], ATTR_DURATION: {"minutes": 30}},
            blocking=True,
        )
        # Gerät läuft jetzt, Überschuss reicht nicht — die Automatik will es aus.
        hass.states.async_set("switch.heizstab", STATE_ON)
        schaltungen["off"].clear()

        await coordinator.async_request_refresh_now()
        await hass.async_block_till_done()

        assert schaltungen["off"] == []
        assert coordinator.data.blockers[subentry_id(entry)] == "forced"

    async def test_nimmt_auch_eine_entitaet_als_ziel(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Eine Automatisierung hat meist eine entity_id zur Hand, kein Gerät."""
        await setup(hass, entry)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_ENTITY_ID: ["sensor.heizstab_status"], ATTR_DURATION: {"minutes": 5}},
            blocking=True,
        )

        assert [c.data["entity_id"] for c in schaltungen["on"]] == ["switch.heizstab"]

    async def test_merkt_sich_das_ende(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)
        vorher = dt_util.utcnow().timestamp()

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [geraet_id(hass, entry, "Heizstab")], ATTR_DURATION: {"minutes": 30}},
            blocking=True,
        )

        runtime = entry.runtime_data.runtime_for(subentry_id(entry))
        assert runtime.force_until is not None
        assert runtime.force_until >= vorher + 1800

    async def test_weist_ein_fremdes_geraet_zurueck(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Hub ist kein Verbraucher — ein stiller Fehlschlag wäre schlimmer."""
        await setup(hass, entry)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_FORCE_ON,
                {
                    ATTR_DEVICE_ID: [geraet_id(hass, entry, "Energy Manager")],
                    ATTR_DURATION: {"minutes": 5},
                },
                blocking=True,
            )

    async def test_verlangt_ein_ziel(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, SERVICE_FORCE_ON, {ATTR_DURATION: {"minutes": 5}}, blocking=True
            )


class TestClearForce:
    async def test_beendet_die_freigabe_ohne_abzuschalten(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Ob das Gerät weiterlaufen darf, entscheidet ab jetzt der Überschuss."""
        await setup(hass, entry)
        device_id = geraet_id(hass, entry, "Heizstab")

        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [device_id], ATTR_DURATION: {"minutes": 30}},
            blocking=True,
        )
        schaltungen["off"].clear()

        await hass.services.async_call(
            DOMAIN, SERVICE_CLEAR_FORCE, {ATTR_DEVICE_ID: [device_id]}, blocking=True
        )

        assert entry.runtime_data.runtime_for(subentry_id(entry)).force_until is None
        assert schaltungen["off"] == []


class TestPause:
    async def test_haelt_die_automatik_an(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)
        coordinator = entry.runtime_data
        await coordinator.async_set_automation(True)

        await hass.services.async_call(DOMAIN, SERVICE_PAUSE, {}, blocking=True)

        assert coordinator.automation_enabled is False

    async def test_resume_schaltet_wieder_scharf(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)

        await hass.services.async_call(DOMAIN, SERVICE_RESUME, {}, blocking=True)

        assert entry.runtime_data.automation_enabled is True

    async def test_befristete_pause_merkt_sich_einen_timer(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Das ist der Grund für den Dienst: Der Hauptschalter kennt keine Dauer."""
        await setup(hass, entry)
        coordinator = entry.runtime_data
        await coordinator.async_set_automation(True)

        await hass.services.async_call(
            DOMAIN, SERVICE_PAUSE, {ATTR_DURATION: {"hours": 2}}, blocking=True
        )

        assert coordinator.automation_enabled is False
        assert coordinator._pause_timer is not None

    async def test_timer_wird_beim_entladen_abgeraeumt(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Ein Timer, der auf einen entladenen Eintrag zugreift, wäre ein Fehler."""
        await setup(hass, entry)
        coordinator = entry.runtime_data

        await hass.services.async_call(
            DOMAIN, SERVICE_PAUSE, {ATTR_DURATION: {"hours": 2}}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ON,
            {ATTR_DEVICE_ID: [geraet_id(hass, entry, "Heizstab")], ATTR_DURATION: {"hours": 1}},
            blocking=True,
        )

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert coordinator._pause_timer is None
        assert coordinator._force_timers == {}
