"""Nennleistung aus der Statistik schätzen.

Ohne eingetragene Nennleistung rechnete die Automatik mit einem festen
Vorgabewert. Der ist fast immer falsch — eine Wärmepumpe zieht ein Vielfaches,
eine Umwälzpumpe einen Bruchteil. Beides führt zu Fehlschaltungen.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_POWER,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DEFAULT_REQUIRED_W,
    DOMAIN,
    METER_MODE_GRID,
    STANDBY_W,
    SUBENTRY_TYPE_CONSUMER,
)
from custom_components.energy_manager.engine import required_source, resolve_required_w
from custom_components.energy_manager.estimate import async_estimate_power
from custom_components.energy_manager.models import ConsumerConfig

PFAD = "custom_components.energy_manager.coordinator.async_estimate_power"


def config(**kwargs) -> ConsumerConfig:
    return ConsumerConfig(subentry_id="x", name="Test", switch_entity="switch.x", **kwargs)


class TestReihenfolgeDerQuellen:
    """Die Schätzung darf nur einspringen, wo nichts Besseres da ist."""

    def test_eingetragene_werte_schlagen_die_schaetzung(self) -> None:
        assert resolve_required_w(config(min_power=800), None, 1840) == 800
        assert resolve_required_w(config(max_power=900), None, 1840) == 900

    def test_ein_laufendes_geraet_zaehlt_was_es_zieht(self) -> None:
        # Dann ist die Frage ohnehin beantwortet.
        assert resolve_required_w(config(), 400, 1840) == 400

    def test_geschaetzt_greift_bei_ausgeschaltetem_geraet(self) -> None:
        """Der eigentliche Zielfall: aus, nichts eingetragen.

        Ohne Schätzung stünde hier der Vorgabewert — und die Automatik
        entschiede auf einer Zahl, die mit dem Gerät nichts zu tun hat.
        """
        assert resolve_required_w(config(), 0, 1840) == 1840
        assert resolve_required_w(config(), None, 1840) == 1840

    def test_ohne_alles_bleibt_der_vorgabewert(self) -> None:
        assert resolve_required_w(config(), None, None) == DEFAULT_REQUIRED_W

    def test_die_herkunft_ist_ablesbar(self) -> None:
        # Ohne diese Angabe ist ein geratener Wert nicht von einem
        # eingetragenen zu unterscheiden.
        assert required_source(config(min_power=800), None, 1840) == "min_power"
        assert required_source(config(max_power=900), None, 1840) == "max_power"
        assert required_source(config(), 400, 1840) == "measured"
        assert required_source(config(), 0, 1840) == "estimated"
        assert required_source(config(), None, None) == "default"


class TestBereitschaftsbetrieb:
    """Ein laufender Schalter heißt nicht, dass das Gerät arbeitet.

    Ein Luftentfeuchter mit Hygrostat, eine Waschmaschine nach dem Programm,
    ein Klimagerät ohne Kühlbedarf: Der Schalter ist an, das Gerät zieht ein
    paar Watt und tut nichts.
    """

    def test_standby_gilt_nicht_als_bedarf(self) -> None:
        """Der gefährliche Fall.

        Ohne diese Grenze hielte die Automatik das Gerät für mit 2 W
        zuschaltbar. Es liefe an, zöge seine echten 300 W — und das Ergebnis
        wäre Netzbezug.
        """
        assert resolve_required_w(config(), 2, None) == DEFAULT_REQUIRED_W
        assert required_source(config(), 2, None) == "default"

    def test_statt_standby_gilt_die_schaetzung(self) -> None:
        assert resolve_required_w(config(), 2, 1840) == 1840
        assert required_source(config(), 2, 1840) == "estimated"

    def test_ein_echter_betriebswert_zaehlt_weiter(self) -> None:
        assert resolve_required_w(config(), 300, 1840) == 300
        assert required_source(config(), 300, 1840) == "measured"

    def test_die_grenze_selbst_zaehlt_als_betrieb(self) -> None:
        assert resolve_required_w(config(), STANDBY_W, None) == STANDBY_W


def consumer_subentry(name: str, **extra) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_type=SUBENTRY_TYPE_CONSUMER,
        title=name,
        unique_id=None,
        data={
            CONF_NAME: name,
            CONF_SWITCH_ENTITY: f"switch.{name.lower()}",
            CONF_POWER_ENTITY: f"sensor.{name.lower()}_leistung",
            **extra,
        },
    )


async def aufbau(hass: HomeAssistant, *consumers: ConfigSubentryData) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
        options={CONF_SMOOTHING_WINDOW: 0},
        unique_id=DOMAIN,
        subentries_data=list(consumers),
    )
    hass.states.async_set("sensor.netz", "-3000", {"unit_of_measurement": "W"})
    for sub in consumers:
        name = sub["data"][CONF_NAME].lower()
        hass.states.async_set(f"switch.{name}", STATE_OFF)
        hass.states.async_set(f"sensor.{name}_leistung", "0", {"unit_of_measurement": "W"})

    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


class TestVerdrahtung:
    async def test_geschaetzter_wert_landet_im_bedarf(self, hass: HomeAssistant) -> None:
        with patch(PFAD, return_value=1840.0):
            entry = await aufbau(hass, consumer_subentry("Waermepumpe"))
            await entry.runtime_data.async_update_estimates()
            await entry.runtime_data.async_request_refresh_now()
            await hass.async_block_till_done()

        status = hass.states.get("sensor.waermepumpe_status")
        assert status.attributes["required_w"] == 1840.0
        assert status.attributes["required_source"] == "estimated"

    async def test_ohne_statistik_bleibt_der_vorgabewert(self, hass: HomeAssistant) -> None:
        """Kein Recorder, kein state_class, nie gelaufen — alles derselbe Fall."""
        with patch(PFAD, return_value=None):
            entry = await aufbau(hass, consumer_subentry("Waermepumpe"))
            await entry.runtime_data.async_update_estimates()
            await entry.runtime_data.async_request_refresh_now()
            await hass.async_block_till_done()

        status = hass.states.get("sensor.waermepumpe_status")
        assert status.attributes["required_w"] == DEFAULT_REQUIRED_W
        assert status.attributes["required_source"] == "default"

    async def test_eingetragene_leistung_wird_nicht_ueberstimmt(self, hass: HomeAssistant) -> None:
        """Wer sie angegeben hat, soll nicht von einer Schätzung überstimmt werden."""
        with patch(PFAD, return_value=1840.0) as schaetzer:
            entry = await aufbau(hass, consumer_subentry("Waermepumpe", **{CONF_MAX_POWER: 900}))
            await entry.runtime_data.async_update_estimates()
            await entry.runtime_data.async_request_refresh_now()
            await hass.async_block_till_done()

        # Gar nicht erst abgefragt: Die Datenbank soll nicht ohne Grund arbeiten.
        schaetzer.assert_not_called()
        status = hass.states.get("sensor.waermepumpe_status")
        assert status.attributes["required_w"] == 900
        assert status.attributes["required_source"] == "max_power"

    async def test_ohne_leistungssensor_gibt_es_nichts_zu_schaetzen(
        self, hass: HomeAssistant
    ) -> None:
        sub = ConfigSubentryData(
            subentry_type=SUBENTRY_TYPE_CONSUMER,
            title="Pumpe",
            unique_id=None,
            data={CONF_NAME: "Pumpe", CONF_SWITCH_ENTITY: "switch.pumpe"},
        )
        with patch(PFAD, return_value=1840.0) as schaetzer:
            entry = await aufbau(hass, sub)
            await entry.runtime_data.async_update_estimates()

        schaetzer.assert_not_called()
        assert entry.runtime_data.estimated_power(next(iter(entry.runtime_data.consumers))) is None

    async def test_nachgetragene_leistung_verwirft_die_schaetzung(
        self, hass: HomeAssistant
    ) -> None:
        """Sonst bliebe ein alter Schätzwert neben dem eingetragenen liegen."""
        with patch(PFAD, return_value=1840.0):
            entry = await aufbau(hass, consumer_subentry("Waermepumpe"))
            coordinator = entry.runtime_data
            await coordinator.async_update_estimates()
            subentry_id = next(iter(coordinator.consumers))
            assert coordinator.estimated_power(subentry_id) == 1840.0

            # Jetzt trägt der Nutzer die Nennleistung nach.
            subentry = next(iter(entry.subentries.values()))
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={**subentry.data, CONF_MIN_POWER: 1200},
            )
            await hass.async_block_till_done()
            await entry.runtime_data.async_update_estimates()

        assert entry.runtime_data.estimated_power(subentry_id) is None


class TestAbfrage:
    """Das Modul selbst — was es aus einem Statistikergebnis macht.

    ``get_instance`` wird mitgemockt: Ohne geladenen Recorder wirft es, und die
    Abfangklausel machte aus jedem Test einen Test des Fehlerpfads. Genau das
    war hier zunächst der Fall — drei Prüfungen bestanden aus dem falschen
    Grund.
    """

    @staticmethod
    @contextmanager
    def statistik(hass: HomeAssistant, ergebnis=None, fehler=None):
        """Stellt den Recorder so, als läge dieses Ergebnis vor."""

        # SimpleNamespace statt einer Klasse: Als Klassenattribut griffe das
        # Descriptor-Protokoll und schöbe self als erstes Argument unter.
        instanz = SimpleNamespace(async_add_executor_job=hass.async_add_executor_job)

        with (
            patch(
                "homeassistant.components.recorder.get_instance",
                return_value=instanz,
            ),
            patch(
                "homeassistant.components.recorder.statistics.statistic_during_period",
                return_value=ergebnis,
                side_effect=fehler,
            ),
        ):
            yield

    async def test_ein_betriebswert_wird_uebernommen(self, hass: HomeAssistant) -> None:
        with self.statistik(hass, {"max": 1839.6}):
            assert await async_estimate_power(hass, "sensor.x") == 1840

    async def test_standby_gilt_nicht_als_nennleistung(self, hass: HomeAssistant) -> None:
        """Ein Gerät, das nur im Bereitschaftsbetrieb lief, ist keine Grundlage.

        Den Wert zu übernehmen wäre schlimmer als der Vorgabewert: Die Automatik
        hielte das Gerät für beliebig zuschaltbar.
        """
        with self.statistik(hass, {"max": 3.0}):
            assert await async_estimate_power(hass, "sensor.x") is None

    async def test_ohne_aufzeichnung_kommt_nichts(self, hass: HomeAssistant) -> None:
        with self.statistik(hass, {}):
            assert await async_estimate_power(hass, "sensor.x") is None

    async def test_eine_kaputte_statistik_blockiert_nichts(self, hass: HomeAssistant) -> None:
        with self.statistik(hass, fehler=RuntimeError("keine Datenbank")):
            assert await async_estimate_power(hass, "sensor.x") is None

    async def test_watt_werden_erzwungen(self, hass: HomeAssistant) -> None:
        """Ein Sensor in kW laege sonst um den Faktor 1000 daneben —
        und das fiele erst beim Schalten auf."""

        instanz = SimpleNamespace(async_add_executor_job=hass.async_add_executor_job)

        with (
            patch("homeassistant.components.recorder.get_instance", return_value=instanz),
            patch(
                "homeassistant.components.recorder.statistics.statistic_during_period",
                return_value={"max": 1840.0},
            ) as abfrage,
        ):
            await async_estimate_power(hass, "sensor.x")

        assert abfrage.call_args.kwargs["units"] == {"power": "W"}
        assert abfrage.call_args.kwargs["types"] == {"max"}


class TestApiVertraeglichkeit:
    """Passt der Aufruf noch zur Recorder-API?

    Ein echter Recorder-Test scheidet aus: ``recorder_mock`` verlangt, dass das
    Modul noch nicht importiert wurde — die gemockten Tests oben tun genau das.
    Die Signatur zu prüfen fängt aber den Fall, auf den es ankommt: dass Home
    Assistant die Funktion ändert und die Schätzung still ausfällt.
    """

    def test_argumente_passen_zur_signatur(self) -> None:
        import inspect

        from homeassistant.components.recorder import statistics

        signatur = inspect.signature(statistics.statistic_during_period)
        # Genau die Argumente, die estimate.py übergibt.
        signatur.bind(
            None,  # hass
            None,  # start_time
            None,  # end_time
            "sensor.x",
            types={"max"},
            units={"power": "W"},
        )

    def test_get_instance_bietet_den_executor(self) -> None:
        from homeassistant.components.recorder import get_instance

        assert callable(get_instance)
