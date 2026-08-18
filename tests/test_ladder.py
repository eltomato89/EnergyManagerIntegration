"""Das Stufenraster, aus der Steuerentität gelesen.

Der Kern der Zusage „nicht konfigurieren, sondern auslesen". Was Home Assistant
liefert, wird genommen; was es nicht wissen kann — die Übersetzung in Watt —
steht in der Konfiguration.

Die interessanten Fälle sind die, in denen die Entität etwas Unbrauchbares
meldet: eine fehlende Einheit, eine gelogene Schrittweite, ein leeres Raster.
Dort darf nichts angenommen werden, denn ein Fehlgriff schreibt eine Stellgröße
in ein echtes Gerät.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.energy_manager.const import (
    CONSUMER_TYPE_MODULATING,
    CONSUMER_TYPE_SWITCH,
    MAX_LEVELS,
)
from custom_components.energy_manager.ladder import (
    CONTROL_NUMBER_A,
    CONTROL_NUMBER_W,
    CONTROL_SELECT,
    build_ladder,
)
from custom_components.energy_manager.models import ConsumerConfig


def consumer(**extra) -> ConsumerConfig:
    """Ein regelbarer Verbraucher mit Steuerentität."""
    felder = {
        "subentry_id": "x",
        "name": "Wallbox",
        "switch_entity": "switch.wallbox",
        "consumer_type": CONSUMER_TYPE_MODULATING,
        "control_entity": "number.ladestrom",
        **extra,
    }
    return ConsumerConfig(**felder)


def set_number(
    hass: HomeAssistant,
    value: str = "10",
    entity_id: str = "number.ladestrom",
    **attrs,
) -> None:
    basis = {"min": 6, "max": 16, "step": 1, "unit_of_measurement": "A"}
    hass.states.async_set(entity_id, value, {**basis, **attrs})


class TestNumberInAmpere:
    """Der Wallbox-Fall. Die Ampere-Leiter ist gleichmäßig, die Phasen skalieren."""

    async def test_dreiphasig(self, hass: HomeAssistant) -> None:
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3))

        assert leiter is not None
        assert leiter.source == CONTROL_NUMBER_A
        assert leiter.count == 11
        # 6 A · 3 · 230 V = 4140 W, 16 A · 3 · 230 V = 11040 W
        assert leiter.min_w == 4140
        assert leiter.max_w == 11040
        # Die Stellgröße bleibt Ampere — geschrieben wird in der Sprache des Geräts.
        assert leiter.levels[0].command == 6
        assert leiter.levels[-1].command == 16

    async def test_einphasig(self, hass: HomeAssistant) -> None:
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=1))

        assert leiter is not None
        assert leiter.min_w == 1380
        assert leiter.max_w == 3680

    async def test_mindeststufe_streicht_untere_stufen(self, hass: HomeAssistant) -> None:
        """min der Entität ist die Grenze der Box, nicht die des Fahrzeugs."""
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3, min_level_w=5500))

        assert leiter is not None
        # 8 A · 690 = 5520 W ist die erste, die bleibt.
        assert leiter.min_w == 5520
        assert leiter.max_w == 11040

    async def test_mindeststufe_ueber_dem_maximum_ergibt_keine_leiter(
        self, hass: HomeAssistant
    ) -> None:
        """Ein Konfigurationsfehler — und kein Anlass, die Grenze zu übergehen."""
        set_number(hass)
        assert build_ladder(hass, consumer(phases=3, min_level_w=20000)) is None


class TestNumberInWatt:
    async def test_watt_direkt(self, hass: HomeAssistant) -> None:
        set_number(hass, min=500, max=3500, step=500, unit_of_measurement="W")
        leiter = build_ladder(hass, consumer())

        assert leiter is not None
        assert leiter.source == CONTROL_NUMBER_W
        assert leiter.min_w == 500
        assert leiter.max_w == 3500
        assert leiter.count == 7

    async def test_kilowatt_wird_umgerechnet(self, hass: HomeAssistant) -> None:
        """Ein Wechselrichter meldet gern kW, eine Steckdose W."""
        set_number(hass, min=0.5, max=3.5, step=0.5, unit_of_measurement="kW")
        leiter = build_ladder(hass, consumer())

        assert leiter is not None
        assert leiter.min_w == 500
        assert leiter.max_w == 3500
        # Geschrieben wird weiter in kW.
        assert leiter.levels[-1].command == 3.5


class TestUnbrauchbar:
    """Hier darf nichts angenommen werden."""

    async def test_ohne_einheit_keine_leiter(self, hass: HomeAssistant) -> None:
        """Der gefährlichste Fall, deshalb strenger als bei einem Messwert.

        Eine als Watt gelesene Ampere-Entität ergäbe eine Leiter von 6 bis 16 W.
        Die passt in jeden Überschuss — und die Automatik schriebe 16, während
        das Gerät 16 A zieht. Ein stiller Fehler um den Faktor 690.
        """
        hass.states.async_set("number.ladestrom", "10", {"min": 6, "max": 16, "step": 1})
        assert build_ladder(hass, consumer(phases=3)) is None

    async def test_unbekannte_einheit_keine_leiter(self, hass: HomeAssistant) -> None:
        set_number(hass, unit_of_measurement="°C")
        assert build_ladder(hass, consumer()) is None

    async def test_fehlende_entitaet(self, hass: HomeAssistant) -> None:
        assert build_ladder(hass, consumer()) is None

    async def test_unavailable(self, hass: HomeAssistant) -> None:
        hass.states.async_set("number.ladestrom", "unavailable")
        assert build_ladder(hass, consumer()) is None

    async def test_fremde_domain(self, hass: HomeAssistant) -> None:
        hass.states.async_set("input_text.irgendwas", "10")
        assert build_ladder(hass, consumer(control_entity="input_text.irgendwas")) is None

    async def test_schaltbarer_verbraucher_hat_keine_leiter(self, hass: HomeAssistant) -> None:
        set_number(hass)
        assert build_ladder(hass, consumer(consumer_type=CONSUMER_TYPE_SWITCH)) is None

    async def test_ohne_steuerentitaet(self, hass: HomeAssistant) -> None:
        assert build_ladder(hass, consumer(control_entity=None)) is None


class TestRasterWirdAusgeduennt:
    """Manche Integrationen melden eine Schrittweite, die niemand fahren kann."""

    async def test_zu_feines_raster(self, hass: HomeAssistant) -> None:
        """step 0.01 über 6 bis 16 A wären 1001 Stufen.

        Mal Beruhigungszeit bei einer Aktion je Durchlauf dauerte der Weg von
        unten nach oben Stunden.
        """
        set_number(hass, min=6, max=16, step=0.01)
        leiter = build_ladder(hass, consumer(phases=3))

        assert leiter is not None
        assert leiter.count <= MAX_LEVELS

    async def test_das_maximum_bleibt_erhalten(self, hass: HomeAssistant) -> None:
        """Beim Ausdünnen fällt es sonst als erstes heraus — die wichtigste Stufe."""
        set_number(hass, min=6, max=16, step=0.01)
        leiter = build_ladder(hass, consumer(phases=3))

        assert leiter is not None
        assert leiter.max_w == 11040

    async def test_ohne_schrittweite_bleiben_die_enden(self, hass: HomeAssistant) -> None:
        set_number(hass, min=6, max=16, step=0)
        leiter = build_ladder(hass, consumer(phases=3))

        assert leiter is not None
        assert [level.command for level in leiter.levels] == [6, 16]

    async def test_krummes_maximum_wird_ergaenzt(self, hass: HomeAssistant) -> None:
        """6 bis 15,5 in 1er-Schritten endet auf 15 — 15,5 muss dazukommen."""
        set_number(hass, min=6, max=15.5, step=1)
        leiter = build_ladder(hass, consumer(phases=1))

        assert leiter is not None
        assert leiter.levels[-1].command == 15.5


class TestSelect:
    """Eine Auswahlliste hat keine Einheit — die Zuordnung ist der einzige Weg."""

    def _select(self, hass: HomeAssistant, *options: str) -> None:
        hass.states.async_set("select.heizstab", options[0], {"options": list(options)})

    async def test_zuordnung_wird_uebernommen(self, hass: HomeAssistant) -> None:
        self._select(hass, "aus", "niedrig", "mittel", "hoch")
        leiter = build_ladder(
            hass,
            consumer(
                control_entity="select.heizstab",
                level_map={"aus": 0, "niedrig": 1400, "mittel": 2400, "hoch": 3600},
            ),
        )

        assert leiter is not None
        assert leiter.source == CONTROL_SELECT
        # Die Aus-Stellung ist keine Stufe: abgeschaltet wird über switch_entity.
        assert leiter.count == 3
        assert leiter.min_w == 1400
        assert leiter.max_w == 3600
        assert leiter.levels[0].command == "niedrig"

    async def test_unbekannte_option_faellt_heraus(self, hass: HomeAssistant) -> None:
        """Benennt die Integration eine Option um, wird sie nicht geschätzt."""
        self._select(hass, "eco", "boost")
        leiter = build_ladder(
            hass,
            consumer(control_entity="select.heizstab", level_map={"boost": 3600}),
        )

        assert leiter is not None
        assert leiter.count == 1
        assert leiter.levels[0].command == "boost"

    async def test_ohne_zuordnung_keine_leiter(self, hass: HomeAssistant) -> None:
        self._select(hass, "eco", "boost")
        assert build_ladder(hass, consumer(control_entity="select.heizstab")) is None

    async def test_reihenfolge_folgt_der_leistung_nicht_der_liste(
        self, hass: HomeAssistant
    ) -> None:
        """Die Optionsliste ist nicht zwangsläufig aufsteigend sortiert."""
        self._select(hass, "hoch", "niedrig", "mittel")
        leiter = build_ladder(
            hass,
            consumer(
                control_entity="select.heizstab",
                level_map={"hoch": 3600, "niedrig": 1400, "mittel": 2400},
            ),
        )

        assert leiter is not None
        assert [level.w for level in leiter.levels] == [1400, 2400, 3600]


class TestAuswahl:
    """Die beiden Abfragen, auf die die Engine sich stützt."""

    async def test_hoechste_passende_stufe(self, hass: HomeAssistant) -> None:
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3))
        assert leiter is not None

        # 4140, 4830, 5520, ... 11040
        assert leiter.at_or_below(5000).w == 4830
        assert leiter.at_or_below(4140).w == 4140
        assert leiter.at_or_below(99999).w == 11040

    async def test_unter_der_kleinsten_stufe_passt_nichts(self, hass: HomeAssistant) -> None:
        """Nicht drosseln, sondern abschalten — das entscheidet die Engine."""
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3))
        assert leiter is not None

        assert leiter.at_or_below(4000) is None
        assert leiter.at_or_below(0) is None

    async def test_naechstgelegene_stufe(self, hass: HomeAssistant) -> None:
        """Für die Rückrichtung: ein von Hand gestellter Sollwert liegt daneben."""
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3))
        assert leiter is not None

        assert leiter.nearest(5000).w == 4830
        assert leiter.nearest(5400).w == 5520


class TestHoechststufe:
    """Die Steuerentität nimmt mehr entgegen, als das Gerät leisten kann.

    Ein regelbares Netzteil für 50 bis 600 W meldet als ``number`` gern 0 bis
    3000. Ohne Obergrenze verteilen sich die Stufen über den ganzen gemeldeten
    Bereich, und im nutzbaren Teil bleibt ein Bruchteil der Auflösung.
    """

    def _netzteil(self, hass: HomeAssistant) -> None:
        hass.states.async_set(
            "number.ladestrom",
            "0",
            {"min": 0, "max": 3000, "step": 1, "unit_of_measurement": "W"},
        )

    async def test_ohne_grenze_liegen_die_stufen_zu_weit(self, hass: HomeAssistant) -> None:
        self._netzteil(hass)
        leiter = build_ladder(hass, consumer())

        assert leiter is not None
        assert leiter.max_w == 3000
        # Über 3000 W verteilt: die Schritte sind grob.
        assert leiter.levels[1].w - leiter.levels[0].w > 100

    async def test_mit_grenze_liegt_die_aufloesung_im_nutzbaren_bereich(
        self, hass: HomeAssistant
    ) -> None:
        self._netzteil(hass)
        leiter = build_ladder(hass, consumer(max_level_w=600))

        assert leiter is not None
        assert leiter.max_w == 600
        # Dieselbe Stufenzahl, aber über 600 W statt über 3000.
        assert leiter.count <= MAX_LEVELS
        assert leiter.levels[1].w - leiter.levels[0].w <= 30

    async def test_zusammen_mit_der_mindeststufe(self, hass: HomeAssistant) -> None:
        self._netzteil(hass)
        leiter = build_ladder(hass, consumer(min_level_w=50, max_level_w=600))

        assert leiter is not None
        assert leiter.min_w >= 50
        assert leiter.max_w == 600

    async def test_in_ampere_wird_umgerechnet(self, hass: HomeAssistant) -> None:
        """Die Grenze steht in Watt, die Stellgröße ist Ampere."""
        set_number(hass)
        leiter = build_ladder(hass, consumer(phases=3, max_level_w=7000))

        assert leiter is not None
        # 10 A · 690 = 6900 W passt noch, 11 A wären 7590.
        assert leiter.max_w == 6900

    async def test_bei_einer_auswahlliste(self, hass: HomeAssistant) -> None:
        hass.states.async_set(
            "select.heizstab", "aus", {"options": ["aus", "niedrig", "mittel", "hoch"]}
        )
        leiter = build_ladder(
            hass,
            consumer(
                control_entity="select.heizstab",
                level_map={"aus": 0, "niedrig": 1400, "mittel": 2400, "hoch": 3600},
                max_level_w=2500,
            ),
        )

        assert leiter is not None
        assert [level.w for level in leiter.levels] == [1400, 2400]

    async def test_eine_zu_niedrige_grenze_ergibt_keine_leiter(self, hass: HomeAssistant) -> None:
        set_number(hass)
        assert build_ladder(hass, consumer(phases=3, max_level_w=100)) is None
