"""Verdrängung: Priorität schlägt "wer zuerst kommt".

Ohne diesen Weg wäre die Priorität nur eine Reihenfolge beim Zuschalten. Zwei
kleine Verbraucher könnten einen großen mit höherem Rang dauerhaft aussperren —
egal wie weit der Überschuss steigt, denn ihre Last fehlt ihm ja gerade.

Der Fall, an dem das auffiel: 800 W Überschuss, V1 braucht 1000 W, V2 500 W,
V3 200 W. V2 und V3 gehen an. Steigt der Überschuss auf 1100 W, bleiben nur
400 W frei — V1 käme nie zum Zug.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)


def consumer(name: str, watt: int, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={
            CONF_NAME: name,
            CONF_SWITCH_ENTITY: f"switch.{name.lower()}",
            CONF_POWER_ENTITY: f"sensor.{name.lower()}_leistung",
            CONF_MAX_POWER: watt,
            **extra,
        },
    )


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def aufbau(
    hass: HomeAssistant,
    consumers: list[ConfigSubentryData],
    *,
    ueberschuss: int,
    laufend: dict[str, int] | None = None,
    settle: int = 0,
) -> MockConfigEntry:
    """Richtet ein und setzt die Prioritäten in der Reihenfolge der Liste.

    `laufend` nennt die Verbraucher, die schon laufen, samt ihrer Ist-Leistung.
    Das Netz zeigt entsprechend weniger Einspeisung — so, wie es in einer
    echten Anlage aussähe.
    """
    laufend = laufend or {}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: settle},
        unique_id=DOMAIN,
        subentries_data=consumers,
    )

    # Netz = -(Überschuss - laufende Last): was gerade übrig bleibt.
    frei = ueberschuss - sum(laufend.values())
    hass.states.async_set("sensor.netz", str(-frei), {"unit_of_measurement": "W"})

    for sub in consumers:
        name = sub["data"][CONF_NAME].lower()
        watt = laufend.get(name, 0)
        hass.states.async_set(f"switch.{name}", STATE_ON if name in laufend else STATE_OFF)
        hass.states.async_set(f"sensor.{name}_leistung", str(watt), {"unit_of_measurement": "W"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}
    for rang, sub in enumerate(consumers, start=1):
        await coordinator.async_set_priority(ids[sub["data"][CONF_NAME]], rang)
    await hass.async_block_till_done()
    return entry


def geschaltet(schaltungen: dict[str, list], richtung: str) -> list[str]:
    return [call.data["entity_id"] for call in schaltungen[richtung]]


class TestDerBerichteteFall:
    async def test_grosser_verbraucher_verdraengt_zwei_kleine(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """1100 W Überschuss, V2+V3 laufen mit 700 W. V1 braucht 1000 W.

        400 W sind frei, 700 W ließen sich zurückholen — zusammen genug.
        """
        entry = await aufbau(
            hass,
            [consumer("V1", 1000), consumer("V2", 500), consumer("V3", 200)],
            ueberschuss=1100,
            laufend={"v2": 500, "v3": 200},
        )
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        # Die unwichtigsten weichen zuerst, dann kommt V1.
        assert set(geschaltet(schaltungen, "off")) == {"switch.v2", "switch.v3"}
        assert geschaltet(schaltungen, "on") == ["switch.v1"]

    async def test_bei_800_w_reicht_es_auch_zusammen_nicht(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """100 W frei + 700 W aus V2/V3 = 800 W. V1 braucht 1000 W.

        Dann wird niemand abgeschaltet: Man hätte zwei Geräte ausgemacht und
        trotzdem nichts gewonnen.
        """
        entry = await aufbau(
            hass,
            [consumer("V1", 1000), consumer("V2", 500), consumer("V3", 200)],
            ueberschuss=800,
            laufend={"v2": 500, "v3": 200},
        )
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert geschaltet(schaltungen, "off") == []
        assert geschaltet(schaltungen, "on") == []

    async def test_nur_so_viele_wie_noetig_weichen(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """V1 braucht 1000, 800 W sind frei — 200 W fehlen.

        V3 allein bringt sie. V2 darf weiterlaufen.
        """
        entry = await aufbau(
            hass,
            [consumer("V1", 1000), consumer("V2", 500), consumer("V3", 200)],
            ueberschuss=1500,
            laufend={"v2": 500, "v3": 200},
        )
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert geschaltet(schaltungen, "off") == ["switch.v3"]
        assert geschaltet(schaltungen, "on") == ["switch.v1"]


class TestWerNichtWeichenMuss:
    async def test_mindestlaufzeit_schuetzt_vor_verdraengung(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Ein angefangener Waschgang wird nicht abgebrochen.

        Eine Verdrängung ist kein Freibrief, die Zeitschutzfelder zu übergehen.
        """
        entry = await aufbau(
            hass,
            [
                consumer("V1", 1000),
                consumer("V2", 500, **{CONF_MIN_RUNTIME: 3600}),
                consumer("V3", 200),
            ],
            ueberschuss=1100,
            laufend={"v2": 500, "v3": 200},
        )
        coordinator = entry.runtime_data
        ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}

        # So, als hätte die Integration V2 gerade eingeschaltet.
        runtime = coordinator.runtime_for(ids["V2"])
        runtime.last_switch_ts = dt_util.utcnow().timestamp()
        runtime.last_switch_to = True

        await coordinator.async_set_automation(True)
        await hass.async_block_till_done()

        # V3 allein bringt nur 200 W, V1 fehlen 600 — also passiert nichts.
        assert geschaltet(schaltungen, "off") == []
        assert geschaltet(schaltungen, "on") == []

    async def test_wer_nicht_an_der_automatik_teilnimmt_bleibt_unangetastet(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Sonst würde die Automatik ein Gerät abschalten, das sie nicht führt."""
        entry = await aufbau(
            hass,
            [consumer("V1", 1000), consumer("V2", 500), consumer("V3", 200)],
            ueberschuss=1100,
            laufend={"v2": 500, "v3": 200},
        )
        coordinator = entry.runtime_data
        ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}

        await coordinator.async_set_managed(ids["V2"], False)
        await coordinator.async_set_automation(True)
        await hass.async_block_till_done()

        assert geschaltet(schaltungen, "off") == []
        assert geschaltet(schaltungen, "on") == []

    async def test_hoehere_prioritaet_weicht_nicht_fuer_niedrigere(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Die Richtung ist eindeutig — sonst wäre es kein Vorrang."""
        entry = await aufbau(
            hass,
            # V1 läuft und ist der wichtigste; V3 will an.
            [consumer("V1", 500), consumer("V2", 200), consumer("V3", 1000)],
            ueberschuss=1100,
            laufend={"v1": 500, "v2": 200},
        )
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert geschaltet(schaltungen, "off") == []
        assert geschaltet(schaltungen, "on") == []


class TestNachwirkung:
    async def test_verdraengte_werden_nicht_sofort_zurueckgeholt(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Sonst pendelte die Anlage: aus, an, aus, an.

        Nach der Verdrängung liegt für jeden Beteiligten ein
        Beruhigungsfenster; zusätzlich rechnet die Antizipation die Last des
        Neuen bereits heraus.
        """
        entry = await aufbau(
            hass,
            [consumer("V1", 1000), consumer("V2", 500), consumer("V3", 200)],
            ueberschuss=1100,
            laufend={"v2": 500, "v3": 200},
            settle=60,
        )
        coordinator = entry.runtime_data
        await coordinator.async_set_automation(True)
        await hass.async_block_till_done()
        assert len(schaltungen["on"]) == 1

        # Der Zähler zeigt die Umschaltung noch nicht — der gefährliche Moment.
        await coordinator.async_request_refresh_now()
        await hass.async_block_till_done()

        assert len(schaltungen["on"]) == 1, "V2/V3 wurden sofort zurückgeholt"
        for view in coordinator.data.consumers:
            if view.config.name in ("V2", "V3"):
                assert coordinator.data.blockers[view.config.subentry_id] == "settling"
