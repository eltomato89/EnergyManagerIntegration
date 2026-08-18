"""Tages-Mindestlaufzeit: der eine Fall, in dem Netzstrom in Kauf genommen wird.

Die Regel ist ein Vergleich, kein Regelkreis: Reicht die verbleibende Prognose
nicht mehr für die noch fehlende Energie, läuft der Verbraucher auch ohne
Überschuss. Vorher nicht — solange es sich mit Sonne ausgeht, gibt es keinen
Grund dafür.

Zwei Dinge sind hier wichtiger als der Rest. Erstens: **Ohne Prognosesensor
greift die Regel nie.** Ein fehlender Wert ist ein Konfigurationsfehler und kein
Anlass, ein Gerät auf Netzstrom laufen zu lassen. Zweitens: Der Tag beginnt mit
dem **Sonnenaufgang**, nicht um Mitternacht — bei einer Anlage, deren Ertrag an
der Sonne hängt, ist das der Schnitt, der die Sache trifft.
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
    CONF_DAILY_TARGET,
    CONF_ENERGY_ENTITY,
    CONF_FORECAST_ENTITY,
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONSUMER_TYPE_MODULATING,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)
from custom_components.energy_manager.daily import DailyProgress, solar_day_start

PUMPE = "switch.poolpumpe"
ZAEHLER = "sensor.poolpumpe_energie"
PROGNOSE = "sensor.prognose_rest"


class TestDailyProgress:
    """Die reine Rechnung."""

    def _p(self, ziel: float, geschafft: float, prognose: float | None) -> DailyProgress:
        return DailyProgress(target_kwh=ziel, done_kwh=geschafft, forecast_kwh=prognose)

    def test_fehlende_energie(self) -> None:
        assert self._p(3.0, 1.2, None).missing_kwh == pytest.approx(1.8)

    def test_ziel_erreicht(self) -> None:
        erreicht = self._p(3.0, 3.0, 0.0)
        assert erreicht.reached
        assert erreicht.missing_kwh == 0
        assert erreicht.must_run is False

    def test_ueber_dem_ziel_ist_kein_negatives_fehlen(self) -> None:
        assert self._p(3.0, 4.5, 0.0).missing_kwh == 0

    def test_prognose_reicht(self) -> None:
        # 1,8 kWh fehlen, 5 kWh erwartet: kein Grund für Netzstrom.
        assert self._p(3.0, 1.2, 5.0).must_run is False

    def test_prognose_reicht_nicht(self) -> None:
        assert self._p(3.0, 1.2, 0.5).must_run is True

    def test_knapp_reicht_nicht(self) -> None:
        """Eine Prognose ist keine Zusage, deshalb etwas Luft.

        1,8 kWh fehlen, 1,9 kWh erwartet: rechnerisch geht es auf, aber bei
        exakter Gleichheit anzunehmen, es reiche, verschöbe die Nachladung bis
        zur letzten Minute.
        """
        assert self._p(3.0, 1.2, 1.9).must_run is True
        assert self._p(3.0, 1.2, 2.1).must_run is False

    def test_ohne_prognose_nie(self) -> None:
        """Ein fehlender Wert ist ein Konfigurationsfehler, keine Entscheidung."""
        assert self._p(3.0, 0.0, None).must_run is False


class TestSolartag:
    async def test_beginnt_mit_dem_sonnenaufgang(self, hass: HomeAssistant) -> None:
        """Nicht um Mitternacht: Ein Zähler, der dort zurückgesetzt wird, liegt
        Stunden vor dem ersten Ertrag und zerschneidet keinen Solartag."""
        jetzt = dt_util.utcnow().timestamp()
        beginn = solar_day_start(hass, jetzt)

        assert beginn <= jetzt
        # Innerhalb der letzten 24 Stunden — ein Sonnenaufgang je Tag.
        assert jetzt - beginn < 24 * 3600

    async def test_ist_ueber_den_tag_stabil(self, hass: HomeAssistant) -> None:
        """Sonst würde der Tageszähler mitten am Tag zurückgesetzt."""
        jetzt = dt_util.utcnow().timestamp()

        assert solar_day_start(hass, jetzt) == solar_day_start(hass, jetzt + 600)


# --- Durch die ganze Kette --------------------------------------------------


def poolpumpe(**extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title="Poolpumpe",
        unique_id=None,
        data={
            CONF_NAME: "Poolpumpe",
            CONF_SWITCH_ENTITY: PUMPE,
            CONF_MAX_POWER: 500,
            CONF_ENERGY_ENTITY: ZAEHLER,
            **extra,
        },
    )


def make_entry(*consumers: ConfigSubentryData, prognose: bool = True) -> MockConfigEntry:
    options: dict = {CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60}
    if prognose:
        options[CONF_FORECAST_ENTITY] = PROGNOSE
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options=options,
        unique_id=DOMAIN,
        subentries_data=list(consumers),
    )


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    grid: str = "1000",
    zaehler: str = "0",
    prognose: str = "10",
    state: str = STATE_OFF,
) -> None:
    """Kein Überschuss (1000 W Netzbezug), damit nur das Tagesziel schalten kann."""
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": "W"})
    hass.states.async_set(ZAEHLER, zaehler, {"unit_of_measurement": "kWh"})
    hass.states.async_set(PROGNOSE, prognose, {"unit_of_measurement": "kWh"})
    hass.states.async_set(PUMPE, state)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def arm(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_set_automation(True)
    await hass.async_block_till_done()


def view(entry: MockConfigEntry, name: str = "Poolpumpe"):
    return next(v for v in entry.runtime_data.data.consumers if v.config.name == name)


class TestOhneZiel:
    async def test_aendert_nichts(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Der Regelfall: kein Tagesziel, kein Netzstrom."""
        entry = make_entry(poolpumpe())
        await setup(hass, entry)
        await arm(hass, entry)

        assert not schaltungen["on"]
        assert view(entry).must_run is False
        assert view(entry).daily_target_kwh == 0


class TestMitZiel:
    async def test_prognose_reicht_also_wird_gewartet(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """6 h à 500 W sind 3 kWh. 10 kWh erwartet: das geht sich aus."""
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, prognose="10")
        await arm(hass, entry)

        assert not schaltungen["on"]
        assert view(entry).daily_target_kwh == pytest.approx(3.0)
        assert view(entry).must_run is False

    async def test_prognose_reicht_nicht_also_wird_geschaltet(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Der Kern: 3 kWh fehlen, 0,5 kWh erwartet, kein Überschuss.

        Ohne dieses Merkmal bliebe die Pumpe aus und das Tagesziel unerfüllt.
        """
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, prognose="0.5")
        await arm(hass, entry)

        assert [call.data["entity_id"] for call in schaltungen["on"]] == [PUMPE]
        assert view(entry).must_run is True

    async def test_ohne_prognosesensor_geschieht_nichts(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Ein fehlender Wert ist kein Grund, Netzstrom zu ziehen."""
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}), prognose=False)
        await setup(hass, entry)
        await arm(hass, entry)

        assert not schaltungen["on"]
        assert view(entry).must_run is False
        assert view(entry).daily_forecast_kwh is None

    async def test_erreichtes_ziel_schaltet_nicht(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, prognose="0.5", zaehler="0")
        # Der Tageszähler steht auf 0 kWh; jetzt hat die Pumpe 4 kWh geschafft.
        hass.states.async_set(ZAEHLER, "4", {"unit_of_measurement": "kWh"})
        await arm(hass, entry)

        assert not schaltungen["on"]
        assert view(entry).must_run is False
        assert view(entry).daily_done_kwh == pytest.approx(4.0)

    async def test_ein_laufender_verbraucher_bleibt_an(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Sonst würde er im Defizit abgeschaltet und käme nie ans Ziel."""
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, prognose="0.5", state=STATE_ON)
        await arm(hass, entry)

        assert not schaltungen["off"]
        assert view(entry).status.value == "on_ok"


class TestModulierend:
    async def test_das_ziel_ist_bereits_eine_energie(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Bei einer modulierenden Last sind Stunden keine Aussage.

        Sechs Stunden auf der kleinsten Stufe und sechs auf der größten erfüllen
        ein Stundenziel gleich gut und unterscheiden sich um ein Vielfaches.
        """
        entry = make_entry(
            poolpumpe(
                **{
                    CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
                    CONF_DAILY_TARGET: 12,
                }
            )
        )
        await setup(hass, entry)

        # 12 kWh, nicht 12 h mal Leistung.
        assert view(entry).daily_target_kwh == pytest.approx(12.0)


class TestTageszaehler:
    async def test_zaehlt_ab_dem_stand_bei_tagesbeginn(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, zaehler="120.5")
        hass.states.async_set(ZAEHLER, "122.0", {"unit_of_measurement": "kWh"})
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        assert view(entry).daily_done_kwh == pytest.approx(1.5)

    async def test_ein_zaehlerreset_setzt_neu_auf(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Ein ausgetauschtes oder stromlos gewesenes Gerät beginnt bei 0.

        Ohne diese Prüfung ergäbe die Differenz einen negativen Verbrauch, und
        das Tagesziel wäre für den Rest des Tages unerreichbar.
        """
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, zaehler="120.5")
        hass.states.async_set(ZAEHLER, "0.2", {"unit_of_measurement": "kWh"})
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        assert view(entry).daily_done_kwh == pytest.approx(0.0)

    async def test_ueberlebt_einen_neustart(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Sonst begänne der Tag mitten am Nachmittag von vorn."""
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, zaehler="120.5")

        from custom_components.energy_manager.models import ConsumerRuntime

        subentry_id = next(iter(entry.runtime_data.consumers))
        gespeichert = entry.runtime_data.runtime_for(subentry_id).as_dict()
        wieder = ConsumerRuntime.from_dict(gespeichert)

        assert wieder.day_start_kwh == pytest.approx(120.5)
        assert wieder.day_start_ts == gespeichert["day_start_ts"]

    async def test_ein_leistungssensor_statt_eines_zaehlers(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Falsche Einheit: nicht als 0 kWh lesen.

        Als 0 gelesen sähe es aus, als hätte das Gerät nie gelaufen — und die
        Automatik zöge daraus, dass es dringend laufen muss.
        """
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        hass.states.async_set("sensor.netz", "1000", {"unit_of_measurement": "W"})
        hass.states.async_set(ZAEHLER, "450", {"unit_of_measurement": "W"})
        hass.states.async_set(PROGNOSE, "0.5", {"unit_of_measurement": "kWh"})
        hass.states.async_set(PUMPE, STATE_OFF)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        subentry_id = next(iter(entry.runtime_data.consumers))
        # Kein Tagesbeginn gesetzt: Ohne verwertbaren Zählerstand gibt es nichts
        # zu verankern.
        assert entry.runtime_data.runtime_for(subentry_id).day_start_kwh is None
        assert view(entry).daily_done_kwh == 0


class TestSchutz:
    async def test_wird_nicht_verdraengt(self, hass: HomeAssistant, schaltungen: dict) -> None:
        """Ihn für einen wichtigeren abzuschalten hieße, ihm die Zeit zu nehmen,
        die ihm ohnehin fehlt."""
        entry = make_entry(
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
            poolpumpe(**{CONF_DAILY_TARGET: 6}),
        )
        hass.states.async_set("switch.waermepumpe", STATE_OFF)
        await setup(hass, entry, grid="0", prognose="0.5", state=STATE_ON)

        coordinator = entry.runtime_data
        ids = {v.name: k for k, v in coordinator.consumers.items()}
        await coordinator.async_set_priority(ids["Waermepumpe"], 1.0)
        await coordinator.async_set_priority(ids["Poolpumpe"], 2.0)
        await hass.async_block_till_done()

        pumpe = view(entry, "Waermepumpe")
        assert pumpe.displaceable == ()
        assert pumpe.throttleable == ()


class TestAttribute:
    async def test_am_status_sensor(self, hass: HomeAssistant, schaltungen: dict) -> None:
        entry = make_entry(poolpumpe(**{CONF_DAILY_TARGET: 6}))
        await setup(hass, entry, prognose="0.5")
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Poolpumpe"
        )

        assert status.attributes["daily_target_kwh"] == pytest.approx(3.0)
        assert status.attributes["daily_done_kwh"] == 0
        assert status.attributes["daily_forecast_kwh"] == pytest.approx(0.5)
        assert status.attributes["must_run"] is True

    async def test_ohne_ziel_keine_angaben(self, hass: HomeAssistant, schaltungen: dict) -> None:
        entry = make_entry(poolpumpe())
        await setup(hass, entry)
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Poolpumpe"
        )

        assert "daily_target_kwh" not in status.attributes
        assert "must_run" not in status.attributes
