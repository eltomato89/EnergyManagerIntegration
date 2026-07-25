"""Erzeugt die Brand-Assets der Integration.

Ein Sonnensymbol über einem Schieberegler: Überschuss, der auf Verbraucher
verteilt wird. Bewusst schlicht — es wird meist in 48 px angezeigt.
"""

from __future__ import annotations

import math
import pathlib
import sys

from PIL import Image, ImageDraw

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

SIZE = 512
SOLAR = (255, 152, 0, 255)  # HAs --energy-solar-color
DARK = (56, 63, 71, 255)
LIGHT = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / SIZE  # Skalierungsfaktor

    # Abgerundeter Hintergrund
    d.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)],
        radius=int(110 * s),
        fill=DARK,
    )

    # Sonne
    cx, cy, r = size / 2, size * 0.36, size * 0.13
    d.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=SOLAR)

    # Strahlen
    for i in range(8):
        angle = math.radians(i * 45)
        inner, outer = r * 1.45, r * 2.05
        d.line(
            [
                (cx + math.cos(angle) * inner, cy + math.sin(angle) * inner),
                (cx + math.cos(angle) * outer, cy + math.sin(angle) * outer),
            ],
            fill=SOLAR,
            width=max(2, int(16 * s)),
        )

    # Drei Balken als priorisierte Verbraucher, absteigend gefüllt
    bar_h = size * 0.055
    gap = size * 0.045
    left = size * 0.22
    full = size * 0.56
    top = size * 0.60

    for i, anteil in enumerate((1.0, 0.66, 0.33)):
        y = top + i * (bar_h + gap)
        # Spur
        d.rounded_rectangle(
            [(left, y), (left + full, y + bar_h)],
            radius=bar_h / 2,
            fill=(255, 255, 255, 45),
        )
        # Füllung
        d.rounded_rectangle(
            [(left, y), (left + full * anteil, y + bar_h)],
            radius=bar_h / 2,
            fill=SOLAR if i == 0 else (*SOLAR[:3], 200 - i * 60),
        )

    return img


base = draw(SIZE)
base.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
base.save(OUT / "icon@2x.png")

# Logo: dasselbe Motiv, HA erwartet es in 256 px Höhe.
base.resize((256, 256), Image.LANCZOS).save(OUT / "logo.png")
base.save(OUT / "logo@2x.png")

print("geschrieben:", ", ".join(sorted(p.name for p in OUT.iterdir())))
