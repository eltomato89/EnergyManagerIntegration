"""Zeitgewichtete Glättung. Fälle wie in ``test/smoothing.test.ts`` der Karte."""

from __future__ import annotations

from custom_components.energy_manager.smoothing import MAX_SAMPLES, TimeWeightedWindow

T0 = 1_000_000.0


def test_gewichtet_nach_dauer_nicht_nach_anzahl() -> None:
    """Der Kernfall: 55 s auf 3000 W, dann 5 s auf 0 W.

    Arithmetisch wären das 1500 W — richtig sind 2750 W.
    """
    w = TimeWeightedWindow(60)
    w.push(3000, T0)
    w.push(0, T0 + 55)

    assert w.value(T0 + 60) == 2750


def test_letzter_wert_gilt_bis_jetzt() -> None:
    w = TimeWeightedWindow(60)
    w.push(1000, T0)
    assert w.value(T0 + 30) == 1000


def test_carry_in_traegt_den_wert_ins_fenster() -> None:
    """Seit fünf Minuten unverändert — ohne Carry-in wäre das Fenster leer."""
    w = TimeWeightedWindow(60)
    w.push(2200, T0)
    assert w.value(T0 + 300) == 2200


def test_alte_werte_fallen_heraus() -> None:
    w = TimeWeightedWindow(60)
    w.push(0, T0)
    w.push(1000, T0 + 100)

    # T0+130: die letzten 60 s bestehen je zur Hälfte aus 0 W und 1000 W.
    assert w.value(T0 + 130) == 500
    # T0+160: der 0-Abschnitt ist vollständig aus dem Fenster gewandert.
    assert w.value(T0 + 160) == 1000


def test_luecken_zaehlen_nicht_als_null() -> None:
    w = TimeWeightedWindow(60)
    w.push(2000, T0)
    w.push(None, T0 + 30)  # Sensor fällt aus
    assert w.value(T0 + 60) == 2000


def test_ohne_gueltigen_wert_none() -> None:
    w = TimeWeightedWindow(60)
    assert w.value(T0) is None
    w.push(None, T0)
    assert w.value(T0 + 10) is None


def test_abdeckung() -> None:
    w = TimeWeightedWindow(60)
    w.push(1000, T0)
    assert w.coverage(T0 + 30) == 0.5
    assert w.coverage(T0 + 60) == 1.0
    # Nie über 1, auch wenn länger nichts passiert.
    assert w.coverage(T0 + 120) == 1.0


def test_abdeckung_ohne_daten() -> None:
    assert TimeWeightedWindow(60).coverage(T0) == 0.0


def test_fenster_null_nimmt_den_letzten_wert() -> None:
    w = TimeWeightedWindow(0)
    w.push(1000, T0)
    w.push(250, T0 + 1)
    assert w.value(T0 + 2) == 250
    assert w.coverage(T0 + 2) == 1.0


def test_puffer_ist_begrenzt() -> None:
    w = TimeWeightedWindow(600)
    for i in range(MAX_SAMPLES + 250):
        w.push(i, T0 + i * 0.01)
    assert w.value(T0 + (MAX_SAMPLES + 250) * 0.01) is not None


def test_zeitsprung_rueckwaerts_verwirft_den_puffer() -> None:
    w = TimeWeightedWindow(60)
    w.push(3000, T0)
    w.push(500, T0 - 10)
    assert w.value(T0 - 5) == 500


def test_fensteraenderung_setzt_zurueck() -> None:
    w = TimeWeightedWindow(60)
    w.push(3000, T0)
    w.set_window(120)
    assert w.value(T0 + 1) is None


def test_gleiche_fensterbreite_behaelt_den_puffer() -> None:
    w = TimeWeightedWindow(60)
    w.push(3000, T0)
    w.set_window(60)
    assert w.value(T0 + 1) == 3000


def test_reset_leert() -> None:
    w = TimeWeightedWindow(60)
    w.push(3000, T0)
    w.reset()
    assert w.value(T0 + 1) is None
