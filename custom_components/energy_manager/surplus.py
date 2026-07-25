"""Überschussberechnung.

Portierung von ``src/lib/surplus.ts`` der Karte. Jede Abweichung würde
bedeuten, dass die Karte etwas anderes anzeigt, als die Automatik tut.

Bilanz am Netzverknüpfungspunkt, alles in Watt, ``G`` = Netzleistung
(>0 Bezug), ``B`` = Batterieleistung (>0 Laden)::

    G = C_haus + B - P_pv     ⟹     S_roh = P_pv - C_haus = B - G
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import BATTERY_MODE_FULL, METER_MODE_GRID
from .models import Reading, ReadingReason, SurplusError, SurplusResult
from .units import round_w


@dataclass(frozen=True, slots=True)
class SurplusInput:
    """Eingangsgrößen, bereits invertiert und zusammengesetzt."""

    mode: str
    grid: Reading
    production: Reading
    consumption: Reading
    battery: Reading
    battery_configured: bool
    battery_mode: str
    battery_soc: float | None
    consumption_includes_battery: bool
    battery_min_soc: float | None
    battery_reserve_w: float


def compute_surplus(data: SurplusInput) -> SurplusResult:
    """Berechnet den Überschuss.

    ``B_eff`` hängt am Batteriemodus:

    - ``charge_only`` (Vorgabe): ``max(B, 0)``. Ladeleistung ist umlenkbar und
      erhöht den Überschuss; eine Entladung wird ignoriert. Ohne diese
      Begrenzung meldet die Anzeige ein Defizit in Höhe der Entladeleistung,
      obwohl der Zähler nahezu Null zeigt — das liest sich wie ein Rechenfehler.
    - ``full``: ``B``. Entladung wird abgezogen, das Ergebnis ist der reine
      PV-Überschuss.
    """
    errors: list[SurplusError] = []

    # Ist eine Batterie konfiguriert, liefert aber gerade keinen Wert, wird mit
    # 0 weitergerechnet UND das Ergebnis als unsicher markiert. Ein kurz
    # ausgefallener Sensor darf keinen falschen Überschuss als gesichert
    # ausweisen.
    battery_correction = 0.0
    degraded = False
    if data.battery_configured:
        if data.battery.w is None:
            degraded = True
        elif data.battery_mode == BATTERY_MODE_FULL:
            battery_correction = data.battery.w
        else:
            battery_correction = max(data.battery.w, 0.0)

    raw: float | None = None

    if data.mode == METER_MODE_GRID:
        if data.grid.w is None:
            errors.append(_reason_to_error(data.grid.reason, "grid"))
        else:
            raw = -data.grid.w + battery_correction
    else:
        if data.production.w is None:
            errors.append(_reason_to_error(data.production.reason, "production"))
        if data.consumption.w is None:
            errors.append(_reason_to_error(data.consumption.reason, "consumption"))

        if data.production.w is not None and data.consumption.w is not None:
            raw = data.production.w - data.consumption.w
            if data.consumption_includes_battery:
                raw += battery_correction

    # Rohmesswerte mitgeben, damit die Anzeige den tatsächlichen Zählerstand
    # neben den berechneten Überschuss stellen kann. Ohne das liest sich ein
    # Defizit wie Netzbezug in gleicher Höhe.
    grid_w = None if data.grid.w is None else round_w(data.grid.w)
    battery_w = None if data.battery.w is None else round_w(data.battery.w)

    if raw is None:
        return SurplusResult(
            raw=None,
            available=None,
            battery_correction=battery_correction,
            grid_w=grid_w,
            battery_w=battery_w,
            degraded=degraded,
            errors=tuple(errors),
        )

    available = apply_reserve(raw, data.battery_soc, data.battery_min_soc, data.battery_reserve_w)

    return SurplusResult(
        raw=round_w(raw),
        available=None if available is None else round_w(available),
        battery_correction=round_w(battery_correction),
        grid_w=grid_w,
        battery_w=battery_w,
        degraded=degraded,
        errors=tuple(errors),
    )


def apply_reserve(
    raw_w: float | None,
    battery_soc: float | None,
    battery_min_soc: float | None,
    battery_reserve_w: float,
) -> float | None:
    """Zieht die der Batterie vorbehaltene Leistung ab.

    Bewusst getrennt von :func:`compute_surplus`, damit die Reserve **nach** der
    Glättung greifen kann: geglättet wird der Rohwert, sonst liefe die
    Ladevorrangregel um das Mittelungsfenster verzögert nach.

    Kein Clamping auf ≥ 0 — negative Werte bedeuten ein Defizit und werden für
    die Ampel gebraucht.
    """
    if raw_w is None:
        return None

    available = raw_w - (battery_reserve_w or 0.0)

    # Unterhalb der Ladestandsgrenze hat das Laden der Batterie Vorrang: es wird
    # kein Überschuss mehr an Verbraucher ausgewiesen.
    if battery_soc is not None and battery_min_soc is not None and battery_soc < battery_min_soc:
        available = min(available, 0.0)

    return available


def _reason_to_error(reason: ReadingReason | None, source: str) -> SurplusError:
    """Ordnet einem unbrauchbaren Messwert den passenden Fehler zu."""
    if reason is ReadingReason.WRONG_UNIT:
        return SurplusError.WRONG_UNIT
    if reason is ReadingReason.MISSING:
        return SurplusError(f"missing_{source}")
    return SurplusError(f"{source}_unavailable")
