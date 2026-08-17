"""Der Einrichtungsablauf für regelbare Verbraucher.

Zwei Dinge sind hier zu prüfen. Erstens: Ein rein schaltbarer Verbraucher
bekommt weiterhin **ein** Formular und ist danach fertig — der zusätzliche
Schritt darf niemanden betreffen, der ihn nicht braucht.

Zweitens, und wichtiger: Was sich nicht in ein Stufenraster übersetzen lässt,
wird **abgewiesen**. Das ist die einzige Stelle, an der ein falsch verstandenes
Gerät noch aufzuhalten ist. Wird eine Ampere-Entität als Watt gelesen, entsteht
eine Leiter von 6 bis 16 W: Sie passt in jeden Überschuss, die Automatik stellt
16 — und das Gerät zieht 16 A.
"""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_GRID_ENTITY,
    CONF_LEVEL_HOLD,
    CONF_LEVEL_MAP,
    CONF_METER_MODE,
    CONF_MIN_LEVEL_W,
    CONF_NAME,
    CONF_PHASES,
    CONF_SWITCH_ENTITY,
    CONSUMER_TYPE_MODULATING,
    CONSUMER_TYPE_SWITCH,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)

OPTIONEN = ["aus", "niedrig", "mittel", "hoch"]


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={},
        unique_id=DOMAIN,
    )


def wallbox_number(hass: HomeAssistant, **attrs) -> None:
    basis = {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"}
    hass.states.async_set("number.ladestrom", "6", {**basis, **attrs})


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    entry.add_to_hass(hass)
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": config_entries.SOURCE_USER},
    )


async def grundangaben(
    hass: HomeAssistant,
    result: dict,
    typ: str = CONSUMER_TYPE_MODULATING,
    switch: str = "switch.wallbox",
) -> dict:
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Wallbox", CONF_SWITCH_ENTITY: switch, CONF_CONSUMER_TYPE: typ},
    )


def gespeichert(entry: MockConfigEntry) -> dict:
    return next(iter(entry.subentries.values())).data


class TestSchaltbarBleibtEinschrittig:
    async def test_kein_zweiter_schritt(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        """Der Zusatzschritt darf niemanden betreffen, der ihn nicht braucht."""
        result = await start(hass, entry)
        result = await grundangaben(hass, result, typ=CONSUMER_TYPE_SWITCH)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert gespeichert(entry)[CONF_CONSUMER_TYPE] == CONSUMER_TYPE_SWITCH
        assert CONF_CONTROL_ENTITY not in gespeichert(entry)


class TestNumberInAmpere:
    async def test_ablauf(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        wallbox_number(hass)
        result = await start(hass, entry)
        result = await grundangaben(hass, result)
        assert result["step_id"] == "control"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_CONTROL_ENTITY: "number.ladestrom",
                CONF_PHASES: "3",
                CONF_LEVEL_HOLD: 300,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        await hass.async_block_till_done()
        daten = gespeichert(entry)
        assert daten[CONF_CONTROL_ENTITY] == "number.ladestrom"
        # Die Auswahlliste liefert Zeichenketten; gespeichert gehört eine Zahl.
        assert daten[CONF_PHASES] == 3
        assert daten[CONF_LEVEL_HOLD] == 300

    async def test_ohne_einheit_abgewiesen(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """Der Fall, der ohne diese Prüfung um den Faktor 690 danebenliegt."""
        hass.states.async_set("number.ladestrom", "6", {"min": 6, "max": 16, "step": 1})
        result = await start(hass, entry)
        result = await grundangaben(hass, result)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_CONTROL_ENTITY: "number.ladestrom", CONF_PHASES: "3"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_CONTROL_ENTITY: "control_unit_required"}

    async def test_falsche_einheit_abgewiesen(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        wallbox_number(hass, unit_of_measurement="°C")
        result = await start(hass, entry)
        result = await grundangaben(hass, result)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "number.ladestrom", CONF_PHASES: "1"}
        )

        assert result["errors"] == {CONF_CONTROL_ENTITY: "control_unit_required"}

    async def test_ohne_zustand_abgewiesen(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        result = await start(hass, entry)
        result = await grundangaben(hass, result)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "number.ladestrom", CONF_PHASES: "1"}
        )

        assert result["errors"] == {CONF_CONTROL_ENTITY: "control_entity_unavailable"}

    async def test_mindeststufe_ueber_dem_maximum(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """Der Fehler gehört an das Feld, das ihn verursacht hat."""
        wallbox_number(hass)
        result = await start(hass, entry)
        result = await grundangaben(hass, result)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_CONTROL_ENTITY: "number.ladestrom",
                CONF_PHASES: "3",
                CONF_MIN_LEVEL_W: 20000,
            },
        )

        assert result["errors"] == {CONF_MIN_LEVEL_W: "min_level_too_high"}


class TestSelect:
    async def test_ablauf_mit_zuordnung(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        hass.states.async_set("select.heizstab", "aus", {"options": OPTIONEN})
        result = await start(hass, entry)
        result = await grundangaben(hass, result, switch="switch.heizstab")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "select.heizstab", CONF_PHASES: "1"}
        )
        assert result["step_id"] == "levels"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {"aus": 0, "niedrig": 1400, "mittel": 2400, "hoch": 3600},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        await hass.async_block_till_done()
        # Die Aus-Stellung wird keine Stufe.
        assert gespeichert(entry)[CONF_LEVEL_MAP] == {
            "niedrig": 1400,
            "mittel": 2400,
            "hoch": 3600,
        }

    async def test_alles_null_ergibt_keine_stufe(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        hass.states.async_set("select.heizstab", "aus", {"options": OPTIONEN})
        result = await start(hass, entry)
        result = await grundangaben(hass, result, switch="switch.heizstab")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "select.heizstab", CONF_PHASES: "1"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], dict.fromkeys(OPTIONEN, 0)
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "no_levels"}

    async def test_ohne_optionen_zurueck_zur_entitaet(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """Ohne Optionen lässt sich kein Feld je Stufe anbieten."""
        hass.states.async_set("select.heizstab", "aus", {})
        result = await start(hass, entry)
        result = await grundangaben(hass, result, switch="switch.heizstab")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "select.heizstab", CONF_PHASES: "1"}
        )

        assert result["step_id"] == "control"
        assert result["errors"] == {CONF_CONTROL_ENTITY: "select_not_ready"}


class TestBearbeiten:
    def _entry_mit_wallbox(self) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            title="Energy Manager",
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            options={},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Wallbox",
                    unique_id=None,
                    data={
                        CONF_NAME: "Wallbox",
                        CONF_SWITCH_ENTITY: "switch.wallbox",
                        CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
                        CONF_CONTROL_ENTITY: "number.ladestrom",
                        CONF_PHASES: 3,
                    },
                )
            ],
        )

    async def _bearbeiten(self, hass: HomeAssistant, entry: MockConfigEntry) -> dict:
        entry.add_to_hass(hass)
        subentry_id = next(iter(entry.subentries))
        return await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
            context={"source": "reconfigure", "subentry_id": subentry_id},
        )

    async def test_erkanntes_raster_steht_im_formular(self, hass: HomeAssistant) -> None:
        """Der Nachweis, dass die Integration die Entität versteht.

        Abzulesen, **bevor** scharfgeschaltet wird. 6 bis 16 A dreiphasig ergeben
        11 Stufen von 4140 bis 11040 W.
        """
        wallbox_number(hass)
        entry = self._entry_mit_wallbox()
        result = await self._bearbeiten(hass, entry)
        result = await grundangaben(hass, result)

        assert result["step_id"] == "control"
        assert result["description_placeholders"]["detected"] == "11 @ 4140-11040 W"

    async def test_aendern_behaelt_den_typ(self, hass: HomeAssistant) -> None:
        wallbox_number(hass)
        entry = self._entry_mit_wallbox()
        result = await self._bearbeiten(hass, entry)
        result = await grundangaben(hass, result)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CONTROL_ENTITY: "number.ladestrom", CONF_PHASES: "1"}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert gespeichert(entry)[CONF_PHASES] == 1

    async def test_wechsel_auf_schaltbar_laesst_keine_reste(self, hass: HomeAssistant) -> None:
        """Sonst blieben Steuerentität und Phasenzahl in den Daten stehen.

        Sie wirkten dort nicht mehr, tauchten aber in den Attributen auf — und
        beim nächsten Umstellen auf regelbar wären sie stillschweigend wieder da.
        """
        wallbox_number(hass)
        entry = self._entry_mit_wallbox()
        result = await self._bearbeiten(hass, entry)
        result = await grundangaben(hass, result, typ=CONSUMER_TYPE_SWITCH)

        assert result["type"] is FlowResultType.ABORT
        await hass.async_block_till_done()
        daten = gespeichert(entry)
        assert daten[CONF_CONSUMER_TYPE] == CONSUMER_TYPE_SWITCH
        assert CONF_CONTROL_ENTITY not in daten
        assert CONF_PHASES not in daten
