"""Überschussberechnung.

Die Fälle entsprechen ``test/surplus.test.ts`` der Karte. Weicht ein Ergebnis
ab, rechnen Anzeige und Automatik verschieden — das ist ein Fehler, keine
zulässige Abweichung.
"""

from __future__ import annotations

import pytest

from custom_components.energy_manager.const import (
    BATTERY_MODE_CHARGE_ONLY,
    BATTERY_MODE_FULL,
    METER_MODE_GRID,
    METER_MODE_SPLIT,
)
from custom_components.energy_manager.models import Reading, ReadingReason, SurplusError
from custom_components.energy_manager.surplus import (
    SurplusInput,
    apply_reserve,
    compute_surplus,
)

NONE = Reading(w=None, reason=ReadingReason.MISSING)


def make_input(**overrides) -> SurplusInput:
    defaults = {
        "mode": METER_MODE_GRID,
        "grid": NONE,
        "production": NONE,
        "consumption": NONE,
        "battery": NONE,
        "battery_configured": False,
        "battery_mode": BATTERY_MODE_CHARGE_ONLY,
        "battery_soc": None,
        "consumption_includes_battery": False,
        "battery_min_soc": None,
        "battery_reserve_w": 0.0,
    }
    return SurplusInput(**{**defaults, **overrides})


class TestGridMode:
    def test_einspeisung_ergibt_positiven_ueberschuss(self) -> None:
        # -2000 W am Netz bedeutet 2000 W Einspeisung.
        result = compute_surplus(make_input(grid=Reading(w=-2000)))
        assert result.raw == 2000
        assert result.available == 2000
        assert result.errors == ()

    def test_netzbezug_ergibt_negativen_ueberschuss(self) -> None:
        assert compute_surplus(make_input(grid=Reading(w=800))).available == -800

    def test_batterieladung_zaehlt_als_umlenkbar(self) -> None:
        # Netz ausgeglichen, aber 1500 W gehen in die Batterie — die könnte
        # stattdessen ein Verbraucher bekommen.
        result = compute_surplus(
            make_input(grid=Reading(w=0), battery=Reading(w=1500), battery_configured=True)
        )
        assert result.raw == 1500
        assert result.battery_correction == 1500
        assert result.degraded is False

    def test_entladung_wird_im_modus_charge_only_ignoriert(self) -> None:
        result = compute_surplus(
            make_input(grid=Reading(w=0), battery=Reading(w=-1000), battery_configured=True)
        )
        assert result.raw == 0
        assert result.battery_correction == 0

    def test_entladung_wird_im_modus_full_abgezogen(self) -> None:
        result = compute_surplus(
            make_input(
                grid=Reading(w=0),
                battery=Reading(w=-1000),
                battery_configured=True,
                battery_mode=BATTERY_MODE_FULL,
            )
        )
        assert result.raw == -1000

    def test_ladung_zaehlt_in_beiden_modi_gleich(self) -> None:
        base = {"grid": Reading(w=0), "battery": Reading(w=1500), "battery_configured": True}
        assert compute_surplus(make_input(**base)).raw == 1500
        assert compute_surplus(make_input(**base, battery_mode=BATTERY_MODE_FULL)).raw == 1500

    def test_realer_anlagenfall(self) -> None:
        """7 W Netzbezug bei 386 W Entladung.

        Der Modus ``full`` meldete hier -393 W und beschriftete das als
        Netzbezug — ein Wert, der dem Zähler klar widerspricht. Genau dieser
        Fall führte zur Einführung von ``charge_only`` als Vorgabe.
        """
        messwerte = {
            "grid": Reading(w=7),
            "battery": Reading(w=-386),
            "battery_configured": True,
            "battery_soc": 84.0,
        }

        charge_only = compute_surplus(make_input(**messwerte))
        assert charge_only.available == -7
        # Die Rohwerte bleiben unabhängig vom Modus erhalten.
        assert charge_only.grid_w == 7
        assert charge_only.battery_w == -386

        voll = compute_surplus(make_input(**messwerte, battery_mode=BATTERY_MODE_FULL))
        assert voll.available == -393

    def test_ausgefallener_batteriesensor_macht_unsicher(self) -> None:
        result = compute_surplus(
            make_input(
                grid=Reading(w=-2000),
                battery=Reading(w=None, reason=ReadingReason.UNAVAILABLE),
                battery_configured=True,
            )
        )
        assert result.degraded is True
        assert result.battery_correction == 0
        # Der Netzwert allein bleibt verwertbar, wird aber als unsicher geführt.
        assert result.raw == 2000

    def test_fehlender_netzsensor_ist_ein_fehler(self) -> None:
        result = compute_surplus(make_input(grid=Reading(w=None, reason=ReadingReason.MISSING)))
        assert result.raw is None
        assert result.available is None
        assert SurplusError.MISSING_GRID in result.errors
        assert result.usable is False

    @pytest.mark.parametrize(
        ("reason", "erwartet"),
        [
            (ReadingReason.UNAVAILABLE, SurplusError.GRID_UNAVAILABLE),
            (ReadingReason.WRONG_UNIT, SurplusError.WRONG_UNIT),
            (ReadingReason.NAN, SurplusError.GRID_UNAVAILABLE),
        ],
    )
    def test_fehlerarten_werden_unterschieden(self, reason, erwartet) -> None:
        result = compute_surplus(make_input(grid=Reading(w=None, reason=reason)))
        assert erwartet in result.errors


class TestSplitMode:
    def test_erzeugung_minus_verbrauch(self) -> None:
        result = compute_surplus(
            make_input(
                mode=METER_MODE_SPLIT,
                production=Reading(w=5000),
                consumption=Reading(w=1800),
            )
        )
        assert result.raw == 3200

    def test_liefert_dasselbe_wie_der_grid_modus(self) -> None:
        """Quervalidierung: 5000 W PV, 1800 W Haus ⇒ 3200 W Einspeisung."""
        via_grid = compute_surplus(make_input(grid=Reading(w=-3200)))
        via_split = compute_surplus(
            make_input(
                mode=METER_MODE_SPLIT,
                production=Reading(w=5000),
                consumption=Reading(w=1800),
            )
        )
        assert via_split.raw == via_grid.raw

    def test_batterie_nur_wenn_der_verbrauch_sie_mitzaehlt(self) -> None:
        base = {
            "mode": METER_MODE_SPLIT,
            "production": Reading(w=5000),
            "consumption": Reading(w=3000),
            "battery": Reading(w=1200),
            "battery_configured": True,
        }
        assert compute_surplus(make_input(**base)).raw == 2000
        assert compute_surplus(make_input(**base, consumption_includes_battery=True)).raw == 3200

    def test_beide_fehlenden_sensoren_werden_gemeldet(self) -> None:
        result = compute_surplus(make_input(mode=METER_MODE_SPLIT))
        assert SurplusError.MISSING_PRODUCTION in result.errors
        assert SurplusError.MISSING_CONSUMPTION in result.errors
        assert result.raw is None

    def test_ein_fehlender_sensor_genuegt_zum_abbruch(self) -> None:
        result = compute_surplus(make_input(mode=METER_MODE_SPLIT, production=Reading(w=5000)))
        assert result.raw is None
        assert result.errors == (SurplusError.MISSING_CONSUMPTION,)


class TestApplyReserve:
    def test_zieht_die_reserve_ab(self) -> None:
        assert apply_reserve(2000, None, None, 500) == 1500

    def test_sperrt_unterhalb_der_ladestandsgrenze(self) -> None:
        assert apply_reserve(2000, 15, 20, 0) == 0

    def test_gibt_ab_der_grenze_wieder_frei(self) -> None:
        assert apply_reserve(2000, 20, 20, 0) == 2000
        assert apply_reserve(2000, 55, 20, 0) == 2000

    def test_verschlechtert_ein_defizit_nicht(self) -> None:
        # min(-800, 0) = -800: der Netzbezug bleibt sichtbar.
        assert apply_reserve(-800, 10, 20, 0) == -800

    def test_klemmt_negative_werte_nicht_weg(self) -> None:
        assert apply_reserve(200, None, None, 500) == -300

    def test_reicht_none_durch(self) -> None:
        assert apply_reserve(None, 50, 20, 100) is None

    def test_ohne_grenze_keine_sperre(self) -> None:
        assert apply_reserve(2000, 5, None, 0) == 2000


def test_reserve_und_ladestandsregel_greifen_am_ende() -> None:
    result = compute_surplus(
        make_input(
            grid=Reading(w=-3000),
            battery=Reading(w=0),
            battery_configured=True,
            battery_soc=10.0,
            battery_min_soc=30.0,
            battery_reserve_w=500.0,
        )
    )
    assert result.raw == 3000
    assert result.available == 0
