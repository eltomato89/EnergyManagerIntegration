"""Die Batterie als verschiebbare Last.

Höher priorisierte Verbraucher werden vor der Batterie versorgt und bleiben
eingeschaltet; die Batterie reserviert an ihrem Rang ihre Ladeleistung; nur der
Rest steht tiefer priorisierten Verbrauchern zur Verfügung. Geschaltet wird die
Batterie dabei nicht — sie belegt nur Budget.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)


def consumer(name: str, watt: int) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={
            CONF_NAME: name,
            CONF_SWITCH_ENTITY: f"switch.{name.lower()}",
            CONF_POWER_ENTITY: f"sensor.{name.lower()}_leistung",
            CONF_MAX_POWER: watt,
        },
    )


async def setup_battery(
    hass: HomeAssistant,
    *,
    netz: int,
    soc: int,
    max_charge: int = 2000,
    battery_prio: int = 2,
) -> MockConfigEntry:
    """Zwei Verbraucher (Wichtig=1, Egal=3) mit der Batterie dazwischen.

    ``netz`` ist die Netzleistung (negativ = Einspeisung); der verfügbare
    Überschuss ergibt sich daraus wie in einer echten Anlage.
    """
    data = {
        CONF_METER_MODE: METER_MODE_GRID,
        CONF_GRID_ENTITY: "sensor.netz",
        CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
        CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
    }
    if max_charge:
        data[CONF_BATTERY_MAX_CHARGE_W] = max_charge

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 0},
        unique_id=DOMAIN,
        subentries_data=[consumer("Wichtig", 1000), consumer("Egal", 1000)],
    )

    hass.states.async_set("sensor.netz", str(netz), {"unit_of_measurement": "W"})
    # Batterie gerade nicht ladend: der Überschuss kommt allein aus dem Netz.
    hass.states.async_set("sensor.akku_leistung", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.akku_soc", str(soc), {"unit_of_measurement": "%"})
    for name in ("wichtig", "egal"):
        hass.states.async_set(f"switch.{name}", STATE_OFF)
        hass.states.async_set(f"sensor.{name}_leistung", "0", {"unit_of_measurement": "W"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    ids = {v.config.name: v.config.subentry_id for v in coordinator.data.consumers}
    await coordinator.async_set_priority(ids["Wichtig"], 1)
    await coordinator.async_set_priority(ids["Egal"], 3)
    if max_charge:
        await coordinator.async_set_battery_priority(battery_prio)
    await hass.async_block_till_done()
    return entry


class TestBudget:
    async def test_batterie_sperrt_tiefere_verbraucher_aus(self, hass: HomeAssistant) -> None:
        """2500 W: Wichtig (1000 W) bleibt bereit, die Batterie nimmt den Rest.

        Für Egal bleibt dann nichts — obwohl 1500 W frei wären, gehören sie der
        höher priorisierten Batterie.
        """
        entry = await setup_battery(hass, netz=-2500, soc=60)
        coordinator = entry.runtime_data
        nach_namen = {v.config.name: v for v in coordinator.data.consumers}

        assert nach_namen["Wichtig"].status.value == "off_ready"
        assert nach_namen["Egal"].status.value == "off_insufficient"

    async def test_ohne_batterie_kaeme_egal_zum_zug(self, hass: HomeAssistant) -> None:
        """Gegenprobe: ohne Ladeleistung ist die Batterie nur ein Korrekturterm."""
        entry = await setup_battery(hass, netz=-2500, soc=60, max_charge=0)
        coordinator = entry.runtime_data
        nach_namen = {v.config.name: v for v in coordinator.data.consumers}

        assert nach_namen["Egal"].status.value == "off_ready"
        assert coordinator.data.battery is None

    async def test_batterie_view_und_raenge(self, hass: HomeAssistant) -> None:
        """Die Batterie belegt Rang 2; die Ränge der Verbraucher lassen Platz."""
        entry = await setup_battery(hass, netz=-2500, soc=60)
        coordinator = entry.runtime_data
        nach_namen = {v.config.name: v for v in coordinator.data.consumers}

        battery = coordinator.data.battery
        assert battery is not None
        assert battery.rank == 1  # 0-basiert: Wichtig 0, Batterie 1, Egal 2
        assert battery.claim_w == 1500  # nur was nach Wichtig übrig war
        assert battery.status.value == "on_deficit"  # weniger als die volle Ladeleistung

        assert nach_namen["Wichtig"].rank == 0
        assert nach_namen["Egal"].rank == 2

    async def test_volle_batterie_gibt_das_budget_frei(self, hass: HomeAssistant) -> None:
        """Am Ladeende reserviert die Batterie nichts mehr."""
        entry = await setup_battery(hass, netz=-2500, soc=100)
        coordinator = entry.runtime_data
        nach_namen = {v.config.name: v for v in coordinator.data.consumers}

        assert coordinator.data.battery is not None
        assert coordinator.data.battery.full is True
        assert coordinator.data.battery.claim_w == 0
        # Egal bekommt die vollen 1500 W, die die Batterie nicht mehr braucht.
        assert nach_namen["Egal"].status.value == "off_ready"


class TestEntitaeten:
    async def test_prioritaets_entitaet_der_batterie(self, hass: HomeAssistant) -> None:
        await setup_battery(hass, netz=-2500, soc=60, battery_prio=2)

        state = hass.states.get("number.energy_manager_battery_priority")
        assert state is not None
        assert float(state.state) == 2

    async def test_keine_batterie_entitaet_ohne_ladeleistung(self, hass: HomeAssistant) -> None:
        await setup_battery(hass, netz=-2500, soc=60, max_charge=0)
        assert hass.states.get("number.energy_manager_battery_priority") is None

    async def test_hub_sensor_traegt_die_batterie_attribute(self, hass: HomeAssistant) -> None:
        """Die Karte baut ihre Batteriezeile allein aus diesen Attributen."""
        await setup_battery(hass, netz=-2500, soc=60)

        hub = hass.states.get("sensor.energy_manager_surplus")
        assert hub is not None
        attrs = hub.attributes
        assert attrs["battery_load"] is True
        assert attrs["battery_rank"] == 2
        assert attrs["battery_max_charge_w"] == 2000
        assert attrs["battery_claim_w"] == 1500
        assert attrs["battery_status"] == "on_deficit"
        assert attrs["battery_soc_entity"] == "sensor.akku_soc"

    async def test_batterie_rang_ueberlebt_neuladen(self, hass: HomeAssistant) -> None:
        """Der Rang liegt im Speicher, nicht nur in der Entität."""
        entry = await setup_battery(hass, netz=-2500, soc=60, battery_prio=3)
        coordinator = entry.runtime_data
        assert coordinator.battery_priority == 3
