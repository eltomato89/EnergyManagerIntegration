"""Nachträglich ergänzte Angaben an einem Verbraucher.

Der berichtete Ablauf: Verbraucher ohne Leistungssensor anlegen, ihn in der
Karte sehen, dann den Sensor nachtragen. Greift die Änderung ohne Neustart?
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Pumpe",
                unique_id=None,
                # Bewusst OHNE Leistungssensor.
                data={CONF_NAME: "Pumpe", CONF_SWITCH_ENTITY: "switch.pumpe"},
            )
        ],
    )
    hass.states.async_set("sensor.netz", "-3000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.pumpe", STATE_OFF)
    hass.states.async_set("sensor.pumpe_leistung", "394", {"unit_of_measurement": "W"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_ohne_leistungssensor_ist_die_leistung_unbekannt(hass: HomeAssistant) -> None:
    """Nicht 0 W: 'unbekannt' und 'null' sind verschiedene Aussagen.

    Die Karte zeigt bei ``None`` gar nichts an; eine 0 waere die Behauptung,
    das Geraet ziehe nachweislich nichts.
    """
    await _setup(hass)

    status = hass.states.get("sensor.pumpe_status")
    assert status.attributes["power_entity"] is None
    assert status.attributes["power_w"] is None


async def test_nachgetragener_sensor_greift_ohne_neustart(hass: HomeAssistant) -> None:
    """Über den echten Bearbeitungsdialog, nicht über die interne Methode."""
    entry = await _setup(hass)
    subentry = next(iter(entry.subentries.values()))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Pumpe",
            CONF_SWITCH_ENTITY: "switch.pumpe",
            CONF_POWER_ENTITY: "sensor.pumpe_leistung",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    status = hass.states.get("sensor.pumpe_status")
    assert status.attributes["power_entity"] == "sensor.pumpe_leistung"
    assert status.attributes["power_w"] == 394


async def test_der_neue_sensor_wird_auch_beobachtet(hass: HomeAssistant) -> None:
    """Sonst bliebe der Wert auf dem Stand des Nachtragens stehen."""
    entry = await _setup(hass)
    subentry = next(iter(entry.subentries.values()))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Pumpe",
            CONF_SWITCH_ENTITY: "switch.pumpe",
            CONF_POWER_ENTITY: "sensor.pumpe_leistung",
        },
    )
    await hass.async_block_till_done()

    # Der Sensor meldet einen neuen Wert — ohne erneutes Zutun.
    hass.states.async_set("sensor.pumpe_leistung", "512", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.pumpe_status").attributes["power_w"] == 512
