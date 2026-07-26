"""Einrichtung, Optionen und Verbraucher-Subentries."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    BATTERY_MODE_CHARGE_ONLY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_CONSUMPTION_ENTITY,
    CONF_GRID_ENTITY,
    CONF_INVERT_GRID,
    CONF_METER_MODE,
    CONF_MIN_OFF_TIME,
    CONF_NAME,
    CONF_PRODUCTION_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    METER_MODE_SPLIT,
    SUBENTRY_TYPE_CONSUMER,
)


async def test_grid_flow(hass: HomeAssistant) -> None:
    """Einrichtung mit bidirektionalem Netzsensor."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER_MODE: METER_MODE_GRID}
    )
    assert result["step_id"] == "grid"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GRID_ENTITY: "sensor.netz", CONF_INVERT_GRID: False},
    )
    assert result["step_id"] == "battery"

    # Ohne Batterie: leer bestätigen.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRID_ENTITY] == "sensor.netz"
    assert result["data"][CONF_METER_MODE] == METER_MODE_GRID


async def test_split_flow_mit_batterie(hass: HomeAssistant) -> None:
    """Einrichtung mit getrennten Sensoren und Batterie."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER_MODE: METER_MODE_SPLIT}
    )
    assert result["step_id"] == "split"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PRODUCTION_ENTITY: "sensor.pv",
            CONF_CONSUMPTION_ENTITY: "sensor.haus",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_POWER_ENTITY: "sensor.batterie",
            "battery_mode": BATTERY_MODE_CHARGE_ONLY,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRODUCTION_ENTITY] == "sensor.pv"
    assert result["data"][CONF_BATTERY_POWER_ENTITY] == "sensor.batterie"


async def test_leere_felder_werden_nicht_gespeichert(hass: HomeAssistant) -> None:
    """Ein leeres Formularfeld ist etwas anderes als ein gespeicherter Leerwert."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER_MODE: METER_MODE_GRID}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_GRID_ENTITY: "sensor.netz"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    # Kein battery_soc_entity mit leerem Wert in den Daten.
    assert "battery_soc_entity" not in result["data"]


async def test_nur_eine_instanz(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Ein zweiter Eintrag wird abgewiesen."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Regelungsparameter lassen sich nachträglich ändern."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SMOOTHING_WINDOW: 90, CONF_SETTLE_TIME: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SMOOTHING_WINDOW] == 90


async def test_verbraucher_anlegen(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Ein Verbraucher wird als Subentry angelegt."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Wallbox",
            CONF_SWITCH_ENTITY: "switch.wallbox",
            CONF_MIN_OFF_TIME: 600,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wallbox"

    await hass.async_block_till_done()
    subentries = list(mock_config_entry.subentries.values())
    assert len(subentries) == 1
    assert subentries[0].data[CONF_SWITCH_ENTITY] == "switch.wallbox"
    assert subentries[0].data[CONF_MIN_OFF_TIME] == 600


async def test_verbraucher_bearbeiten(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Ein vorhandener Verbraucher lässt sich ändern."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": config_entries.SOURCE_USER},
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Wallbox", CONF_SWITCH_ENTITY: "switch.wallbox"},
    )
    await hass.async_block_till_done()

    subentry_id = next(iter(mock_config_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Wallbox Garage", CONF_SWITCH_ENTITY: "switch.wallbox"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    await hass.async_block_till_done()
    assert mock_config_entry.subentries[subentry_id].title == "Wallbox Garage"


class TestDoppelteSchaltEntitaet:
    """Zwei Verbraucher für dasselbe Gerät sind immer ein Versehen.

    Meist eine verwechselte Entität, weil deren ID nicht zum Anzeigenamen passt.
    Die Automatik verplant das Gerät dann doppelt: rechnet den Bedarf zweimal
    ab, hält es für zweimal schaltbar und führt zwei getrennte Sperrzeiten.
    """

    async def test_anlegen_wird_abgelehnt(self, hass: HomeAssistant) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Erster",
                    unique_id=None,
                    data={CONF_NAME: "Erster", CONF_SWITCH_ENTITY: "switch.geteilt"},
                )
            ],
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER), context={"source": "user"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Zweiter", CONF_SWITCH_ENTITY: "switch.geteilt"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_SWITCH_ENTITY: "switch_in_use"}

    async def test_ein_freies_geraet_geht_durch(self, hass: HomeAssistant) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Erster",
                    unique_id=None,
                    data={CONF_NAME: "Erster", CONF_SWITCH_ENTITY: "switch.eins"},
                )
            ],
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER), context={"source": "user"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Zweiter", CONF_SWITCH_ENTITY: "switch.zwei"},
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_der_eigene_eintrag_blockiert_sich_nicht(self, hass: HomeAssistant) -> None:
        """Beim Bearbeiten darf die eigene Entität bleiben."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Pumpe",
                    unique_id=None,
                    data={CONF_NAME: "Pumpe", CONF_SWITCH_ENTITY: "switch.pumpe"},
                )
            ],
        )
        entry.add_to_hass(hass)
        subentry = next(iter(entry.subentries.values()))

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
            context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Pumpe neu", CONF_SWITCH_ENTITY: "switch.pumpe"},
        )

        assert result["type"] is FlowResultType.ABORT


class TestNullwerte:
    async def test_null_bei_der_nennleistung_wird_nicht_gespeichert(
        self, hass: HomeAssistant
    ) -> None:
        """Sie sähe im Attribut wie eine Angabe aus, wirkt aber nicht."""
        from custom_components.energy_manager.const import CONF_HYSTERESIS, CONF_MAX_POWER
        from custom_components.energy_manager.schemas import clean

        gereinigt = clean(
            {
                CONF_NAME: "X",
                CONF_SWITCH_ENTITY: "switch.x",
                CONF_MAX_POWER: 0,
                # Hier bedeutet 0 "aus" und gehört gespeichert.
                CONF_HYSTERESIS: 0,
            }
        )
        assert CONF_MAX_POWER not in gereinigt
        assert gereinigt[CONF_HYSTERESIS] == 0
