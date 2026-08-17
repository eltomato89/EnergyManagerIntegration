"""Drosseln statt Verdrängen.

Bisher kannte die Verdrängung nur ein Mittel: Der unwichtigere geht aus. Bei
einem regelbaren Verbraucher ist das unnötig grob — er kann die Differenz bis zu
seiner kleinsten Stufe hergeben und dabei weiterlaufen.

Die Reihenfolge ist damit zweistufig, und beide Stufen sind hier zu prüfen: Erst
gibt jeder das Gelindeste her, was er anzubieten hat; reicht das zusammengenommen
nicht, wird aus Drosseln Abschalten. Ohne diesen zweiten Durchgang ginge Können
verloren — ein verdrängter regelbarer Verbraucher gab bisher seine **ganze**
Leistung her.
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
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_GRID_ENTITY,
    CONF_LEVEL_HOLD,
    CONF_LEVEL_MAP,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONSUMER_TYPE_MODULATING,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)

SETTLE = 60
# Kleinste Stufe 1000, größte 4000 — der Heizstab kann also 3000 W hergeben,
# ohne auszugehen.
STUFEN = {"aus": 0, "s1": 1000, "s2": 2000, "s3": 3000, "s4": 4000}
OPTIONEN = ["aus", "s1", "s2", "s3", "s4"]


def waermepumpe(bedarf: int) -> ConfigSubentryData:
    """Rang 1, ausgeschaltet, braucht Platz."""
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title="Waermepumpe",
        unique_id=None,
        data={
            CONF_NAME: "Waermepumpe",
            CONF_SWITCH_ENTITY: "switch.waermepumpe",
            CONF_MAX_POWER: bedarf,
        },
    )


def heizstab(**extra) -> ConfigSubentryData:
    """Rang 2, regelbar, läuft."""
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title="Heizstab",
        unique_id=None,
        data={
            CONF_NAME: "Heizstab",
            CONF_SWITCH_ENTITY: "switch.heizstab",
            CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
            CONF_CONTROL_ENTITY: "select.heizstab",
            CONF_LEVEL_MAP: STUFEN,
            CONF_POWER_ENTITY: "sensor.heizstab_leistung",
            **extra,
        },
    )


def make_entry(*consumers: ConfigSubentryData) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: SETTLE},
        unique_id=DOMAIN,
        subentries_data=list(consumers),
    )


@pytest.fixture
def dienste(hass: HomeAssistant) -> dict[str, list]:
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
        "level": async_mock_service(hass, "select", "select_option"),
    }


async def setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    grid: str = "0",
    option: str = "s4",
    leistung: str = "4000",
    extra_switches: dict[str, str] | None = None,
) -> None:
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": "W"})
    hass.states.async_set("select.heizstab", option, {"options": OPTIONEN})
    hass.states.async_set("sensor.heizstab_leistung", leistung, {"unit_of_measurement": "W"})
    hass.states.async_set("switch.heizstab", STATE_ON)
    hass.states.async_set("switch.waermepumpe", STATE_OFF)
    for entity_id, state in (extra_switches or {}).items():
        hass.states.async_set(entity_id, state)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def set_ranks(hass: HomeAssistant, entry: MockConfigEntry, *names: str) -> None:
    """Legt die Rangfolge ausdrücklich fest.

    Nötig, weil bei gleicher Priorität der **Name** entscheidet: „Heizstab"
    stünde alphabetisch vor „Waermepumpe", und dann hätte der Verdränger
    niemanden unter sich. Genau die Annahme, die diese Datei prüfen will, wäre
    damit stillschweigend ausgehebelt.
    """
    for rang, name in enumerate(names, start=1):
        await entry.runtime_data.async_set_priority(id_of(entry, name), float(rang))
    await hass.async_block_till_done()


async def evaluate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()


async def arm(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_set_automation(True)
    await hass.async_block_till_done()


def view(entry: MockConfigEntry, name: str):
    return next(v for v in entry.runtime_data.data.consumers if v.config.name == name)


def id_of(entry: MockConfigEntry, name: str) -> str:
    return next(key for key, value in entry.runtime_data.consumers.items() if value.name == name)


class TestDrosselnGehtVor:
    async def test_der_heizstab_geht_herunter_statt_aus(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Die Wärmepumpe braucht 2000 W, der Heizstab läuft auf 4000 W.

        Er kann 3000 W hergeben, ohne auszugehen — das reicht. Also wird
        gedrosselt und nicht abgeschaltet.
        """
        entry = make_entry(waermepumpe(2000), heizstab())
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == (id_of(entry, "Heizstab"),)
        assert pumpe.displaceable == ()

    async def test_die_handlung_wird_ausgefuehrt(self, hass: HomeAssistant, dienste: dict) -> None:
        """Erst herunterstellen, dann einschalten — in einem Durchlauf."""
        entry = make_entry(waermepumpe(2000), heizstab())
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await arm(hass, entry)

        assert [call.data["option"] for call in dienste["level"]] == ["s1"]
        assert [call.data["entity_id"] for call in dienste["on"]] == ["switch.waermepumpe"]
        # Der Heizstab bleibt an.
        assert not dienste["off"]

    async def test_eine_laufende_mindestlaufzeit_steht_nicht_im_weg(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Sie schützt vor dem Abschalten. Gedrosselt läuft das Gerät weiter.

        Ein angefangener Waschgang wird davon nicht abgebrochen — und wäre der
        Heizstab dadurch unantastbar, bliebe die Wärmepumpe aus, obwohl 3000 W
        zu holen sind.
        """
        entry = make_entry(waermepumpe(2000), heizstab(**{CONF_MIN_RUNTIME: 3600}))
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        laufzeit = entry.runtime_data.runtime_for(id_of(entry, "Heizstab"))
        laufzeit.last_switch_ts = dt_util.utcnow().timestamp()
        laufzeit.last_switch_to = True
        await evaluate(hass, entry)

        assert view(entry, "Waermepumpe").throttleable == (id_of(entry, "Heizstab"),)

    async def test_die_haltezeit_steht_im_weg(self, hass: HomeAssistant, dienste: dict) -> None:
        """Sie ist der Schutz davor, die Leiter im Takt zu bewegen.

        Eine Verdrängung ist kein Grund, ihn zu übergehen. Abschalten bleibt
        möglich — und weil 4000 W dafür reichen, geschieht genau das.
        """
        entry = make_entry(waermepumpe(2000), heizstab(**{CONF_LEVEL_HOLD: 600}))
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        entry.runtime_data.runtime_for(
            id_of(entry, "Heizstab")
        ).last_level_ts = dt_util.utcnow().timestamp()
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == ()
        assert pumpe.displaceable == (id_of(entry, "Heizstab"),)


class TestAbschaltenWennDrosselnNichtReicht:
    async def test_der_zweite_durchgang(self, hass: HomeAssistant, dienste: dict) -> None:
        """Die Wärmepumpe braucht 3500 W, drosseln gibt nur 3000 W her.

        Ohne den zweiten Durchgang bliebe sie aus, obwohl der Heizstab
        abgeschaltet 4000 W hergäbe — ein Fall, der vor der Drosselung
        funktioniert hat.
        """
        entry = make_entry(waermepumpe(3500), heizstab())
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == ()
        assert pumpe.displaceable == (id_of(entry, "Heizstab"),)

    async def test_reicht_auch_abschalten_nicht_geschieht_nichts(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Sonst hätte man abgeschaltet und trotzdem nichts gewonnen."""
        entry = make_entry(waermepumpe(9000), heizstab())
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == ()
        assert pumpe.displaceable == ()


class TestGemischt:
    async def test_drosseln_und_abschalten_zusammen(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Der Heizstab gibt 3000 W durch Drosseln, die Pumpe 500 W durch Ausgehen.

        Zusammen 3500 W. Der Heizstab muss dafür nicht ausgehen — das gelindeste
        Mittel je Kandidat, und nur so viele Kandidaten wie nötig.
        """
        entry = make_entry(
            waermepumpe(3400),
            heizstab(),
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Pumpe",
                unique_id=None,
                data={
                    CONF_NAME: "Pumpe",
                    CONF_SWITCH_ENTITY: "switch.pumpe",
                    CONF_MAX_POWER: 500,
                },
            ),
        )
        await setup(hass, entry, extra_switches={"switch.pumpe": STATE_ON})
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab", "Pumpe")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        # Von hinten gesammelt: erst die Pumpe (500 W), dann der Heizstab (3000 W).
        assert pumpe.displaceable == (id_of(entry, "Pumpe"),)
        assert pumpe.throttleable == (id_of(entry, "Heizstab"),)

    async def test_nur_der_unwichtigste_wird_angefasst(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """500 W fehlen. Die Pumpe allein reicht, der Heizstab bleibt in Ruhe."""
        entry = make_entry(
            waermepumpe(500),
            heizstab(),
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Pumpe",
                unique_id=None,
                data={
                    CONF_NAME: "Pumpe",
                    CONF_SWITCH_ENTITY: "switch.pumpe",
                    CONF_MAX_POWER: 500,
                },
            ),
        )
        await setup(hass, entry, extra_switches={"switch.pumpe": STATE_ON})
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab", "Pumpe")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.displaceable == (id_of(entry, "Pumpe"),)
        assert pumpe.throttleable == ()


class TestNichtsZuHolen:
    async def test_ein_heizstab_auf_der_kleinsten_stufe_gibt_nichts_her(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Drosseln geht nicht mehr; abschalten gäbe 1000 W, das reicht nicht."""
        entry = make_entry(waermepumpe(2000), heizstab())
        await setup(hass, entry, option="s1", leistung="1000")
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await evaluate(hass, entry)

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == ()
        assert pumpe.displaceable == ()

    async def test_ein_schaltbarer_verbraucher_wird_nicht_gedrosselt(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        entry = make_entry(
            waermepumpe(500),
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Pumpe",
                unique_id=None,
                data={
                    CONF_NAME: "Pumpe",
                    CONF_SWITCH_ENTITY: "switch.pumpe",
                    CONF_MAX_POWER: 800,
                },
            ),
        )
        hass.states.async_set("sensor.netz", "0", {"unit_of_measurement": "W"})
        hass.states.async_set("switch.waermepumpe", STATE_OFF)
        hass.states.async_set("switch.pumpe", STATE_ON)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await set_ranks(hass, entry, "Waermepumpe", "Pumpe")

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.throttleable == ()
        assert pumpe.displaceable == (id_of(entry, "Pumpe"),)


class TestAttribute:
    async def test_getrennt_gezaehlt(self, hass: HomeAssistant, dienste: dict) -> None:
        """Es ist ein Unterschied, ob ein Gerät ausgeht oder nur heruntergeht."""
        entry = make_entry(waermepumpe(2000), heizstab())
        await setup(hass, entry)
        await set_ranks(hass, entry, "Waermepumpe", "Heizstab")
        await evaluate(hass, entry)

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Waermepumpe"
        )

        assert status.attributes["throttles"] == 1
        assert status.attributes["displaces"] == 0
