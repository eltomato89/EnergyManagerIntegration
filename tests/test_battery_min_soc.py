"""Die Ladevorranggrenze als Stellgröße.

Unterhalb dieses Ladestands hat das Laden der Batterie Vorrang: Es wird kein
Überschuss mehr an Verbraucher ausgewiesen. Bis zu dieser Fassung stand die
Grenze als Zahl im Einrichtungsformular — eine Vorgabe, die sich nur mit dem
Formular ändern ließ.

Sie ändert sich aber mit dem Tag: Bei sehr guter Vorhersage darf ein Verbraucher
schon am Vormittag bei niedrigem Ladestand anlaufen, an einem trüben Tag nicht.
Deshalb steht sie jetzt hinter einer eigenen Entität und lässt sich damit auch
automatisieren.
"""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_GRID_ENTITY,
    CONF_METER_MODE,
    CONF_SMOOTHING_WINDOW,
    DOMAIN,
    METER_MODE_GRID,
)

# Die Prüfinstanz läuft auf Englisch; der Name kommt aus den Übersetzungen.
REGLER = "number.energy_manager_battery_minimum_state_of_charge"


def make_entry(*, soc_sensor: bool = True, **extra) -> MockConfigEntry:
    data = {
        CONF_METER_MODE: METER_MODE_GRID,
        CONF_GRID_ENTITY: "sensor.netz",
        CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
        **extra,
    }
    if soc_sensor:
        data[CONF_BATTERY_SOC_ENTITY] = "sensor.akku_soc"

    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data=data,
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry, *, soc: int = 30) -> None:
    """2000 W Einspeisung — genug Überschuss, damit nur die Grenze ihn nimmt."""
    hass.states.async_set("sensor.netz", "-2000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.akku_leistung", "0", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.akku_soc", str(soc), {"unit_of_measurement": "%"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def verfuegbar(entry: MockConfigEntry) -> float | None:
    return entry.runtime_data.data.surplus.available


class TestEntitaet:
    async def test_entsteht_mit_ladestandssensor(self, hass: HomeAssistant) -> None:
        await setup(hass, make_entry())

        assert hass.states.get(REGLER) is not None

    async def test_entsteht_nicht_ohne_ladestandssensor(self, hass: HomeAssistant) -> None:
        """Ohne Messwert bliebe der Regler wirkungslos.

        Ein wirkungsloser Regler ist schlimmer als keiner: Er sieht aus wie eine
        Einstellung und ist keine. Vorher war das eine Fehlermeldung im
        Formular — jetzt entsteht die Entität schlicht nicht.
        """
        await setup(hass, make_entry(soc_sensor=False))

        assert hass.states.get(REGLER) is None

    async def test_ist_eine_konfigurationsentitaet(self, hass: HomeAssistant) -> None:
        await setup(hass, make_entry())
        eintrag = er.async_get(hass).async_get(REGLER)

        assert eintrag is not None
        assert eintrag.entity_category is er.EntityCategory.CONFIG

    async def test_steht_anfangs_auf_null(self, hass: HomeAssistant) -> None:
        """0 heißt: Jeder Ladestand liegt über der Grenze, die Regel greift nicht.

        Der Rückfallwert für alle, die nie etwas eingetragen haben — das
        bisherige Verhalten.
        """
        await setup(hass, make_entry())

        assert hass.states.get(REGLER).state == "0.0"


class TestUebernahme:
    async def test_uebernimmt_den_wert_aus_der_alten_konfiguration(
        self, hass: HomeAssistant
    ) -> None:
        """Wer die Grenze im Formular eingetragen hat, findet sie unverändert wieder.

        Ohne diese Übernahme stünde sie nach dem Update auf 0, und die Batterie
        verlöre still ihren Ladevorrang — eine Änderung des Verhaltens, die
        niemand angestoßen hat.
        """
        await setup(hass, make_entry(**{CONF_BATTERY_MIN_SOC: 40}))

        assert hass.states.get(REGLER).state == "40.0"

    async def test_der_gespeicherte_wert_geht_vor(
        self, hass: HomeAssistant, hass_storage: dict
    ) -> None:
        """Nach der ersten Änderung zählt nur noch der Laufzeitwert.

        Sonst spränge die Grenze bei jedem Neustart auf den alten Formularwert
        zurück.
        """
        entry = make_entry(**{CONF_BATTERY_MIN_SOC: 40})
        hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
            "version": 1,
            "data": {"battery_min_soc": 15.0},
        }
        await setup(hass, entry)

        assert hass.states.get(REGLER).state == "15.0"


class TestWirkung:
    async def test_unter_der_grenze_gibt_es_keinen_ueberschuss(self, hass: HomeAssistant) -> None:
        entry = make_entry()
        await setup(hass, entry, soc=30)
        await hass.services.async_call(
            "number", "set_value", {"entity_id": REGLER, "value": 50}, blocking=True
        )
        await hass.async_block_till_done()

        assert verfuegbar(entry) == 0

    async def test_ueber_der_grenze_steht_er_wieder_bereit(self, hass: HomeAssistant) -> None:
        """Der Kern des Wunsches: heruntergestellt, und die Verbraucher dürfen.

        Sofort und nicht erst beim nächsten Durchlauf — wer die Grenze wegen
        einer guten Vorhersage senkt, erwartet keine Wartezeit.
        """
        entry = make_entry()
        await setup(hass, entry, soc=30)
        await hass.services.async_call(
            "number", "set_value", {"entity_id": REGLER, "value": 50}, blocking=True
        )
        await hass.async_block_till_done()
        assert verfuegbar(entry) == 0

        await hass.services.async_call(
            "number", "set_value", {"entity_id": REGLER, "value": 20}, blocking=True
        )
        await hass.async_block_till_done()

        assert verfuegbar(entry) == pytest.approx(2000)

    async def test_ohne_ladestand_bleibt_die_grenze_wirkungslos(self, hass: HomeAssistant) -> None:
        """Ein unlesbarer Ladestand ist kein Grund, den Überschuss zu sperren.

        Er ist ein Fehler und wird als solcher gemeldet; eine Grenze gegen einen
        unbekannten Wert zu prüfen hieße raten.
        """
        entry = make_entry()
        await setup(hass, entry, soc=30)
        await hass.services.async_call(
            "number", "set_value", {"entity_id": REGLER, "value": 50}, blocking=True
        )
        hass.states.async_set("sensor.akku_soc", STATE_UNAVAILABLE)
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        assert verfuegbar(entry) == pytest.approx(2000)
