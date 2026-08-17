"""Die Stufenwahl — von Hand gerechnet.

Diese Rechnung ist die riskanteste Stelle der Stufenregelung, und zwar nicht
weil sie kompliziert wäre, sondern weil ein Fehler darin nicht auffällt: Kein
Absturz, kein Logeintrag, nur gelegentlich falsch geschaltete Verbraucher weiter
unten in der Rangfolge.

Deshalb stehen hier die Zahlen ausgeschrieben statt aus dem Code hergeleitet.
Eine Prüfung, die dieselbe Formel benutzt wie die Sache, die sie prüft, prüft
nichts.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.energy_manager.const import CONSUMER_TYPE_MODULATING
from custom_components.energy_manager.engine import choose_level, reachable_w
from custom_components.energy_manager.ladder import Ladder, Level, build_ladder, read_level
from custom_components.energy_manager.models import ConsumerConfig

# Ein Heizstab mit drei Stufen. Runde Zahlen, damit die Rechnung im Kopf geht.
HEIZSTAB = Ladder(
    levels=(
        Level(w=1000.0, command="niedrig"),
        Level(w=2000.0, command="mittel"),
        Level(w=3000.0, command="hoch"),
    ),
    source="select",
)


class TestErreichbareLeistung:
    """Die Umformulierung der bestehenden Regel, nicht ihre Änderung."""

    def test_ausgeschaltet_zaehlt_nur_das_budget(self) -> None:
        assert reachable_w(2500, is_currently_on=False, power_w=None, current=None) == 2500

    def test_laufend_kommt_der_eigene_verbrauch_hinzu(self) -> None:
        """Der Ist-Verbrauch steckt im Messwert, das Budget ist der Kopfraum.

        Läuft der Heizstab auf 1000 W und sind 800 W frei, kann er insgesamt
        1800 W ziehen — nicht 800.
        """
        erreichbar = reachable_w(800, is_currently_on=True, power_w=1000, current=None)
        assert erreichbar == 1800

    def test_negatives_budget_ergibt_eine_niedrigere_stufe(self) -> None:
        """Dieselbe Formel, andere Richtung: 3000 W laufen, 1200 W Defizit."""
        erreichbar = reachable_w(-1200, is_currently_on=True, power_w=3000, current=None)
        assert erreichbar == 1800

    def test_ohne_leistungssensor_gilt_die_gestellte_stufe(self) -> None:
        """Die eigene Anforderung ist die beste vorliegende Schätzung."""
        erreichbar = reachable_w(
            500, is_currently_on=True, power_w=None, current=HEIZSTAB.levels[1]
        )
        assert erreichbar == 2500

    def test_ohne_sensor_und_ohne_stufe_bleibt_das_budget(self) -> None:
        assert reachable_w(500, is_currently_on=True, power_w=None, current=None) == 500


class TestStufenwahl:
    def test_hoechste_passende_stufe(self) -> None:
        assert choose_level(HEIZSTAB, 2500, None, 0) is HEIZSTAB.levels[1]

    def test_genau_auf_der_stufe(self) -> None:
        assert choose_level(HEIZSTAB, 2000, None, 0) is HEIZSTAB.levels[1]

    def test_ueber_dem_maximum_bleibt_das_maximum(self) -> None:
        assert choose_level(HEIZSTAB, 99999, None, 0) is HEIZSTAB.levels[2]

    def test_unter_der_kleinsten_stufe_keine(self) -> None:
        """Dann ist zu schalten, nicht zu drosseln."""
        assert choose_level(HEIZSTAB, 900, None, 0) is None

    def test_negativ_keine(self) -> None:
        assert choose_level(HEIZSTAB, -500, HEIZSTAB.levels[2], 0) is None


class TestHysterese:
    """Das Totband hält die Leiter an einer Stufengrenze ruhig."""

    def test_kleiner_wechsel_unterbleibt(self) -> None:
        """Von 2000 auf 1000 sind 1000 W — bei 1200 W Totband zu wenig."""
        gewaehlt = choose_level(HEIZSTAB, 1900, HEIZSTAB.levels[1], hysteresis=1200)
        assert gewaehlt is HEIZSTAB.levels[1]

    def test_grosser_wechsel_geschieht(self) -> None:
        gewaehlt = choose_level(HEIZSTAB, 1900, HEIZSTAB.levels[1], hysteresis=500)
        assert gewaehlt is HEIZSTAB.levels[0]

    def test_ohne_aktuelle_stufe_greift_sie_nicht(self) -> None:
        """Beim Einschalten gibt es nichts zu halten."""
        gewaehlt = choose_level(HEIZSTAB, 1900, None, hysteresis=1200)
        assert gewaehlt is HEIZSTAB.levels[0]

    def test_das_abschalten_wird_nicht_gebremst(self) -> None:
        """Ein Defizit zu beenden ist dringender als eine Stufe zu halten."""
        gewaehlt = choose_level(HEIZSTAB, 500, HEIZSTAB.levels[2], hysteresis=5000)
        assert gewaehlt is None

    def test_gleiche_stufe_bleibt_gleich(self) -> None:
        gewaehlt = choose_level(HEIZSTAB, 2000, HEIZSTAB.levels[1], hysteresis=100)
        assert gewaehlt is HEIZSTAB.levels[1]


class TestGelesenerStand:
    """Gelesen wird die Stellgröße, nicht der Leistungssensor."""

    def _wallbox(self) -> ConsumerConfig:
        return ConsumerConfig(
            subentry_id="x",
            name="Wallbox",
            switch_entity="switch.wallbox",
            consumer_type=CONSUMER_TYPE_MODULATING,
            control_entity="number.ladestrom",
            phases=3,
        )

    async def test_number(self, hass: HomeAssistant) -> None:
        hass.states.async_set(
            "number.ladestrom", "10", {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"}
        )
        consumer = self._wallbox()
        leiter = build_ladder(hass, consumer)
        assert leiter is not None

        stufe = read_level(hass, consumer, leiter)
        assert stufe is not None
        # 10 A · 3 · 230 V
        assert stufe.w == 6900

    async def test_number_zwischen_zwei_stufen(self, hass: HomeAssistant) -> None:
        """Nach einem Eingriff von Hand liegt der Wert nicht auf dem Raster."""
        hass.states.async_set(
            "number.ladestrom",
            "10.4",
            {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"},
        )
        consumer = self._wallbox()
        leiter = build_ladder(hass, consumer)
        assert leiter is not None

        stufe = read_level(hass, consumer, leiter)
        assert stufe is not None
        assert stufe.command == 10

    async def test_select(self, hass: HomeAssistant) -> None:
        hass.states.async_set(
            "select.heizstab", "mittel", {"options": ["aus", "niedrig", "mittel", "hoch"]}
        )
        consumer = ConsumerConfig(
            subentry_id="x",
            name="Heizstab",
            switch_entity="switch.heizstab",
            consumer_type=CONSUMER_TYPE_MODULATING,
            control_entity="select.heizstab",
            level_map={"aus": 0, "niedrig": 1400, "mittel": 2400, "hoch": 3600},
        )
        leiter = build_ladder(hass, consumer)
        assert leiter is not None

        stufe = read_level(hass, consumer, leiter)
        assert stufe is not None
        assert stufe.w == 2400

    async def test_select_in_der_aus_stellung(self, hass: HomeAssistant) -> None:
        """„aus" ist keine Stufe — sie steht nicht auf der Leiter."""
        hass.states.async_set(
            "select.heizstab", "aus", {"options": ["aus", "niedrig", "mittel", "hoch"]}
        )
        consumer = ConsumerConfig(
            subentry_id="x",
            name="Heizstab",
            switch_entity="switch.heizstab",
            consumer_type=CONSUMER_TYPE_MODULATING,
            control_entity="select.heizstab",
            level_map={"aus": 0, "niedrig": 1400, "mittel": 2400, "hoch": 3600},
        )
        leiter = build_ladder(hass, consumer)
        assert leiter is not None

        assert read_level(hass, consumer, leiter) is None

    async def test_unlesbarer_zustand(self, hass: HomeAssistant) -> None:
        hass.states.async_set(
            "number.ladestrom",
            "unavailable",
            {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"},
        )
        consumer = self._wallbox()
        leiter = Ladder(levels=(Level(w=4140.0, command=6.0),), source="number_a")

        assert read_level(hass, consumer, leiter) is None


class TestZusammenspiel:
    """Die beiden Funktionen hintereinander, wie die Engine sie benutzt."""

    def test_laufender_verbraucher_steigt_mit_dem_ueberschuss(self) -> None:
        """1000 W laufen, 1300 W frei: erreichbar 2300 → Stufe 2000."""
        erreichbar = reachable_w(1300, is_currently_on=True, power_w=1000, current=None)
        assert choose_level(HEIZSTAB, erreichbar, HEIZSTAB.levels[0], 0) is HEIZSTAB.levels[1]

    def test_laufender_verbraucher_faellt_mit_dem_defizit(self) -> None:
        """3000 W laufen, 900 W Defizit: erreichbar 2100 → Stufe 2000."""
        erreichbar = reachable_w(-900, is_currently_on=True, power_w=3000, current=None)
        assert choose_level(HEIZSTAB, erreichbar, HEIZSTAB.levels[2], 0) is HEIZSTAB.levels[1]

    def test_laufender_verbraucher_faellt_ganz_heraus(self) -> None:
        """1000 W laufen, 500 W Defizit: erreichbar 500 → keine Stufe passt."""
        erreichbar = reachable_w(-500, is_currently_on=True, power_w=1000, current=None)
        assert choose_level(HEIZSTAB, erreichbar, HEIZSTAB.levels[0], 0) is None

    def test_ausgeschalteter_verbraucher_startet_auf_der_passenden_stufe(self) -> None:
        """Nicht auf der kleinsten: 2500 W frei tragen bereits die mittlere."""
        erreichbar = reachable_w(2500, is_currently_on=False, power_w=None, current=None)
        assert choose_level(HEIZSTAB, erreichbar, None, 0) is HEIZSTAB.levels[1]
