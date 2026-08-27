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
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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


class TestEntitaetenWandern:
    """Die Stellgrößen ziehen ans Speichergerät — mit ihrer Entitäts-ID.

    Das ist die eine Stelle des Umbaus, an der ein Fehler dem Nutzer wirklich
    Arbeit macht: Die Entitäts-ID steht in Dashboards, Automatisierungen und
    Verlaufsdaten. Greift das Umschreiben nicht, entsteht daneben eine zweite
    Entität mit ``_2``, und alles Verknüpfte zeigt ins Leere.
    """

    def _alte_entitaet(self, hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
        """Legt eine Entität an, wie eine ältere Fassung sie hinterlassen hat."""
        registry = er.async_get(hass)
        eintrag = registry.async_get_or_create(
            "number",
            DOMAIN,
            f"{entry.entry_id}_{key}",
            config_entry=entry,
            suggested_object_id=f"energy_manager_{key}",
        )
        return eintrag.entity_id

    async def test_die_entitaets_id_bleibt(self, hass: HomeAssistant) -> None:
        entry = alteintrag(
            **{
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
                CONF_BATTERY_MAX_CHARGE_W: 5000,
            }
        )
        entry.add_to_hass(hass)
        vorher = {
            key: self._alte_entitaet(hass, entry, key)
            for key in ("battery_priority", "battery_min_soc")
        }

        await setup(hass, entry)

        registry = er.async_get(hass)
        subentry_id = speicher(entry)[0].subentry_id

        for alt, neu in (("battery_priority", "priority"), ("battery_min_soc", "min_soc")):
            eintrag = registry.async_get(vorher[alt])
            assert eintrag is not None, f"{vorher[alt]} ist verschwunden"
            assert eintrag.unique_id == f"{subentry_id}_{neu}"
            assert eintrag.config_subentry_id == subentry_id

        # Und keine zweite daneben: Genau darin bestünde der Schaden.
        assert len(registry.entities) == len(
            [e for e in registry.entities.values() if not e.entity_id.endswith("_2")]
        )

    async def test_ohne_altbestand_geschieht_nichts(self, hass: HomeAssistant) -> None:
        """Eine frische Einrichtung hat nichts umzuhängen."""
        entry = alteintrag(**{CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc"})
        await setup(hass, entry)

        registry = er.async_get(hass)
        assert (
            registry.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_battery_min_soc")
            is None
        )

    async def test_eine_belegte_kennung_bricht_nichts_ab(self, hass: HomeAssistant) -> None:
        """Der Fall des Nutzers, der die Fassung ohne diesen Schritt schon lief.

        Er hat bereits eine Entität mit der neuen Kennung. Das Umschreiben würde
        hart werfen und die Einrichtung abbrechen — damit ist ihm nicht
        geholfen. Der alte Eintrag bleibt dann eben liegen.
        """
        entry = alteintrag(
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_BATTERY,
                title="Keller",
                unique_id=None,
                data={CONF_NAME: "Keller", CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc"},
            )
        )
        entry.add_to_hass(hass)
        subentry_id = speicher(entry)[0].subentry_id

        registry = er.async_get(hass)
        registry.async_get_or_create("number", DOMAIN, f"{subentry_id}_min_soc", config_entry=entry)
        alt = self._alte_entitaet(hass, entry, "battery_min_soc")

        await setup(hass, entry)

        # Kein Abbruch, und der alte Eintrag ist unverändert.
        assert entry.state is ConfigEntryState.LOADED
        assert registry.async_get(alt).unique_id == f"{entry.entry_id}_battery_min_soc"


class TestZweiSpeicher:
    """Mehrere Speicher in der Bilanz — der Zweck des ganzen Umbaus."""

    def _speicher(self, name: str, sensor: str) -> ConfigSubentryData:
        return ConfigSubentryData(
            subentry_type=SUBENTRY_TYPE_BATTERY,
            title=name,
            unique_id=None,
            data={CONF_NAME: name, CONF_BATTERY_POWER_ENTITY: sensor},
        )

    async def test_beide_zaehlen_in_die_bilanz(self, hass: HomeAssistant) -> None:
        entry = alteintrag(
            self._speicher("Keller", "sensor.keller_leistung"),
            self._speicher("Garage", "sensor.garage_leistung"),
        )
        hass.states.async_set("sensor.keller_leistung", "1500", {"unit_of_measurement": "W"})
        hass.states.async_set("sensor.garage_leistung", "800", {"unit_of_measurement": "W"})
        await setup(hass, entry)

        # 2000 W Einspeisung plus 2300 W, die gerade in die Speicher fließen.
        assert entry.runtime_data.data.surplus.raw == pytest.approx(4300)

    async def test_jeder_bekommt_sein_eigenes_geraet(self, hass: HomeAssistant) -> None:
        entry = alteintrag(
            self._speicher("Keller", "sensor.keller_leistung"),
            self._speicher("Garage", "sensor.garage_leistung"),
        )
        await setup(hass, entry)

        namen = {
            state.attributes["consumer_name"]
            for state in hass.states.async_all("sensor")
            if state.attributes.get("kind") == "battery"
        }
        assert namen == {"Keller", "Garage"}

    async def test_die_stellgroessen_sind_getrennt(self, hass: HomeAssistant) -> None:
        """Zwei Speicher teilen sich keine Ladevorranggrenze."""
        entry = alteintrag(
            self._speicher("Keller", "sensor.keller_leistung"),
            self._speicher("Garage", "sensor.garage_leistung"),
        )
        await setup(hass, entry)

        keller, garage = (s.subentry_id for s in speicher(entry))
        coordinator = entry.runtime_data
        await coordinator.async_set_battery_min_soc(40, keller)
        await hass.async_block_till_done()

        assert coordinator.battery_runtime_for(keller).min_soc == 40
        assert coordinator.battery_runtime_for(garage).min_soc == 0
