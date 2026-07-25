"""Entitäten, Geräte und ihr Zusammenspiel mit dem Koordinator."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_OFF_TIME,
    CONF_NAME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)


def consumer_subentry(name: str, switch: str, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={CONF_NAME: name, CONF_SWITCH_ENTITY: switch, **extra},
    )


@pytest.fixture
def entry_mit_verbrauchern() -> MockConfigEntry:
    """Ein Eintrag mit Netzsensor und zwei Verbrauchern, Glättung aus."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        # Glättung aus, damit die Tests den Momentanwert sehen.
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
        subentries_data=[
            consumer_subentry("Wallbox", "switch.wallbox", **{CONF_MAX_POWER: 11000}),
            consumer_subentry(
                "Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 2000, CONF_MIN_OFF_TIME: 600}
            ),
        ],
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.states.async_set("sensor.netz", "-2000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.wallbox", STATE_OFF)
    hass.states.async_set("switch.heizstab", STATE_OFF)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_geraete_werden_angelegt(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Ein Hub-Gerät plus je Verbraucher ein Gerät."""
    await setup_integration(hass, entry_mit_verbrauchern)

    devices = dr.async_get(hass)
    geraete = dr.async_entries_for_config_entry(devices, entry_mit_verbrauchern.entry_id)
    namen = {d.name for d in geraete}

    assert "Energy Manager" in namen
    assert "Wallbox" in namen
    assert "Heizstab" in namen

    # Die Verbraucher hängen unter dem Hub.
    hub = next(d for d in geraete if d.name == "Energy Manager")
    wallbox = next(d for d in geraete if d.name == "Wallbox")
    assert wallbox.via_device_id == hub.id


async def test_entitaeten_je_verbraucher(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Vier Entitäten je Verbraucher, drei am Hub."""
    await setup_integration(hass, entry_mit_verbrauchern)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry_mit_verbrauchern.entry_id)
    schluessel = {e.unique_id.split("_")[-1] for e in entities}

    assert {"priority", "managed", "status", "until"} <= schluessel
    # Hub: automation, surplus, raw, status
    assert any(e.unique_id.endswith("_surplus") for e in entities)
    assert any(e.unique_id.endswith("_automation") for e in entities)


async def test_ueberschuss_wird_berechnet(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Der Sensor zeigt den Überschuss aus dem Netzsensor."""
    await setup_integration(hass, entry_mit_verbrauchern)

    coordinator = entry_mit_verbrauchern.runtime_data
    # -2000 W am Netz bedeutet 2000 W Einspeisung.
    assert coordinator.data.surplus.available == 2000
    assert coordinator.data.surplus.grid_w == -2000


async def test_ampel_verteilt_das_budget(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """2000 W reichen für den Heizstab, nicht für die Wallbox."""
    await setup_integration(hass, entry_mit_verbrauchern)

    coordinator = entry_mit_verbrauchern.runtime_data
    nach_namen = {v.config.name: v for v in coordinator.data.consumers}

    assert nach_namen["Heizstab"].status.value == "off_ready"
    assert nach_namen["Wallbox"].status.value == "off_insufficient"


async def test_prioritaet_bestimmt_die_reihenfolge(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Wer eine niedrigere Zahl hat, wird zuerst bedient."""
    await setup_integration(hass, entry_mit_verbrauchern)
    coordinator = entry_mit_verbrauchern.runtime_data

    ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}
    await coordinator.async_set_priority(ids["Wallbox"], 1)
    await coordinator.async_set_priority(ids["Heizstab"], 2)
    await hass.async_block_till_done()

    assert coordinator.data.consumers[0].config.name == "Wallbox"

    # Umgekehrt kehrt sich die Reihenfolge um.
    await coordinator.async_set_priority(ids["Heizstab"], 1)
    await coordinator.async_set_priority(ids["Wallbox"], 2)
    await hass.async_block_till_done()

    assert coordinator.data.consumers[0].config.name == "Heizstab"


async def test_nicht_verwalteter_verbraucher_blockiert_kein_budget(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Die bewusste Abweichung von der Kartenlogik.

    Die Karte reserviert Budget auch für nicht verwaltete Verbraucher — richtig
    für die Anzeige. Für die Schaltentscheidung darf ein manuell verwaltetes
    Gerät kein Budget blockieren.
    """
    await setup_integration(hass, entry_mit_verbrauchern)
    coordinator = entry_mit_verbrauchern.runtime_data
    ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}

    # Beide auf 1500 W, Überschuss 2000 W: nur einer passt.
    hass.states.async_set("sensor.netz", "-2000", {"unit_of_measurement": "W"})
    await coordinator.async_set_priority(ids["Heizstab"], 1)
    await coordinator.async_set_priority(ids["Wallbox"], 2)

    # Heizstab aus der Automatik nehmen: sein Bedarf darf das Budget nicht mehr
    # binden, die Wallbox rückt nach.
    await coordinator.async_set_managed(ids["Heizstab"], False)
    await hass.async_block_till_done()

    nach_namen = {v.config.name: v for v in coordinator.data.consumers}
    assert nach_namen["Heizstab"].managed is False
    # Status bleibt derselbe wie in der Karte …
    assert nach_namen["Heizstab"].status.value == "off_ready"
    # … aber das Budget wurde nicht abgezogen.
    assert nach_namen["Heizstab"].headroom_w == 2000


async def test_hauptschalter_ist_anfangs_aus(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Nach dem Einrichten soll erst beobachtet werden."""
    await setup_integration(hass, entry_mit_verbrauchern)
    assert entry_mit_verbrauchern.runtime_data.automation_enabled is False


async def test_laufender_verbraucher_verbraucht_kein_budget(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Sein Verbrauch steckt schon im gemessenen Überschuss."""
    await setup_integration(hass, entry_mit_verbrauchern)

    hass.states.async_set("switch.heizstab", STATE_ON)
    await hass.async_block_till_done()

    coordinator = entry_mit_verbrauchern.runtime_data
    await coordinator.async_request_refresh_now()

    nach_namen = {v.config.name: v for v in coordinator.data.consumers}
    assert nach_namen["Heizstab"].status.value == "on_ok"
    # Volles Budget steht noch für die anderen zur Verfügung.
    assert nach_namen["Heizstab"].headroom_w == 2000


async def test_unbrauchbarer_sensor_fuehrt_in_den_sicheren_zustand(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Ein kWh-Zähler ist ein Konfigurationsfehler, kein Messwert von 0."""
    await setup_integration(hass, entry_mit_verbrauchern)

    # Erst nach dem Aufbau umstellen — setup_integration setzt den Sensor selbst.
    hass.states.async_set("sensor.netz", "4211", {"unit_of_measurement": "kWh"})
    coordinator = entry_mit_verbrauchern.runtime_data
    await coordinator.async_request_refresh_now()

    assert coordinator.data.surplus.available is None
    assert coordinator.data.surplus.usable is False
    assert all(v.status.value == "unavailable" for v in coordinator.data.consumers)


async def test_ausgefallener_sensor_fuehrt_in_den_sicheren_zustand(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Auch ein unavailable-Sensor darf nicht als 0 W durchgehen."""
    await setup_integration(hass, entry_mit_verbrauchern)

    hass.states.async_set("sensor.netz", "unavailable", {"unit_of_measurement": "W"})
    coordinator = entry_mit_verbrauchern.runtime_data
    await coordinator.async_request_refresh_now()

    assert coordinator.data.surplus.available is None
    assert coordinator.data.surplus.usable is False


async def test_unload_hinterlaesst_keine_listener(
    hass: HomeAssistant, entry_mit_verbrauchern: MockConfigEntry
) -> None:
    """Nach Entladen darf keine Zustandsänderung mehr auslösen."""
    await setup_integration(hass, entry_mit_verbrauchern)
    coordinator = entry_mit_verbrauchern.runtime_data

    assert await hass.config_entries.async_unload(entry_mit_verbrauchern.entry_id)
    await hass.async_block_till_done()

    vorher = coordinator.data
    hass.states.async_set("sensor.netz", "-9999", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()

    # Unveränderte Daten: der Listener wurde abgemeldet.
    assert coordinator.data is vorher
