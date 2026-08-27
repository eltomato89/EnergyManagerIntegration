"""Die Wanderung bestehender Konfigurationen.

Bis 0.5.0b3 stand die Batterie als Feldergruppe in den Eintragsdaten. Als
Subentry kann es mehrere geben, und jeder trägt seine Stellgrößen an seinem
eigenen Gerät.

Die Wanderung ist die eine Stelle, an der ein Fehler dem Nutzer wirklich Arbeit
macht: Was hier verloren geht, hat er von Hand eingetragen und muss es von Hand
wiederfinden. Deshalb prüfen diese Tests nicht nur, dass etwas ankommt, sondern
auch, dass nichts zurückbleibt und dass ein zweiter Start nichts wiederholt.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    BATTERY_MODE_FULL,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_MODE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_GRID_ENTITY,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_SMOOTHING_WINDOW,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_BATTERY,
)
from custom_components.energy_manager.migration import BATTERIE_FELDER


def alteintrag(*subentries: ConfigSubentryData, **batterie) -> MockConfigEntry:
    """Ein Eintrag, wie ihn eine ältere Fassung hinterlassen hat."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={
            CONF_METER_MODE: METER_MODE_GRID,
            CONF_GRID_ENTITY: "sensor.netz",
            **batterie,
        },
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
        subentries_data=list(subentries),
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.states.async_set("sensor.netz", "-2000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.akku_leistung", "500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.akku_soc", "62", {"unit_of_measurement": "%"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def speicher(entry: MockConfigEntry) -> list:
    return [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_BATTERY]


class TestWanderung:
    async def test_aus_den_feldern_wird_ein_speicher(self, hass: HomeAssistant) -> None:
        entry = alteintrag(
            **{
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
                CONF_BATTERY_INVERT: True,
                CONF_BATTERY_MODE: BATTERY_MODE_FULL,
                CONF_BATTERY_RESERVE_W: 300,
                CONF_BATTERY_MAX_CHARGE_W: 5000,
            }
        )
        await setup(hass, entry)

        assert len(speicher(entry)) == 1
        daten = speicher(entry)[0].data

        # Jedes Feld einzeln: Ein vergessenes fiele sonst erst dem Nutzer auf.
        assert daten[CONF_BATTERY_SOC_ENTITY] == "sensor.akku_soc"
        assert daten[CONF_BATTERY_POWER_ENTITY] == "sensor.akku_leistung"
        assert daten[CONF_BATTERY_INVERT] is True
        assert daten[CONF_BATTERY_MODE] == BATTERY_MODE_FULL
        assert daten[CONF_BATTERY_RESERVE_W] == 300
        assert daten[CONF_BATTERY_MAX_CHARGE_W] == 5000
        assert daten[CONF_NAME]

    async def test_die_felder_verlassen_den_haupteintrag(self, hass: HomeAssistant) -> None:
        """Bleiben sie liegen, legt der nächste Start einen weiteren Speicher an."""
        entry = alteintrag(**{CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung"})
        await setup(hass, entry)

        assert not [feld for feld in BATTERIE_FELDER if feld in entry.data]
        # Was nicht zur Batterie gehört, bleibt unangetastet.
        assert entry.data[CONF_GRID_ENTITY] == "sensor.netz"

    async def test_ein_zweiter_start_wiederholt_nichts(self, hass: HomeAssistant) -> None:
        entry = alteintrag(**{CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung"})
        await setup(hass, entry)

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert len(speicher(entry)) == 1

    async def test_der_speicher_wird_auch_benutzt(self, hass: HomeAssistant) -> None:
        """Nicht nur angelegt: Die Bilanz muss ihn danach auch lesen."""
        entry = alteintrag(
            **{
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
            }
        )
        await setup(hass, entry)

        assert entry.runtime_data.battery_soc() == pytest.approx(62)
        assert entry.runtime_data.data.surplus.battery_w == pytest.approx(500)

    async def test_der_mindestladestand_ueberlebt(self, hass: HomeAssistant) -> None:
        """Er wandert nicht mit — er ist eine Stellgröße, keine Beschreibung.

        Übernommen wird er weiterhin aus den Eintragsdaten; die Wanderung darf
        ihn dabei nicht wegräumen.
        """
        entry = alteintrag(
            **{
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_MIN_SOC: 40,
            }
        )
        await setup(hass, entry)

        assert entry.runtime_data.battery_min_soc == pytest.approx(40)


class TestOhneBatterie:
    async def test_ohne_felder_entsteht_kein_speicher(self, hass: HomeAssistant) -> None:
        entry = alteintrag()
        await setup(hass, entry)

        assert speicher(entry) == []

    async def test_reste_ohne_messwert_werden_nur_weggeraeumt(self, hass: HomeAssistant) -> None:
        """Eine Ladeleistung ohne jeden Sensor beschreibt keinen Speicher.

        Daraus einen anzulegen hieße, eine Zeile in die Karte zu setzen, hinter
        der nichts steht. Die Reste verschwinden trotzdem — sonst stünden sie
        für immer in den Daten, und niemand käme je an sie heran.
        """
        entry = alteintrag(**{CONF_BATTERY_MAX_CHARGE_W: 5000})
        await setup(hass, entry)

        assert speicher(entry) == []
        assert CONF_BATTERY_MAX_CHARGE_W not in entry.data


class TestBestehenderSubentry:
    async def test_wird_nicht_ueberschrieben(self, hass: HomeAssistant) -> None:
        """Wer schon einen Speicher hat, ist längst gewandert."""
        entry = alteintrag(
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BATTERY,
                title="Keller",
                unique_id=None,
                data={CONF_NAME: "Keller", CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung"},
            ),
            **{CONF_BATTERY_POWER_ENTITY: "sensor.alt"},
        )
        await setup(hass, entry)

        assert [s.title for s in speicher(entry)] == ["Keller"]
        assert speicher(entry)[0].data[CONF_BATTERY_POWER_ENTITY] == "sensor.akku_leistung"
        # Die toten Felder verschwinden trotzdem: Sie führen sonst beim
        # nächsten Blick in die Konfiguration nur in die Irre.
        assert CONF_BATTERY_POWER_ENTITY not in entry.data
