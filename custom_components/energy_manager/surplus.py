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
class BatteryReading:
    """Ein Speicher, wie ihn die Bilanz sieht: ein Messwert und seine Regel."""

    reading: Reading
    mode: str
    """``charge_only`` oder ``full`` — je Speicher, nicht je Anlage.

    Zwei Speicher können verschieden angeschlossen sein: einer am
    Hausanschluss, einer hinter einem eigenen Zähler. Dann geht ihre
    Entladeleistung auch verschieden in die Bilanz ein.
    """

    @property
    def correction_w(self) -> float | None:
        """Was dieser Speicher zum Überschuss beiträgt.

        ``None``, wenn er gerade keinen Wert liefert — das ist etwas anderes
        als 0 und macht das Ergebnis unsicher, statt es zu verfälschen.
        """
        if self.reading.w is None:
            return None
        if self.mode == BATTERY_MODE_FULL:
            return self.reading.w
        return max(self.reading.w, 0.0)


@dataclass(frozen=True, slots=True)
class SurplusInput:
    """Eingangsgrößen, bereits invertiert und zusammengesetzt."""

    mode: str
    grid: Reading
    production: Reading
    consumption: Reading

    batteries: tuple[BatteryReading, ...] = ()
    """Alle eingerichteten Speicher. Leer heißt: keiner."""

    consumption_includes_battery: bool = False
    battery_reserve_w: float = 0.0


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
    for speicher in data.batteries:
        beitrag = speicher.correction_w
        if beitrag is None:
            # Ein einziger ausgefallener Sensor genügt: Was fehlt, fehlt in der
            # Summe, und der Rest ist dadurch nicht verlässlicher geworden.
            degraded = True
        else:
            battery_correction += beitrag

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
    battery_w = _battery_sum(data.batteries)

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

    available = apply_reserve(raw, data.battery_reserve_w)

    return SurplusResult(
        raw=round_w(raw),
        available=None if available is None else round_w(available),
        battery_correction=round_w(battery_correction),
        grid_w=grid_w,
        battery_w=battery_w,
        degraded=degraded,
        errors=tuple(errors),
    )


def _battery_sum(batteries: tuple[BatteryReading, ...]) -> float | None:
    """Die gemessene Leistung aller Speicher zusammen, für die Anzeige.

    ``None``, solange keiner einen Wert liefert: Eine 0 sähe aus, als stünde
    die Batterie still.
    """
    werte = [b.reading.w for b in batteries if b.reading.w is not None]
    return round_w(sum(werte)) if werte else None


def apply_reserve(raw_w: float | None, battery_reserve_w: float) -> float | None:
    """Zieht die den Speichern vorbehaltene Leistung ab.

    Bewusst getrennt von :func:`compute_surplus`, damit die Reserve **nach** der
    Glättung greift: geglättet wird der Rohwert.

    Die Ladevorranggrenze stand früher ebenfalls hier und deckelte den
    ausgewiesenen Überschuss auf 0. Sie ist in die Reihenfolge gewandert, wo ein
    Speicher unter seiner Grenze nach vorn rückt und fordert. Zwei Gründe: Bei
    mehreren Speichern wüsste eine globale Sperre nicht, was sie tun soll — ist
    einer darunter und der andere darüber, sperrt sie dann alles? Und sie log
    der Anzeige einen Überschuss von 0 vor, während die Sonne schien; jetzt
    steht dort, was wirklich da ist, und wer es bekommt.

    Kein Clamping auf ≥ 0 — negative Werte bedeuten ein Defizit und werden für
    die Ampel gebraucht.
    """
    if raw_w is None:
        return None

    return raw_w - (battery_reserve_w or 0.0)


def _reason_to_error(reason: ReadingReason | None, source: str) -> SurplusError:
    """Ordnet einem unbrauchbaren Messwert den passenden Fehler zu."""
    if reason is ReadingReason.WRONG_UNIT:
        return SurplusError.WRONG_UNIT
    if reason is ReadingReason.MISSING:
        return SurplusError(f"missing_{source}")
    return SurplusError(f"{source}_unavailable")
