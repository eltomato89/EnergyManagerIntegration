"""Folgt die Last dem Sollwert?

Der Sollwert anzukommen heißt nicht, dass die Last ihn annimmt. Ein Fahrzeug
lädt mit 10 A, obwohl 16 A angeboten sind; ein anderes ist fertig und nimmt
nichts mehr.

Der Schaden ist nicht auf das Beruhigungsfenster beschränkt, und das ist der
Punkt dieser Datei: Ohne Beobachtung fordert die Automatik **dauerhaft** eine
Stufe an, die nicht erreicht wird, und die Budget-Kaskade legt die Differenz für
einen Verbraucher zurück, der sie nie abruft. Tiefer priorisierte gehen leer aus,
und die Leistung wird eingespeist statt genutzt.
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
    CONF_LEVEL_MAP,
    CONF_MAX_POWER,
    CONF_METER_MODE,
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
from custom_components.energy_manager.engine import cap_ladder
from custom_components.energy_manager.models import Ladder, Level

SETTLE = 60
STUFEN = {"aus": 0, "s1": 1000, "s2": 2000, "s3": 3000, "s4": 4000}
OPTIONEN = ["aus", "s1", "s2", "s3", "s4"]

LEITER = Ladder(
    levels=(
        Level(w=1000.0, command="s1"),
        Level(w=2000.0, command="s2"),
        Level(w=3000.0, command="s3"),
        Level(w=4000.0, command="s4"),
    ),
    source="select",
)


class TestCapLadder:
    """Die reine Rechnung, von Hand nachvollzogen."""

    def test_ohne_beobachtung_bleibt_alles(self) -> None:
        assert cap_ladder(LEITER, None) is LEITER
        assert cap_ladder(LEITER, 0) is LEITER

    def test_eine_stufe_ueber_dem_beobachteten_bleibt_erlaubt(self) -> None:
        """Sonst wäre die Grenze selbsterfüllend.

        Beobachtet 2000: Die Stufen 1000 und 2000 sind erreicht, 3000 bleibt als
        Versuch erlaubt, 4000 fällt weg.
        """
        gekappt = cap_ladder(LEITER, 2000)

        assert [level.w for level in gekappt.levels] == [1000, 2000, 3000]

    def test_zwischen_zwei_stufen(self) -> None:
        """Beobachtet 2400: erreicht sind 1000 und 2000, erlaubt bleibt 3000."""
        assert [level.w for level in cap_ladder(LEITER, 2400).levels] == [1000, 2000, 3000]

    def test_am_maximum_wird_nicht_gekappt(self) -> None:
        assert cap_ladder(LEITER, 4000) is LEITER
        assert cap_ladder(LEITER, 9999) is LEITER

    def test_eine_stufe_unter_dem_maximum_kappt_nicht(self) -> None:
        """3000 erreicht, 4000 als Versuch erlaubt: die ganze Leiter."""
        assert cap_ladder(LEITER, 3000) is LEITER

    def test_unter_der_kleinsten_stufe_bleibt_die_kleinste(self) -> None:
        """Das Gerät erreicht nicht einmal sein Minimum.

        Angeboten wird dann nur noch die kleinste Stufe — mehr anzufordern hätte
        keine Aussicht, und die Reservierung bleibt auf sie begrenzt.
        """
        assert [level.w for level in cap_ladder(LEITER, 400).levels] == [1000]

    def test_die_herkunft_bleibt_erhalten(self) -> None:
        assert cap_ladder(LEITER, 2000).source == "select"


# --- Durch die ganze Kette --------------------------------------------------


def regelbar(**extra) -> ConfigSubentryData:
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


def pumpe() -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title="Pumpe",
        unique_id=None,
        data={
            CONF_NAME: "Pumpe",
            CONF_SWITCH_ENTITY: "switch.pumpe",
            CONF_MAX_POWER: 500,
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
    grid: str,
    option: str,
    leistung: str,
    switches: dict[str, str] | None = None,
) -> None:
    hass.states.async_set("sensor.netz", grid, {"unit_of_measurement": "W"})
    hass.states.async_set("select.heizstab", option, {"options": OPTIONEN})
    hass.states.async_set("sensor.heizstab_leistung", leistung, {"unit_of_measurement": "W"})
    for entity_id, state in (switches or {"switch.heizstab": STATE_ON}).items():
        hass.states.async_set(entity_id, state)

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def evaluate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_request_refresh_now()
    await hass.async_block_till_done()


def view(entry: MockConfigEntry, name: str):
    return next(v for v in entry.runtime_data.data.consumers if v.config.name == name)


class TestBeobachtung:
    async def test_merkt_die_erreichte_leistung(self, hass: HomeAssistant, dienste: dict) -> None:
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-2000", option="s4", leistung="2100")
        await evaluate(hass, entry)

        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.observed_max(subentry_id) == 2100

    async def test_merkt_das_maximum_nicht_den_letzten_wert(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Eine Ladepause würde die Grenze sonst auf null ziehen.

        Danach käme das Fahrzeug nicht mehr hoch, weil eine Stufe, die nicht
        angefordert wird, auch nicht erreicht werden kann.
        """
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-2000", option="s4", leistung="2100")
        await evaluate(hass, entry)

        hass.states.async_set("sensor.heizstab_leistung", "0", {"unit_of_measurement": "W"})
        await evaluate(hass, entry)

        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.observed_max(subentry_id) == 2100

    async def test_nicht_im_beruhigungsfenster(self, hass: HomeAssistant, dienste: dict) -> None:
        """Ein Wert aus der Anlauframpe wäre als Grenze zu niedrig."""
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-2000", option="s4", leistung="500")
        subentry_id = next(iter(entry.runtime_data.consumers))

        # Das Einrichten hat schon ausgewertet, und zwar ohne laufendes
        # Beruhigungsfenster. Für diese Prüfung ist der Ausgangszustand
        # herzustellen: nichts beobachtet, Fenster läuft.
        entry.runtime_data._observed_max.clear()
        entry.runtime_data.runtime_for(subentry_id).settle_until = (
            dt_util.utcnow().timestamp() + SETTLE
        )
        hass.states.async_set("sensor.heizstab_leistung", "3500", {"unit_of_measurement": "W"})

        await evaluate(hass, entry)

        assert entry.runtime_data.observed_max(subentry_id) is None

    async def test_nicht_wenn_das_geraet_aus_ist(self, hass: HomeAssistant, dienste: dict) -> None:
        entry = make_entry(regelbar())
        await setup(
            hass,
            entry,
            grid="-2000",
            option="aus",
            leistung="0",
            switches={"switch.heizstab": STATE_OFF},
        )
        await evaluate(hass, entry)

        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.observed_max(subentry_id) is None

    async def test_ohne_leistungssensor_keine_beobachtung(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Ohne Messwert ist nicht zu erkennen, ob die Last folgt."""
        entry = make_entry(
            ConfigSubentryData(
                subentry_type=SUBENTRY_TYPE_CONSUMER,
                title="Heizstab",
                unique_id=None,
                data={
                    CONF_NAME: "Heizstab",
                    CONF_SWITCH_ENTITY: "switch.heizstab",
                    CONF_CONSUMER_TYPE: CONSUMER_TYPE_MODULATING,
                    CONF_CONTROL_ENTITY: "select.heizstab",
                    CONF_LEVEL_MAP: STUFEN,
                },
            )
        )
        await setup(hass, entry, grid="-2000", option="s4", leistung="2100")
        await evaluate(hass, entry)

        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.observed_max(subentry_id) is None

    async def test_eine_schaltung_setzt_die_beobachtung_zurueck(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Am Kabel hängt beim nächsten Mal vielleicht ein anderes Fahrzeug."""
        entry = make_entry(regelbar())
        # Läuft auf 4000 W angefordert, zieht aber nur 2100 — und der Überschuss
        # ist weg, es wird abgeschaltet.
        await setup(hass, entry, grid="3000", option="s1", leistung="2100")
        await evaluate(hass, entry)
        subentry_id = next(iter(entry.runtime_data.consumers))
        assert entry.runtime_data.observed_max(subentry_id) == 2100

        await entry.runtime_data.async_set_automation(True)
        await hass.async_block_till_done()

        assert dienste["off"]
        assert entry.runtime_data.observed_max(subentry_id) is None


class TestWirkung:
    async def test_die_leiter_wird_beschnitten(self, hass: HomeAssistant, dienste: dict) -> None:
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-5000", option="s4", leistung="2100")
        await evaluate(hass, entry)

        heizstab = view(entry, "Heizstab")
        assert heizstab.level_capped is True
        assert heizstab.observed_max_w == 2100
        # Erreicht 1000 und 2000, 3000 bleibt als Versuch — 4000 fällt weg.
        assert heizstab.ladder is not None
        assert heizstab.ladder.max_w == 3000

    async def test_der_verbraucher_darunter_bekommt_die_leistung(
        self, hass: HomeAssistant, dienste: dict
    ) -> None:
        """Der eigentliche Zweck dieser Stufe.

        1500 W freier Überschuss, der Heizstab erreicht 2100 W. Erreichbar sind
        damit 3600 W.

        **Ohne** Beobachtung fordert er die Stufe 4000 an. Vom Budget gingen
        4000 minus 2100 gemessene, also 1900 W ab; die Pumpe stünde bei -400 W
        und bliebe aus — für Leistung, die der Heizstab nie abruft.

        **Mit** Beobachtung endet seine Leiter bei 3000 W. Es gehen 900 W ab, es
        bleiben 600 W, und die Pumpe mit 500 W Bedarf wird einschaltbereit.
        """
        entry = make_entry(regelbar(), pumpe())
        await setup(
            hass,
            entry,
            grid="-1500",
            option="s1",
            leistung="2100",
            switches={"switch.heizstab": STATE_ON, "switch.pumpe": STATE_OFF},
        )
        await evaluate(hass, entry)

        heizstab = view(entry, "Heizstab")
        assert heizstab.target is not None
        assert heizstab.target.w == 3000
        # 1500 frei plus 2100 gemessen sind 3600 erreichbar, Stufe 3000 passt.
        # Abgezogen wird der Mehrbedarf: 3000 minus 2100 = 900. Bleiben 600.
        assert heizstab.headroom_w == 600
        # Ohne die Kappung stünde hier 4000, es gingen 1900 ab, und die Pumpe
        # sähe -400 W statt 600 — sie bliebe aus.
        assert view(entry, "Pumpe").status.value == "off_ready"

    async def test_ohne_kappung_kein_ausweis(self, hass: HomeAssistant, dienste: dict) -> None:
        """Erreicht das Gerät seine Leiter, ist nichts zu melden."""
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-5000", option="s4", leistung="4000")
        await evaluate(hass, entry)

        heizstab = view(entry, "Heizstab")
        assert heizstab.level_capped is False
        assert heizstab.ladder is not None
        assert heizstab.ladder.max_w == 4000

    async def test_attribute_am_status_sensor(self, hass: HomeAssistant, dienste: dict) -> None:
        """Die Antwort auf „warum geht sie nicht höher"."""
        entry = make_entry(regelbar())
        await setup(hass, entry, grid="-5000", option="s4", leistung="2100")
        await evaluate(hass, entry)

        status = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("consumer_name") == "Heizstab"
        )

        assert status.attributes["observed_max_w"] == 2100
        assert status.attributes["level_capped"] is True
        assert status.attributes["max_level_w"] == 3000
