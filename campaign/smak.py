"""Smakfilen: vad som gör klipp i en stil bra. Profilens style väljer smak/<style>.md."""

from __future__ import annotations

import re
from pathlib import Path

STYLE_NAME = re.compile(r"[a-z0-9_-]+")


def check_style(style: str) -> str:
    """Stilens namn, eller ett fel som nämner filen om namnet inte är ett enkelt namn."""
    if style and not STYLE_NAME.fullmatch(style):
        raise ValueError(
            f"style måste vara ett enkelt namn (a-z, 0-9, _ och -), inte en sökväg: smak/{style}.md"
        )
    return style


def load_taste(style: str, root: Path) -> str:
    """Smakfilens text utan markdown-rubriker. Tom sträng när style är tom."""
    if not check_style(style):
        return ""
    path = Path(root) / "smak" / f"{style}.md"
    if not path.is_file():
        raise ValueError(
            f"Smakfilen saknas: smak/{style}.md. Skapa filen eller ändra style i kampanjen."
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#")).strip()
