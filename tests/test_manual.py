"""Manuelle Übersteuerung: nach einem Eingriff von außen hält sich die Automatik fern.

Aufbauend auf der Erkennung aus ``test_foreign.py``, die nur festhält. Hier wird
sie wirksam — aber **nur mit eingetragener Dauer**. Der Rückfallwert 0 bedeutet,
dass bestehende Verbraucher nichts davon merken, und das ist Absicht: Ob eine
Übersteuerung sinnvoll ist, hängt an der Geräteklasse und nicht am Nutzer.

Zwei Eigenschaften sind wichtiger als alles andere in dieser Datei:

* Die Sperre ist **befristet**. Es gibt eine Klasse von Fehlerkennungen, die
  nicht wegzufiltern ist — Geräte, die ihren eigenen Zustand ändern. Eine
  befristete Sperre läuft dort von selbst ab.
* Der ``managed``-Schalter wird **nie** von der Integration geschrieben. Er ist
  Nutzerkonfiguration; würde die Integration darauf schreiben, wäre eine
  Fehlerkennung dauerhafter Schaden statt eines vorübergehenden Zustands.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MANUAL_OVERRIDE_TIME,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)

SCHALTER = "switch.heizstab"
DAUER = 1800


def make_entry(**extra) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60},
        unique_id=DOMAIN,
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Heizstab",
                unique_id=None,
                data={
                    CONF_NAME: "Heizstab",
                    CONF_SWITCH_ENTITY: SCHALTER,
                    CONF_MAX_POWER: 2000,
                    **extra,
                },
            )
        ],
    )


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def setup(hass: HomeAssistant, entry: MockConfigEntry, state: str = STATE_ON) -> None:
    """3000 W Überschuss: genug, um den Heizstab einzuschalten."""
    hass.states.async_set("sensor.netz", "-3000", {"unit_of_measurement": "W"})
    hass.states.async_set(SCHALTER, state)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def von_hand(hass: HomeAssistant, state: str) -> None:
    """Jemand anders schaltet — mit frischem Context."""
    hass.states.async_set(SCHALTER, state, context=Context())
    await hass.async_block_till_done()


def id_of(entry: MockConfigEntry) -> str:
    return next(iter(entry.runtime_data.consumers))


def runtime(entry: MockConfigEntry):
    return entry.runtime_data.runtime_for(id_of(entry))


class TestMitDauer:
    async def test_ausschalten_von_hand_wird_nicht_ueberstimmt(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Der Defekt, um den es geht.

        Ohne diese Sperre schaltet die Automatik binnen Sekunden wieder ein:
        ``turn_on_delay`` ist standardmäßig 0, und ``compute_lock`` greift nicht,
        weil die letzte Schaltung nicht von ihr kam.
        """
        entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
        await setup(hass, entry)
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()
        schaltungen["on"].clear()
        schaltungen["off"].clear()

        # Der Nutzer schaltet aus. Danach das Beruhigungsfenster aufheben, sonst
        # deckte schon es den Fall ab.
        await von_hand(hass, STATE_OFF)
        runtime(entry).settle_until = dt_util.utcnow().timestamp() - 1
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        assert not schaltungen["on"]
        assert entry.runtime_data.data.blockers[id_of(entry)] == "manual"

    async def test_der_automatik_schalter_bleibt_unangetastet(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Die Invariante: ``managed`` ist Nutzerkonfiguration.

        Schriebe die Integration darauf, wäre eine Fehlerkennung dauerhafter
        Schaden — und der Nutzer könnte „das habe ich abgeschaltet" nicht von
        „das hat das System für mich abgeschaltet" unterscheiden.
        """
        entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)

        assert entry.runtime_data.is_managed(id_of(entry)) is True

    async def test_die_sperre_laeuft_ab(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Deshalb heilt eine Fehlerkennung von selbst."""
        entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)
        assert runtime(entry).manual_until is not None

        # Vorspulen und das Beruhigungsfenster aufheben.
        jetzt = dt_util.utcnow().timestamp()
        runtime(entry).manual_until = jetzt - 1
        runtime(entry).settle_until = jetzt - 1
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert [call.data["entity_id"] for call in schaltungen["on"]] == [SCHALTER]

    async def test_vorzeitig_beenden(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Ausgeschaltet wird dabei nicht: ab jetzt entscheidet der Überschuss."""
        entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)
        assert runtime(entry).manual_until is not None

        await entry.runtime_data.async_clear_manual(id_of(entry))

        assert runtime(entry).manual_until is None

    async def test_ueberlebt_einen_neustart(self, hass: HomeAssistant, schaltungen: dict) -> None:
        entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)

        from custom_components.energy_manager.models import ConsumerRuntime

        wieder = ConsumerRuntime.from_dict(runtime(entry).as_dict())
        assert wieder.manual_until == runtime(entry).manual_until

    async def test_wird_nicht_verdraengt(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Sonst wäre die Übersteuerung nur halb wirksam.

        Ein wichtigerer Verbraucher darf sich nicht die Leistung eines Geräts
        holen, das ausdrücklich in Ruhe gelassen werden soll.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Energy Manager",
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Waermepumpe",
                    unique_id=None,
                    data={
                        CONF_NAME: "Waermepumpe",
                        CONF_SWITCH_ENTITY: "switch.waermepumpe",
                        CONF_MAX_POWER: 2000,
                    },
                ),
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Heizstab",
                    unique_id=None,
                    data={
                        CONF_NAME: "Heizstab",
                        CONF_SWITCH_ENTITY: SCHALTER,
                        CONF_MAX_POWER: 2000,
                        CONF_MANUAL_OVERRIDE_TIME: DAUER,
                    },
                ),
            ],
        )
        hass.states.async_set("sensor.netz", "0", {"unit_of_measurement": "W"})
        hass.states.async_set("switch.waermepumpe", STATE_OFF)
        hass.states.async_set(SCHALTER, STATE_ON)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data
        ids = {value.name: key for key, value in coordinator.consumers.items()}
        await coordinator.async_set_priority(ids["Waermepumpe"], 1.0)
        await coordinator.async_set_priority(ids["Heizstab"], 2.0)
        await hass.async_block_till_done()

        # Zuerst der Nachweis, dass ohne Übersteuerung verdrängt würde.
        pumpe = next(v for v in coordinator.data.consumers if v.config.name == "Waermepumpe")
        assert pumpe.displaceable == (ids["Heizstab"],)

        # Jetzt liegt eine Übersteuerung an. Direkt gesetzt statt über einen
        # Zustandswechsel: Der Heizstab läuft schon, ein Einschalten von Hand
        # wäre also gar kein Wechsel — und geprüft werden soll hier ohnehin die
        # Wirkung der Sperre, nicht ihre Erkennung. Die steht in test_foreign.py.
        coordinator.runtime_for(ids["Heizstab"]).manual_until = dt_util.utcnow().timestamp() + DAUER
        await coordinator.async_request_refresh_now()
        await hass.async_block_till_done()

        pumpe = next(v for v in coordinator.data.consumers if v.config.name == "Waermepumpe")
        assert pumpe.displaceable == ()
        assert pumpe.throttleable == ()


class TestOhneDauer:
    """Der Rückfallwert 0 — das Verhalten bestehender Verbraucher."""

    async def test_keine_sperre(self, hass: HomeAssistant, schaltungen: dict) -> None:
        entry = make_entry()
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)

        assert runtime(entry).manual_until is None

    async def test_der_eingriff_wird_trotzdem_festgehalten(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Die Messung läuft weiter, auch ohne Wirkung.

        Sie ist die Grundlage, um die Dauer überhaupt sinnvoll zu wählen.
        """
        entry = make_entry()
        await setup(hass, entry)
        await von_hand(hass, STATE_OFF)

        assert runtime(entry).last_foreign_change is not None

    async def test_die_automatik_schaltet_wie_bisher(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        entry = make_entry()
        await setup(hass, entry, state=STATE_OFF)
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert [call.data["entity_id"] for call in schaltungen["on"]] == [SCHALTER]


async def test_attribut_am_status_sensor(hass: HomeAssistant, schaltungen: dict) -> None:
    """Damit eine Anzeige den Countdown führen kann, ohne die Dauer zu kennen."""
    entry = make_entry(**{CONF_MANUAL_OVERRIDE_TIME: DAUER})
    await setup(hass, entry)
    await von_hand(hass, STATE_OFF)
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()

    status = next(
        state
        for state in hass.states.async_all("sensor")
        if state.attributes.get("consumer_name") == "Heizstab"
    )

    assert status.attributes["manual_until"] is not None
    assert status.attributes["blocked_by"] == "manual"
