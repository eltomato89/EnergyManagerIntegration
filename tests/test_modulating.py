"""Regelbare Verbraucher, durch die ganze Kette.

Hier zählt nicht mehr die Rechnung — die steht in ``test_levels.py`` — sondern
dass sie an der richtigen Stelle in der Budget-Kaskade landet und dass die
Stellgröße auch geschrieben wird.

Der wichtigste Test der Datei ist
``test_der_verbraucher_darunter_bekommt_nur_den_rest``: Er prüft die einzige
Stelle, an der ein Fehler nicht auffallen würde. Eine falsch verrechnete Stufe
stürzt nicht ab und erscheint in keinem Log — sie führt dazu, dass ein
Verbraucher weiter unten gelegentlich falsch geschaltet wird.
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
    CONF_PHASES,
    CONF_POWER_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONSUMER_TYPE_MODULATING,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)

# Ein Heizstab mit drei runden Stufen — die Rechnung soll im Kopf nachvollziehbar
# bleiben. Über select, damit die Watt-Werte in der Konfiguration stehen und
# nicht aus Ampere hergeleitet werden müssen.
STUFEN = {"aus": 0, "niedrig": 1000, "mittel": 2000, "hoch": 3000}
OPTIONEN = ["aus", "niedrig", "mittel", "hoch"]


def regelbar(name: str, switch: str, control: str, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={
            CONF_NAME: name,
            CONF_SWITCH_ENTITY: switch,
            CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
            CONF_CONTROL_ENTITY: control,
            CONF_LEVEL_MAP: STUFEN,
            **extra,
        },
    )


def schaltbar(name: str, switch: str, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={CONF_NAME: name, CONF_SWITCH_ENTITY: switch, **extra},
    )


def make_entry(*consumers: ConfigSubentryData, **options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60, **options},
        unique_id=DOMAIN,
        subentries_data=list(consumers),
    )


@pytest.fixture
def dienste(hass: HomeAssistant) -> dict[str, list]:
    """Schneidet mit, was die Integration schalten und stellen wollte.

    ``number.set_value`` fehlt hier bewusst: Die Integration richtet selbst eine
    ``number``-Plattform ein (die Prioritäten), und Home Assistant registriert
    dabei seinen echten ``set_value``-Dienst — der überschreibt eine Attrappe,
    die vorher gesetzt wurde. Sie muss deshalb **nach** dem Einrichten kommen,
    siehe :class:`TestNumberSteuerung`. Bei ``select`` tritt das nicht auf, weil
    die Integration keine Select-Plattform hat.
    """
    return {
        "on": async_mock_service(hass, HA_DOMAIN, "turn_on"),
        "off": async_mock_service(hass, HA_DOMAIN, "turn_off"),
        "level": async_mock_service(hass, "select", "select_option"),
    }


async def setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    grid: str = "-2500",
    *,
    switches: dict[str, str] | None = None,
    option: str = "aus",
    power: dict[str, str] | None = None,
) -> None:
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": "W"})
    hass.states.async_set("select.heizstab", option, {"options": OPTIONEN})
    for entity_id, state in (switches or {"switch.heizstab": STATE_OFF}).items():
        hass.states.async_set(entity_id, state)
    for entity_id, value in (power or {}).items():
        hass.states.async_set(entity_id, value, {"unit_of_measurement": "W"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def arm(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_set_automation(True)
    await hass.async_block_till_done()


async def evaluate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Wertet aus, **ohne** zu schalten — für Aussagen über die Verteilung.

    Bewusst ohne Scharfschalten. Nach einer Handlung ist der veröffentlichte
    Zustand bereits der danach: Die Antizipation hat die gerade vergebene Last
    schon abgezogen, und weil der Dienst im Test attrappiert ist, ändert sich
    der Zustand der Entitäten nicht mit. Wer die Budget-Kaskade prüfen will,
    muss sie vor der Handlung ansehen — sonst prüft er die Antizipation.
    """
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()


def view(entry: MockConfigEntry, name: str):
    return next(v for v in entry.runtime_data.data.consumers if v.config.name == name)


class TestEinschalten:
    async def test_startet_auf_der_passenden_stufe(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """2500 W Überschuss tragen die mittlere Stufe, nicht die kleinste."""
        entry = make_entry(regelbar("Heizstab", "switch.heizstab", "select.heizstab"))
        await setup(hass, entry)
        await arm(hass, entry)

        assert [call.data["entity_id"] for call in dienste["on"]] == ["switch.heizstab"]
        assert [call.data["option"] for call in dienste["level"]] == ["mittel"]

    async def test_die_stufe_wird_vor_dem_schalter_gestellt(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Sonst läuft das Gerät einen Durchlaufe lang auf der alten Stufe an."""
        entry = make_entry(regelbar("Heizstab", "switch.heizstab", "select.heizstab"))
        await setup(hass, entry, option="hoch")
        await arm(hass, entry)

        assert dienste["level"] and dienste["on"]
        assert dienste["level"][0].context.id != ""

    async def test_zu_wenig_ueberschuss_fuer_die_kleinste_stufe(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        entry = make_entry(regelbar("Heizstab", "switch.heizstab", "select.heizstab"))
        await setup(hass, entry, grid="-400")
        await arm(hass, entry)

        assert not dienste["on"]
        assert not dienste["level"]
        assert view(entry, "Heizstab").status.value == "off_insufficient"

    async def test_knapp_darunter_gilt_als_knapp(self, hass: HomeAssistant, dienste: dict) -> None:
        """80 % der kleinsten Stufe: 800 von 1000 W."""
        entry = make_entry(regelbar("Heizstab", "switch.heizstab", "select.heizstab"))
        await setup(hass, entry, grid="-850")
        await arm(hass, entry)

        assert view(entry, "Heizstab").status.value == "off_close"


class TestStufenwechsel:
    async def test_steigt_mit_dem_ueberschuss(self, hass: HomeAssistant, dienste: dict) -> None:
        """Läuft auf 1000 W, 1300 W frei: erreichbar 2300 → Stufe 2000."""
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung"},
            )
        )
        await setup(
            hass,
            entry,
            grid="-1300",
            switches={"switch.heizstab": STATE_ON},
            option="niedrig",
            power={"sensor.heizstab_leistung": "1000"},
        )
        await arm(hass, entry)

        assert [call.data["option"] for call in dienste["level"]] == ["mittel"]
        # Der Schalter bleibt unangetastet — es ist ein Stufenwechsel.
        assert not dienste["on"] and not dienste["off"]

    async def test_faellt_mit_dem_defizit(self, hass: HomeAssistant, dienste: dict) -> None:
        """Läuft auf 3000 W, 900 W Netzbezug: erreichbar 2100 → Stufe 2000."""
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung"},
            )
        )
        await setup(
            hass,
            entry,
            grid="900",
            switches={"switch.heizstab": STATE_ON},
            option="hoch",
            power={"sensor.heizstab_leistung": "3000"},
        )
        await arm(hass, entry)

        assert [call.data["option"] for call in dienste["level"]] == ["mittel"]
        assert not dienste["off"]

    async def test_drosseln_statt_abschalten_trotz_mindestlaufzeit(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Die Mindestlaufzeit schützt vor dem Abschalten, nicht vor dem Drosseln.

        Das Gerät läuft weiter — nur schwächer. Es wäre widersinnig, es deshalb
        auf einer zu hohen Stufe zu halten und Netzstrom zu ziehen.
        """
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung", CONF_MIN_RUNTIME: 3600},
            )
        )
        await setup(
            hass,
            entry,
            grid="900",
            switches={"switch.heizstab": STATE_ON},
            option="hoch",
            power={"sensor.heizstab_leistung": "3000"},
        )
        laufzeit = entry.runtime_data.runtime_for(next(iter(entry.runtime_data.consumers)))
        laufzeit.last_switch_ts = dt_util.utcnow().timestamp()
        laufzeit.last_switch_to = True

        await arm(hass, entry)

        assert [call.data["option"] for call in dienste["level"]] == ["mittel"]

    async def test_ganz_abschalten_wenn_keine_stufe_passt(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Läuft auf 1000 W, 600 W Netzbezug: erreichbar 400 → keine Stufe."""
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung"},
            )
        )
        await setup(
            hass,
            entry,
            grid="600",
            switches={"switch.heizstab": STATE_ON},
            option="niedrig",
            power={"sensor.heizstab_leistung": "1000"},
        )
        await arm(hass, entry)

        assert [call.data["entity_id"] for call in dienste["off"]] == ["switch.heizstab"]

    async def test_haltezeit_bremst_den_naechsten_wechsel(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung", CONF_LEVEL_HOLD: 600},
            )
        )
        await setup(
            hass,
            entry,
            grid="-1300",
            switches={"switch.heizstab": STATE_ON},
            option="niedrig",
            power={"sensor.heizstab_leistung": "1000"},
        )
        laufzeit = entry.runtime_data.runtime_for(next(iter(entry.runtime_data.consumers)))
        laufzeit.last_level_ts = dt_util.utcnow().timestamp()

        await arm(hass, entry)

        assert not dienste["level"]
        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.data.blockers[subentry_id] == "level_hold"


class TestBudgetKaskade:
    """Die Stelle, an der ein Fehler nicht auffiele."""

    async def test_der_verbraucher_darunter_bekommt_nur_den_rest(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """3000 W Überschuss. Rang 1 regelbar bis 3000 W, Rang 2 braucht 800 W.

        Greedy: Der Heizstab nimmt die höchste Stufe, die passt — 3000 W. Danach
        ist nichts mehr übrig, die Pumpe bleibt aus. Das ist die dokumentierte
        Regel, nicht ein Fehler.
        """
        entry = make_entry(
            regelbar("Heizstab", "switch.heizstab", "select.heizstab"),
            schaltbar("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 800}),
        )
        await setup(
            hass,
            entry,
            grid="-3000",
            switches={"switch.heizstab": STATE_OFF, "switch.pumpe": STATE_OFF},
        )
        await evaluate(hass, entry)

        heizstab = view(entry, "Heizstab")
        assert heizstab.target is not None
        assert heizstab.target.w == 3000
        assert heizstab.headroom_w == 0
        # Nach dem Heizstab ist nichts mehr da.
        assert view(entry, "Pumpe").status.value == "off_insufficient"

    async def test_nur_der_mehrbedarf_wird_abgezogen(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Der wichtigste Fall: Was läuft, steckt schon im Messwert.

        Der Heizstab läuft auf 1000 W, gemessen sind 1000 W frei. Erreichbar sind
        damit 2000 W, er geht auf die mittlere Stufe — der Mehrbedarf ist
        1000 W. Für die Pumpe bleibt danach nichts.

        Würde stattdessen die ganze Zielstufe abgezogen, stünde die Pumpe bei
        -1000 W statt bei 0. Würde nichts abgezogen, bekäme sie 1000 W, die der
        Heizstab gerade selbst beansprucht — und beide zögen zusammen Netzstrom.
        """
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung"},
            ),
            schaltbar("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 800}),
        )
        await setup(
            hass,
            entry,
            grid="-1000",
            switches={"switch.heizstab": STATE_ON, "switch.pumpe": STATE_OFF},
            option="niedrig",
            power={"sensor.heizstab_leistung": "1000"},
        )
        await evaluate(hass, entry)

        heizstab = view(entry, "Heizstab")
        assert heizstab.target is not None
        assert heizstab.target.w == 2000
        # 1000 W frei minus 1000 W Mehrbedarf = 0.
        assert heizstab.headroom_w == 0
        assert view(entry, "Pumpe").status.value == "off_insufficient"

    async def test_eine_drosselung_gibt_nichts_frei(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Die Leistung ist noch nicht zurückgeflossen.

        Der Heizstab muss von 3000 auf 2000 W. Die 1000 W, die dabei frei werden,
        stehen der Pumpe **nicht** sofort zur Verfügung: Der Zähler zeigt sie
        weiter, und pro Durchlauf findet nur eine Handlung statt. Bekäme die
        Pumpe sie jetzt, wäre derselbe Überschuss zweimal vergeben.
        """
        entry = make_entry(
            regelbar(
                "Heizstab",
                "switch.heizstab",
                "select.heizstab",
                **{CONF_POWER_ENTITY: "sensor.heizstab_leistung"},
            ),
            schaltbar("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 800}),
        )
        await setup(
            hass,
            entry,
            grid="900",
            switches={"switch.heizstab": STATE_ON, "switch.pumpe": STATE_OFF},
            option="hoch",
            power={"sensor.heizstab_leistung": "3000"},
        )
        await arm(hass, entry)

        # Der Heizstab wird gedrosselt ...
        assert [call.data["option"] for call in dienste["level"]] == ["mittel"]
        # ... aber die Pumpe geht nicht an.
        assert "switch.pumpe" not in [call.data["entity_id"] for call in dienste["on"]]

    async def test_ein_nicht_verwalteter_regelbarer_blockiert_kein_budget(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Dieselbe Regel wie bei schaltbaren Verbrauchern."""
        entry = make_entry(
            regelbar("Heizstab", "switch.heizstab", "select.heizstab"),
            schaltbar("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 800}),
        )
        await setup(
            hass,
            entry,
            grid="-3000",
            switches={"switch.heizstab": STATE_OFF, "switch.pumpe": STATE_OFF},
        )
        coordinator = entry.runtime_data
        heizstab_id = next(
            key for key, value in coordinator.consumers.items() if value.name == "Heizstab"
        )
        await coordinator.async_set_managed(heizstab_id, False)
        await evaluate(hass, entry)

        assert view(entry, "Pumpe").status.value == "off_ready"


class TestNumberSteuerung:
    """Der Wallbox-Fall: Stellgröße in Ampere, Leiter aus der Entität."""

    async def test_stellt_ampere_statt_watt(self, hass: HomeAssistant, dienste: dict) -> None:
        """8000 W Überschuss, dreiphasig: 11 A ergeben 7590 W, 12 A wären 8280.

        Geschrieben wird die Stromstufe — die Entscheidung fällt in Watt, die
        Stellgröße bleibt in der Sprache des Geräts.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Energy Manager",
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            options={CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 60},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Wallbox",
                    unique_id=None,
                    data={
                        CONF_NAME: "Wallbox",
                        CONF_SWITCH_ENTITY: "switch.wallbox",
                        CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
                        CONF_CONTROL_ENTITY: "number.ladestrom",
                        CONF_PHASES: 3,
                    },
                )
            ],
        )
        hass.states.async_set("sensor.netz", "-8000", {"unit_of_measurement": "W"})
        hass.states.async_set(
            "number.ladestrom",
            "6",
            {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"},
        )
        hass.states.async_set("switch.wallbox", STATE_OFF)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Erst jetzt attrappieren: Beim Einrichten hat HA die number-Plattform
        # der Integration geladen und dabei den echten set_value-Dienst
        # registriert. Vorher gesetzt, wäre die Attrappe überschrieben.
        gestellt = async_mock_service(hass, "number", "set_value")

        await arm(hass, entry)

        assert [call.data["value"] for call in gestellt] == [11]
        assert [call.data["entity_id"] for call in dienste["on"]] == ["switch.wallbox"]


class TestOhneRaster:
    async def test_regelbar_ohne_steuerentitaet_ist_nicht_verfuegbar(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Ohne Raster ist nicht zu entscheiden, welche Stufe anzufordern wäre.

        Geraten wird hier nicht — der Verbraucher gilt als nicht verfügbar, und
        die Automatik lässt ihn in Ruhe.
        """
        entry = make_entry(
            regelbar("Heizstab", "switch.heizstab", "select.fehlt"),
        )
        await setup(hass, entry)
        await arm(hass, entry)

        assert view(entry, "Heizstab").status.value == "unavailable"
        assert not dienste["on"]


class TestAttribute:
    async def test_stufenangaben_am_status_sensor(self, hass: HomeAssistant, dienste: dict) -> None:
        entry = make_entry(regelbar("Heizstab", "switch.heizstab", "select.heizstab"))
        await setup(hass, entry, switches={"switch.heizstab": STATE_ON}, option="mittel")
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Heizstab"
        )

        assert status.attributes["consumer_type"] == "modulating"
        assert status.attributes["control_entity"] == "select.heizstab"
        assert status.attributes["level_source"] == "select"
        assert status.attributes["level_count"] == 3
        assert status.attributes["min_level_w"] == 1000
        assert status.attributes["max_level_w"] == 3000
        assert status.attributes["level_w"] == 2000
        assert status.attributes["level_index"] == 2
        # Der Bedarf ist die kleinste Stufe, und das steht auch dran.
        assert status.attributes["required_w"] == 1000
        assert status.attributes["required_source"] == "ladder"

    async def test_schaltbarer_verbraucher_hat_keine_stufenangaben(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Weggelassen statt None: An ihrem Vorhandensein erkennt die Karte,
        dass es etwas zu regeln gibt."""
        entry = make_entry(schaltbar("Pumpe", "switch.pumpe", **{CONF_MAX_POWER: 800}))
        await setup(hass, entry, switches={"switch.pumpe": STATE_OFF})
        await entry.runtime_data.async_request_refresh_now()
        await hass.async_block_till_done()

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Pumpe"
        )

        assert "level_source" not in status.attributes
        assert "setpoint_w" not in status.attributes
