"""Einheitennormalisierung. Fälle wie in ``test/units.test.ts`` der Karte."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.energy_manager.models import Reading, ReadingReason
from custom_components.energy_manager.units import (
    combine_battery,
    invert,
    power_factor,
    read_percent,
    read_power_w,
)


def set_sensor(hass: HomeAssistant, entity_id: str, state, unit: str | None = None) -> None:
    attrs = {"unit_of_measurement": unit} if unit is not None else {}
    hass.states.async_set(entity_id, str(state), attrs)


class TestPowerFactor:
    @pytest.mark.parametrize(
        ("unit", "faktor"),
        [("W", 1), ("kW", 1e3), ("MW", 1e6), ("GW", 1e9), ("mW", 1e-3)],
    )
    def test_erkennt_leistungseinheiten(self, unit, faktor) -> None:
        assert power_factor(unit) == (faktor, False)

    def test_ist_tolerant_gegenueber_schreibweise(self) -> None:
        assert power_factor(" kw ") == (1e3, False)
        assert power_factor("KW") == (1e3, False)
        assert power_factor("w") == (1.0, False)

    @pytest.mark.parametrize("unit", ["kWh", "Wh", "MWh", "%", "A", "V", "°C"])
    def test_meldet_fremdeinheiten_statt_sie_zu_raten(self, unit) -> None:
        factor, wrong = power_factor(unit)
        assert factor is None
        assert wrong is True

    def test_fehlende_einheit_ist_kein_fehler(self) -> None:
        assert power_factor(None) == (None, False)
        assert power_factor("") == (None, False)

    def test_fantasieeinheit_gilt_weder_als_leistung_noch_als_falsch(self) -> None:
        assert power_factor("Blubb") == (None, False)


class TestReadPowerW:
    async def test_rechnet_kw_auf_w_um(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.pv", 3.5, "kW")
        assert read_power_w(hass, "sensor.pv").w == 3500

    async def test_reicht_w_durch(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.pv", -750, "W")
        assert read_power_w(hass, "sensor.pv").w == -750

    async def test_nimmt_ohne_einheit_watt_an_und_markiert_das(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.pv", 900)
        reading = read_power_w(hass, "sensor.pv")
        assert reading.w == 900
        assert reading.assumed_unit is True

    async def test_lehnt_einen_kwh_zaehler_ab(self, hass: HomeAssistant) -> None:
        """Der häufigste Konfigurationsfehler — niemals still als 0 lesen."""
        set_sensor(hass, "sensor.zaehler", 4211.5, "kWh")
        reading = read_power_w(hass, "sensor.zaehler")
        assert reading.w is None
        assert reading.reason is ReadingReason.WRONG_UNIT
        assert reading.unit == "kWh"

    async def test_unterscheidet_unavailable_von_fehlend(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.weg", "unavailable", "W")
        set_sensor(hass, "sensor.neu", "unknown", "W")

        assert read_power_w(hass, "sensor.weg").reason is ReadingReason.UNAVAILABLE
        assert read_power_w(hass, "sensor.neu").reason is ReadingReason.UNAVAILABLE
        assert read_power_w(hass, "sensor.gibtsnicht").reason is ReadingReason.MISSING
        assert read_power_w(hass, None).reason is ReadingReason.MISSING

    async def test_faengt_nicht_numerische_zustaende_ab(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.text", "kaputt", "W")
        reading = read_power_w(hass, "sensor.text")
        assert reading.w is None
        assert reading.reason is ReadingReason.NAN

    async def test_faengt_unendlich_ab(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.inf", "inf", "W")
        assert read_power_w(hass, "sensor.inf").reason is ReadingReason.NAN


class TestReadPercent:
    async def test_klemmt_auf_0_bis_100(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.soc", 62, "%")
        set_sensor(hass, "sensor.zuviel", 140, "%")
        set_sensor(hass, "sensor.zuwenig", -5, "%")

        assert read_percent(hass, "sensor.soc") == 62
        assert read_percent(hass, "sensor.zuviel") == 100
        assert read_percent(hass, "sensor.zuwenig") == 0

    async def test_none_bei_unbrauchbaren_werten(self, hass: HomeAssistant) -> None:
        set_sensor(hass, "sensor.weg", "unavailable")
        assert read_percent(hass, "sensor.weg") is None
        assert read_percent(hass, "sensor.gibtsnicht") is None
        assert read_percent(hass, None) is None


class TestInvert:
    def test_kehrt_nur_bei_gesetztem_flag_um(self) -> None:
        assert invert(Reading(w=500), True).w == -500
        assert invert(Reading(w=500), False).w == 500

    def test_macht_aus_none_keine_null(self) -> None:
        assert invert(Reading(w=None, reason=ReadingReason.MISSING), True).w is None


class TestCombineBattery:
    def test_bildet_laden_minus_entladen(self) -> None:
        assert combine_battery(Reading(w=800), Reading(w=0)).w == 800
        assert combine_battery(Reading(w=0), Reading(w=1200)).w == -1200

    def test_nutzt_den_vorhandenen_sensor(self) -> None:
        assert combine_battery(Reading(w=800), Reading(w=None)).w == 800
        assert combine_battery(Reading(w=None), Reading(w=500)).w == -500

    def test_none_wenn_beide_fehlen(self) -> None:
        result = combine_battery(Reading(w=None, reason=ReadingReason.UNAVAILABLE), Reading(w=None))
        assert result.w is None
        assert result.reason is ReadingReason.UNAVAILABLE
