"""Das Scharfschalten — und alles, was es verhindern muss.

Hier liegt das eigentliche Risiko der Integration: Sie greift in eine reale
Anlage ein. Jeder Test prüft entweder, dass geschaltet wird, wenn es richtig
ist, oder dass es unterbleibt, wenn es falsch wäre. Der zweite Teil ist der
wichtigere.

Aufbau aller Tests: erst den Zustand herstellen, **dann** scharfschalten. Das
Scharfschalten löst selbst eine Auswertung aus — wer danach noch etwas ändert,
prüft nicht mehr, was er zu prüfen glaubt.
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
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_OFF_TIME,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONF_TURN_ON_DELAY,
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


def make_entry(*consumers: ConfigSubentryData, **options) -> MockConfigEntry:
    """Ein Eintrag mit Netzsensor. Glättung aus, damit der Momentanwert gilt."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60, **options},
        unique_id=DOMAIN,
        subentries_data=list(consumers),
    )


@pytest.fixture
def entry() -> MockConfigEntry:
    """Ein Verbraucher mit 2000 W Nennleistung."""
    return make_entry(consumer_subentry("Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 2000}))


@pytest.fixture
def schaltungen(hass: HomeAssistant) -> dict[str, list]:
    """Schneidet mit, was die Integration schalten WOLLTE.

    Der Zustandswechsel selbst bleibt aus: ``homeassistant.turn_on`` braucht die
    switch-Integration, die hier nicht geladen ist. Der Aufruf ist ohnehin das,
    was zu prüfen ist — was das Gerät daraus macht, ist HAs Sache.
    """
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
    }


async def setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    grid: str = "-3000",
    unit: str = "W",
    switches: dict[str, str] | None = None,
) -> None:
    """Richtet die Integration ein — Automatik noch aus."""
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": unit})
    for entity_id, state in (switches or {"switch.heizstab": STATE_OFF}).items():
        hass.states.async_set(entity_id, state)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def arm(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Schaltet die Automatik scharf und wertet aus."""
    await entry.runtime_data.async_set_automation(True)
    await hass.async_block_till_done()


def nichts_geschaltet(schaltungen: dict[str, list]) -> bool:
    return not schaltungen["on"] and not schaltungen["off"]


def geschaltet(schaltungen: dict[str, list], richtung: str) -> list[str]:
    return [call.data["entity_id"] for call in schaltungen[richtung]]


def erster_verbraucher(entry: MockConfigEntry) -> str:
    return next(iter(entry.runtime_data.consumers))


class TestSchaltetNicht:
    """Die Fälle, in denen nichts geschehen darf."""

    async def test_nicht_ohne_hauptschalter(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Der Grundzustand nach dem Einrichten: beobachten, nicht eingreifen."""
        await setup(hass, entry)
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        assert entry.runtime_data.automation_enabled is False
        assert nichts_geschaltet(schaltungen)

    async def test_nicht_bei_unbrauchbarem_sensor(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Ein kWh-Zähler ist ein Konfigurationsfehler, kein Überschuss von 0."""
        await setup(hass, entry, grid="4211", unit="kWh")
        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert entry.runtime_data.data.may_switch is False

    async def test_nicht_bei_ausgefallenem_sensor(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry, grid="unavailable")
        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert entry.runtime_data.data.may_switch is False

    async def test_nicht_ohne_teilnahme_an_der_automatik(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Wer aus der Automatik genommen ist, wird nicht angefasst."""
        await setup(hass, entry)
        coordinator = entry.runtime_data
        subentry_id = erster_verbraucher(entry)

        await coordinator.async_set_managed(subentry_id, False)
        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert coordinator.data.blockers[subentry_id] == "not_managed"

    async def test_nicht_bei_zu_kurzem_ueberschuss(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """turn_on_delay: eine einzelne Sonnenlücke reicht nicht."""
        entry = make_entry(
            consumer_subentry(
                "Heizstab",
                "switch.heizstab",
                **{CONF_MAX_POWER: 2000, CONF_TURN_ON_DELAY: 600},
            )
        )
        await setup(hass, entry)
        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert entry.runtime_data.data.blockers[erster_verbraucher(entry)] == "turn_on_delay"

    async def test_nicht_waehrend_der_sperrzeit(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """min_off_time: der Kompressor braucht seinen Druckausgleich."""
        entry = make_entry(
            consumer_subentry(
                "Heizstab",
                "switch.heizstab",
                **{CONF_MAX_POWER: 2000, CONF_MIN_OFF_TIME: 600},
            )
        )
        await setup(hass, entry)
        coordinator = entry.runtime_data
        subentry_id = erster_verbraucher(entry)

        # So, als hätte die Integration gerade selbst ausgeschaltet — aber ohne
        # Beruhigungsfenster, damit wirklich min_off_time greift und nicht
        # schon settling blockiert.
        runtime = coordinator.runtime_for(subentry_id)
        runtime.last_switch_ts = dt_util.utcnow().timestamp()
        runtime.last_switch_to = False

        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert coordinator.data.blockers[subentry_id] == "min_off_time"

    async def test_nicht_im_beruhigungsfenster(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Nach einer Schaltung bleibt dieses Gerät kurz unangetastet."""
        await setup(hass, entry)
        coordinator = entry.runtime_data
        subentry_id = erster_verbraucher(entry)
        coordinator.runtime_for(subentry_id).settle_until = dt_util.utcnow().timestamp() + 60

        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert coordinator.data.blockers[subentry_id] == "settling"

    async def test_nicht_bevor_ha_durchgestartet_ist(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Während des Starts meldet nicht jede Entität einen Zustand.

        Auf halb gefüllten Daten zu entscheiden ist der schlechteste denkbare
        Zeitpunkt — dann sähe die Anlage aus wie nach einem Totalausfall.
        """
        await setup(hass, entry)
        coordinator = entry.runtime_data
        coordinator._started = False

        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert coordinator.data.started is False
        assert coordinator.data.may_switch is False

    async def test_nicht_bei_zu_duennem_mittelwert(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Ein kaum gefülltes Mittelungsfenster schwankt zu stark.

        Darauf zu schalten hieße, die Glättung gerade dann zu übergehen, wenn
        sie am nötigsten ist.
        """
        entry = make_entry(
            consumer_subentry("Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 2000}),
            **{CONF_SMOOTHING_WINDOW: 600},
        )
        await setup(hass, entry)
        await arm(hass, entry)

        coordinator = entry.runtime_data
        assert coordinator.data.coverage < 0.5
        assert coordinator.data.may_switch is False
        assert nichts_geschaltet(schaltungen)

    async def test_min_runtime_haelt_das_geraet_an(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Ein gerade eingeschaltetes Gerät wird nicht sofort abgeworfen."""
        entry = make_entry(
            consumer_subentry(
                "Heizstab",
                "switch.heizstab",
                **{CONF_MAX_POWER: 2000, CONF_MIN_RUNTIME: 900},
            )
        )
        # Gerät läuft, aber der Überschuss ist weg: 2000 W Netzbezug.
        await setup(hass, entry, grid="2000", switches={"switch.heizstab": STATE_ON})
        coordinator = entry.runtime_data
        subentry_id = erster_verbraucher(entry)

        # So, als hätte die Integration es gerade eingeschaltet.
        runtime = coordinator.runtime_for(subentry_id)
        runtime.last_switch_ts = dt_util.utcnow().timestamp()
        runtime.last_switch_to = True

        await arm(hass, entry)

        assert nichts_geschaltet(schaltungen)
        assert coordinator.data.blockers[subentry_id] == "min_runtime"


class TestSchaltet:
    """Die Fälle, in denen es geschehen soll."""

    async def test_schaltet_bei_ausreichendem_ueberschuss_ein(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        await setup(hass, entry)
        await arm(hass, entry)

        assert geschaltet(schaltungen, "on") == ["switch.heizstab"]

    async def test_schaltet_bei_anhaltendem_defizit_aus(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        # Gerät läuft, 1000 W Netzbezug.
        await setup(hass, entry, grid="1000", switches={"switch.heizstab": STATE_ON})
        await arm(hass, entry)

        assert geschaltet(schaltungen, "off") == ["switch.heizstab"]

    async def test_merkt_sich_die_eigene_schaltung(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Grundlage für Sperrzeiten und Beruhigungsfenster.

        Auf ``last_changed`` der Entität wäre kein Verlass — manuelles Schalten
        und ein Neustart setzen es zurück.
        """
        await setup(hass, entry)
        await arm(hass, entry)

        runtime = entry.runtime_data.runtime_for(erster_verbraucher(entry))
        assert runtime.last_switch_ts is not None
        assert runtime.last_switch_to is True
        assert runtime.settle_until is not None
        # Die zugeschaltete Last, die im Messwert noch fehlt.
        assert runtime.anticipated_w == 2000.0

    async def test_nur_eine_schaltung_je_durchlauf(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Jede Schaltung verändert den Überschuss, auf den die nächste baut.

        Wer drei Geräte gleichzeitig zuschaltet, rechnet dreimal mit demselben
        Budget.
        """
        entry = make_entry(
            consumer_subentry("Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 500}),
            consumer_subentry("Boiler", "switch.boiler", **{CONF_MAX_POWER: 500}),
            consumer_subentry("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 500}),
        )
        await setup(
            hass,
            entry,
            switches={
                "switch.heizstab": STATE_OFF,
                "switch.boiler": STATE_OFF,
                "switch.pumpe": STATE_OFF,
            },
        )
        await arm(hass, entry)

        # 3000 W Überschuss würden für alle drei reichen — trotzdem nur einer.
        assert len(schaltungen["on"]) == 1


class TestAntizipation:
    """Der Schutz davor, auf die eigene Wirkung zu reagieren."""

    async def test_zugeschaltete_last_wird_sofort_abgezogen(
        self, hass: HomeAssistant, schaltungen: dict
    ) -> None:
        """Zwei Verbraucher, 3000 W Überschuss, je 2000 W Bedarf.

        Nach dem Zuschalten des ersten sind rechnerisch nur noch 1000 W übrig —
        auch wenn der Zähler das noch nicht zeigt. Ohne die Korrektur bekäme der
        zweite Verbraucher denselben Überschuss ein zweites Mal.
        """
        entry = make_entry(
            consumer_subentry("Heizstab", "switch.heizstab", **{CONF_MAX_POWER: 2000}),
            consumer_subentry("Boiler", "switch.boiler", **{CONF_MAX_POWER: 2000}),
        )
        await setup(
            hass,
            entry,
            switches={"switch.heizstab": STATE_OFF, "switch.boiler": STATE_OFF},
        )
        await arm(hass, entry)
        assert len(schaltungen["on"]) == 1

        coordinator = entry.runtime_data
        assert coordinator.data.surplus.anticipated_w == 2000

        # Der Zähler zeigt die neue Last noch nicht. Der zweite darf trotzdem
        # nicht folgen: 3000 minus 2000 = 1000 W reichen nicht für 2000 W.
        await coordinator.async_request_refresh_now()
        await hass.async_block_till_done()

        assert len(schaltungen["on"]) == 1, (
            "Der zweite Verbraucher hat denselben Überschuss ein zweites Mal bekommen"
        )

    async def test_korrektur_endet_mit_dem_beruhigungsfenster(
        self, hass: HomeAssistant, entry: MockConfigEntry, schaltungen: dict
    ) -> None:
        """Danach steckt die Last im Messwert und darf nicht doppelt zählen."""
        await setup(hass, entry)
        await arm(hass, entry)

        coordinator = entry.runtime_data
        assert coordinator.data.surplus.anticipated_w == 2000

        # Nachstellen, was in einer echten Anlage von selbst geschieht: Das
        # Gerät läuft, und der Zähler zeigt seine Last inzwischen mit.
        hass.states.async_set("switch.heizstab", STATE_ON)
        hass.states.async_set("sensor.netz", "-1000", {"unit_of_measurement": "W"})
        coordinator.runtime_for(erster_verbraucher(entry)).settle_until = (
            dt_util.utcnow().timestamp() - 1
        )
        await coordinator.async_request_refresh_now()
        await hass.async_block_till_done()

        # Keine Korrektur mehr — und die 1000 W Rest werden nicht ein zweites
        # Mal um die bereits laufende Last gekürzt.
        assert coordinator.data.surplus.anticipated_w == 0
        assert coordinator.data.surplus.available == 1000
