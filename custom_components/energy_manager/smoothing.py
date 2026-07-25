"""Zeitgewichteter gleitender Mittelwert.

Portierung von ``src/lib/smoothing.ts`` der Karte.

Bewusst nicht arithmetisch: Zustandsänderungen kommen unregelmäßig. Ein Sensor,
der 55 s auf 3000 W und 5 s auf 0 W steht, muss ~2750 W ergeben und nicht 1500 W.
Jeder Messwert gilt so lange, bis der nächste eintrifft.
"""

from __future__ import annotations

from dataclasses import dataclass

# Obergrenze gegen flatternde Sensoren.
MAX_SAMPLES = 600


@dataclass(slots=True)
class _Sample:
    t: float
    """Zeitpunkt in Sekunden."""

    v: float | None


class TimeWeightedWindow:
    """Gleitendes Fenster mit Gewichtung nach Dauer."""

    def __init__(self, window_s: float) -> None:
        self._window = window_s
        self._buf: list[_Sample] = []

    @property
    def window_s(self) -> float:
        return self._window

    def set_window(self, window_s: float) -> None:
        """Ändert die Fensterbreite und verwirft dabei den Puffer."""
        if window_s == self._window:
            return
        self._window = window_s
        self.reset()

    def reset(self) -> None:
        self._buf.clear()

    def push(self, value: float | None, now: float) -> None:
        """Nimmt einen Messwert auf."""
        # Fenster 0 = Glättung aus: nur den letzten Wert vorhalten.
        if self._window <= 0:
            self._buf = [_Sample(now, value)]
            return

        # Ein Zeitsprung rückwärts (Uhrumstellung, Testzeit) würde die
        # Gewichtung verfälschen — dann lieber neu beginnen.
        if self._buf and now < self._buf[-1].t:
            self._buf = [_Sample(now, value)]
            return

        self._buf.append(_Sample(now, value))

        if len(self._buf) > MAX_SAMPLES:
            del self._buf[: len(self._buf) - MAX_SAMPLES]

    def value(self, now: float) -> float | None:
        """Zeitgewichteter Mittelwert, oder None ohne gültige Daten."""
        if not self._buf:
            return None
        if self._window <= 0:
            return self._buf[-1].v

        self._prune(now)

        start_of_window = now - self._window
        acc = 0.0
        dur = 0.0

        for i, sample in enumerate(self._buf):
            start = max(sample.t, start_of_window)
            # Das letzte Segment reicht bis jetzt: der Wert gilt weiter, bis ein
            # neuer eintrifft.
            end = self._buf[i + 1].t if i + 1 < len(self._buf) else now
            if end <= start:
                continue
            if sample.v is None:
                # Lücken zählen nicht zur Gewichtung — ein ausgefallener Sensor
                # ist kein Messwert von 0 W.
                continue

            acc += sample.v * (end - start)
            dur += end - start

        return acc / dur if dur > 0 else None

    def coverage(self, now: float) -> float:
        """Anteil des Fensters, der von gültigen Daten abgedeckt ist (0..1)."""
        if self._window <= 0:
            return 1.0 if self._buf and self._buf[0].v is not None else 0.0
        if not self._buf:
            return 0.0

        self._prune(now)

        start_of_window = now - self._window
        dur = 0.0

        for i, sample in enumerate(self._buf):
            if sample.v is None:
                continue
            start = max(sample.t, start_of_window)
            end = self._buf[i + 1].t if i + 1 < len(self._buf) else now
            if end > start:
                dur += end - start

        return min(1.0, dur / self._window)

    def _prune(self, now: float) -> None:
        """Verwirft alles vor dem Fenster — bis auf das jüngste Sample davor.

        Dieses "Carry-in" trägt seinen Wert in das Fenster hinein: es galt ja
        bis zum nächsten Sample weiter. Ohne die Ausnahme hätte ein seit Minuten
        konstanter Sensor gar keinen Wert im Fenster.
        """
        start_of_window = now - self._window
        last_outside = -1
        for i, sample in enumerate(self._buf):
            if sample.t <= start_of_window:
                last_outside = i
            else:
                break
        if last_outside > 0:
            del self._buf[:last_outside]
