"""Erkennung von Schaltvorgängen, die nicht von dieser Integration kamen.

Noch ohne jede Wirkung: hier wird nur festgehalten. Der Nutzen dieser Stufe ist
die Messung — wie oft tritt der Fall je Gerät auf, und greifen die Filter? Die
Antwort entscheidet später über die Vorgabe einer befristeten Übersteuerung, und
sie ist nicht vorhersagbar, weil sie an der Geräteklasse hängt.

Der schwierige Teil ist nicht das Erkennen, sondern das **Nicht**-Erkennen: Eine
Integration, die den Context der eigenen Schaltung nicht durchreicht, bestätigt
sie später per Abfrage — äußerlich wie ein Eingriff von Hand. Wäre dieser Fall
nicht abgedeckt, meldete die Erkennung nach jeder eigenen Schaltung einen
Fremdeingriff und sabotierte sich selbst.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_NAME,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    FOREIGN_CONFIRM_FACTOR,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)

SCHALTER = "switch.heizstab"
SETTLE = 60


@pytest.fixture
def entry() -> MockConfigEntry:
    """Ein Verbraucher mit 2000 W. Glättung aus, damit der Momentanwert gilt."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: SETTLE},
        unique_id=DOMAIN,
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Heizstab",
                unique_id=None,
                data={
                    CONF_NAME: "Heizstab",
                    CONF_SWITCH_ENTITY: SCHALTER,
                    CONF_MAX_POWER: 2000,
                },
            )
        ],
    )


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    """Schneidet mit, was die Integration schalten wollte — samt Context."""
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Richtet die Integration ein. Automatik bleibt aus."""
    hass.states.async_set("sensor.netz", "-3000", {"unit_of_measurement": "W"})
    hass.states.async_set(SCHALTER, STATE_OFF)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def runtime(entry: MockConfigEntry):
    subentry_id = next(iter(entry.runtime_data.consumers))
    return entry.runtime_data.runtime_for(subentry_id)


async def schalte(
    hass: HomeAssistant,
    state: str,
    context: Context | None = None,
) -> None:
    """Setzt den Zustand der Schalt-Entität, wie HA es täte."""
    hass.states.async_set(SCHALTER, state, context=context or Context())
    await hass.async_block_till_done()


class TestWirdFestgehalten:
    """Die Fälle, in denen tatsächlich jemand anders geschaltet hat."""

    async def test_einschalten_von_hand(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)
        await schalte(hass, STATE_ON)

        assert runtime(entry).last_foreign_change is not None
        assert runtime(entry).last_foreign_to is True

    async def test_ausschalten_von_hand(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)
        await schalte(hass, STATE_ON)
        await schalte(hass, STATE_OFF)

        assert runtime(entry).last_foreign_to is False

    async def test_nach_der_gnadenfrist_zaehlt_dieselbe_richtung(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Sonst verschluckte die Bestätigungsregel echte Eingriffe.

        Die Automatik hat vor langer Zeit eingeschaltet, das Gerät ging
        zwischendurch aus, und jetzt schaltet jemand von Hand wieder ein — in
        dieselbe Richtung. Das ist ein Eingriff, keine verspätete Bestätigung.
        """
        await setup(hass, entry)
        laufzeit = runtime(entry)
        jetzt = dt_util.utcnow().timestamp()
        laufzeit.last_switch_ts = jetzt - SETTLE * FOREIGN_CONFIRM_FACTOR - 10
        laufzeit.last_switch_to = True
        laufzeit.settle_until = jetzt - 1

        await schalte(hass, STATE_ON)

        assert laufzeit.last_foreign_change is not None

    async def test_ueberlebt_einen_neustart(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Wert wird persistiert — eine Messung über Wochen braucht das."""
        await setup(hass, entry)
        await schalte(hass, STATE_ON)

        gespeichert = runtime(entry).as_dict()
        assert gespeichert["last_foreign_change"] is not None

        from custom_components.energy_manager.models import ConsumerRuntime

        wieder = ConsumerRuntime.from_dict(gespeichert)
        assert wieder.last_foreign_change == gespeichert["last_foreign_change"]
        assert wieder.last_foreign_to is True


class TestWirdNichtFestgehalten:
    """Die Fälle, die als Fremdeingriff gelesen würden, aber keiner sind.

    Der wichtigere Teil: Eine Fehlerkennung führt später dazu, dass sich die
    Automatik von einem Gerät fernhält, das sie verwalten soll.
    """

    async def test_die_eigene_schaltung(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Erkennung über den Context der eigenen Schaltung.

        Das Beruhigungsfenster wird hier absichtlich aufgehoben, sonst deckte
        schon es den Fall ab und der Context bliebe ungeprüft.
        """
        await setup(hass, entry)
        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert len(schaltungen["on"]) == 1
        eigener = schaltungen["on"][0].context

        runtime(entry).settle_until = dt_util.utcnow().timestamp() - 1
        await schalte(hass, STATE_ON, context=eigener)

        assert runtime(entry).last_foreign_change is None

    async def test_die_zwangsfreigabe(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Eine Bedienung über den Dienst ist keine Fremdschaltung.

        Sie kommt vom Nutzer, aber sie kommt durch diese Integration — sie darf
        sich nicht selbst als Übersteuerung zählen.
        """
        await setup(hass, entry)
        subentry_id = next(iter(entry.runtime_data.consumers))
        await entry.runtime_data.async_force_on(subentry_id, 600)
        await hass.async_block_till_done()

        assert len(schaltungen["on"]) == 1
        eigener = schaltungen["on"][0].context

        runtime(entry).settle_until = dt_util.utcnow().timestamp() - 1
        await schalte(hass, STATE_ON, context=eigener)

        assert runtime(entry).last_foreign_change is None

    async def test_im_beruhigungsfenster(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Fall, der die Erkennung sonst unbrauchbar machte.

        Eine träge Integration bestätigt die eigene Schaltung erst per Abfrage,
        und zwar mit frischem Context. Ohne diesen Filter meldete die Erkennung
        nach **jeder** eigenen Schaltung einen Fremdeingriff.
        """
        await setup(hass, entry)
        runtime(entry).settle_until = dt_util.utcnow().timestamp() + SETTLE

        await schalte(hass, STATE_ON)

        assert runtime(entry).last_foreign_change is None

    async def test_verspaetete_bestaetigung_nach_dem_fenster(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Dieselbe Trägheit, nur langsamer als das Beruhigungsfenster."""
        await setup(hass, entry)
        laufzeit = runtime(entry)
        jetzt = dt_util.utcnow().timestamp()
        laufzeit.last_switch_ts = jetzt - SETTLE - 5
        laufzeit.last_switch_to = True
        laufzeit.settle_until = jetzt - 1

        await schalte(hass, STATE_ON)

        assert laufzeit.last_foreign_change is None

    async def test_wiedererreichbar_werden(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Ein Zwischenstecker, der das WLAN verliert und als „aus" zurückkommt.

        Der Übergang ist eine Zustandsänderung mit frischem Context — geschaltet
        hat ihn niemand.
        """
        await setup(hass, entry)
        await schalte(hass, STATE_UNAVAILABLE)
        assert runtime(entry).last_foreign_change is None

        await schalte(hass, STATE_OFF)
        assert runtime(entry).last_foreign_change is None

    async def test_vor_dem_durchstarten(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Während des Starts meldet jede Entität neu, mit frischem Context."""
        await setup(hass, entry)
        entry.runtime_data._started = False

        await schalte(hass, STATE_ON)

        assert runtime(entry).last_foreign_change is None

    async def test_eine_fremde_entitaet(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Netzsensor ändert sich dauernd und ist kein Verbraucher."""
        await setup(hass, entry)
        hass.states.async_set("sensor.netz", "-1000", {"unit_of_measurement": "W"})
        await hass.async_block_till_done()

        assert runtime(entry).last_foreign_change is None


async def test_attribute_am_status_sensor(
    hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
) -> None:
    """Ohne Ausweis in den Attributen ist der Wert nicht ablesbar.

    Genau das ist der Zweck dieser Stufe: der Nutzer soll vor dem Einschalten
    einer Übersteuerung sehen können, wie oft der Fall bei ihm auftritt.

    Der Zwischenschritt über eine Auswertung gehört dazu: Der Laufzeitzustand
    ist sofort gesetzt, die Attribute entstehen aber erst beim Veröffentlichen.
    In einer echten Anlage folgt das binnen des Entprellfensters von selbst,
    weil dieselbe Zustandsänderung auch die Auswertung anstößt.
    """
    await setup(hass, entry)
    await schalte(hass, STATE_ON)
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()

    status = next(
        state
        for state in hass.states.async_all("sensor")
        if state.attributes.get("consumer_id") is not None
    )

    assert status.attributes["last_foreign_change"] is not None
    assert status.attributes["last_foreign_to"] is True
    # Der Verhaltenstyp liegt schon an, damit die Karte ihn nicht nachrüsten muss.
    assert status.attributes["consumer_type"] == "switch"
