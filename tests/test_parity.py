"""Gleichstand mit der Kartenimplementierung.

Die Referenzwerte in ``fixtures/parity_cases.json`` stammen aus der
TypeScript-Implementierung der Energy Manager Card (``src/lib/surplus.ts`` und
``src/lib/smoothing.ts``). Dieser Test stellt sicher, dass die Portierung für
dieselben Eingaben dieselben Zahlen liefert.

Weicht hier etwas ab, zeigt die Karte etwas anderes an, als die Automatik tut —
und das ist der Fehler, der einem Nutzer am schwersten zu erklären wäre.

Die Datei wird erzeugt von ``test/_gen/gen-cases.test.ts`` im Kartenprojekt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.energy_manager.models import Reading, ReadingReason
from custom_components.energy_manager.smoothing import TimeWeightedWindow
from custom_components.energy_manager.surplus import SurplusInput, compute_surplus

FIXTURE = Path(__file__).parent / "fixtures" / "parity_cases.json"

if not FIXTURE.exists():  # pragma: no cover
    pytest.skip("Referenzwerte fehlen", allow_module_level=True)

_DATA = json.loads(FIXTURE.read_text())


def _reading(raw: dict) -> Reading:
    w = raw.get("w")
    reason = raw.get("reason")
    return Reading(
        w=None if w is None else float(w),
        reason=ReadingReason(reason.replace("-", "_")) if reason else None,
    )


def _to_input(raw: dict) -> SurplusInput:
    return SurplusInput(
        mode=raw["mode"],
        grid=_reading(raw["grid"]),
        production=_reading(raw["production"]),
        consumption=_reading(raw["consumption"]),
        battery=_reading(raw["battery"]),
        battery_configured=raw["batteryConfigured"],
        battery_mode=raw["batteryMode"],
        battery_soc=raw.get("batterySoc"),
        consumption_includes_battery=raw["consumptionIncludesBattery"],
        battery_min_soc=raw.get("batteryMinSoc"),
        battery_reserve_w=raw["batteryReserveW"],
    )


@pytest.mark.parametrize("case", _DATA["surplus"], ids=lambda c: c["name"])
def test_surplus_gleichstand(case: dict) -> None:
    """Jeder Fall muss dieselbe Zahl liefern wie die Karte."""
    result = compute_surplus(_to_input(case["input"]))
    expected = case["expected"]

    assert result.raw == expected["raw"], "raw weicht ab"
    assert result.available == expected["available"], "available weicht ab"
    assert result.battery_correction == expected["batteryCorrection"]
    assert result.grid_w == expected["gridW"]
    assert result.battery_w == expected["batteryW"]
    assert result.degraded == expected["degraded"]

    # Die Fehlerbezeichner unterscheiden sich in der Schreibweise
    # (TypeScript nutzt Bindestriche, Python Unterstriche).
    ours = {e.value for e in result.errors}
    theirs = {e.replace("-", "_") for e in expected["errors"]}
    assert ours == theirs, "Fehlerarten weichen ab"


@pytest.mark.parametrize("case", _DATA["smoothing"], ids=lambda c: c["name"])
def test_smoothing_gleichstand(case: dict) -> None:
    """Die Glättung muss identisch gewichten."""
    window = TimeWeightedWindow(case["window"])
    for offset, value in case["samples"]:
        window.push(value, float(offset))

    result = window.value(float(case["at"]))
    expected = case["expected"]

    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-9)

    assert window.coverage(float(case["at"])) == pytest.approx(case["coverage"], rel=1e-9)
