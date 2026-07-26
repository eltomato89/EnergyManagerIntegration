"""Register und Vollständigkeit der Nutzertexte.

Zustandsnamen wie „reicht nicht" lesen sich wie eine Note statt wie eine
technische Aussage. Diese Prüfungen halten fest, was beim Durchgehen der Texte
als salopp aufgefallen ist — sonst schleicht es beim nächsten Feld wieder ein.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

VERZEICHNIS = Path("custom_components/energy_manager/translations")

# Wendungen, die in einer Oberfläche nichts zu suchen haben.
SALOPP = [
    re.compile(r"reicht nicht", re.I),
    re.compile(r"fast ausreichend", re.I),
    re.compile(r"schaltflut", re.I),
    re.compile(r"\bAmpel\b"),
    re.compile(r"einfach leer lassen", re.I),
    re.compile(r"wirklich schalten", re.I),
    re.compile(r"angefasst", re.I),
    re.compile(r"lohnt sich", re.I),
    re.compile(r"Sonnenlücke", re.I),
]

# In einer Oberfläche wirkt der Infinitiv sachlicher als die Anrede, und Home
# Assistant hält es in seinen eigenen Texten ebenso.
IMPERATIVE = re.compile(r"\b(Trage|Prüfe|Nutze|Setze|Aktiviere|Ergänze|Schau|Wähle)\b")


def texte(sprache: str) -> list[tuple[str, str]]:
    """Alle Zeichenketten der Übersetzung, mit ihrem Pfad."""
    daten = json.loads((VERZEICHNIS / f"{sprache}.json").read_text(encoding="utf-8"))
    gefunden: list[tuple[str, str]] = []

    def gehen(pfad: str, wert: object) -> None:
        if isinstance(wert, str):
            gefunden.append((pfad, wert))
        elif isinstance(wert, dict):
            for schluessel, unterwert in wert.items():
                gehen(f"{pfad}.{schluessel}" if pfad else schluessel, unterwert)

    gehen("", daten)
    return gefunden


def test_keine_umgangssprache() -> None:
    treffer = [(pfad, text) for pfad, text in texte("de") if any(m.search(text) for m in SALOPP)]
    assert treffer == []


def test_keine_du_imperative() -> None:
    treffer = [(pfad, text) for pfad, text in texte("de") if IMPERATIVE.search(text)]
    assert treffer == []


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_kein_gedankenstrich_als_satzersatz(sprache: str) -> None:
    """Ein Halbgedanke nach dem Strich liest sich gesprochen, nicht geschrieben.

    Gemeint ist der Gedankenstrich, der einen Nachsatz anhängt statt einen
    Einschub zu klammern — in Beschreibungen die häufigste Quelle von Plauderton.
    """
    treffer = [
        (pfad, text)
        for pfad, text in texte(sprache)
        # Ein Strich mit Leerzeichen davor UND danach, der nicht wieder
        # geschlossen wird.
        if text.count(" — ") == 1 and not text.rstrip().endswith("—")
    ]
    assert treffer == []


def test_beide_sprachen_deckungsgleich() -> None:
    """Ein Text, der nur in einer Sprache existiert, erscheint sonst als Schlüssel."""
    assert [pfad for pfad, _ in texte("de")] == [pfad for pfad, _ in texte("en")]
