"""Ny version av ett godkänt klipp för ett annat konto: annan font, klipp i början, egen overlay och klistermärke.

Byggs aldrig: spegling, ändrad hastighet eller tonhöjd, ljusstyrka eller färgfilter, brus, små beskärningar,
ändrad metadata eller annat vars syfte är att plattformarna inte ska känna igen klippet.
"""

from __future__ import annotations

import math
from pathlib import Path

from .finish import HEIGHT, WIDTH
from .profile import Profile

POSITIONS = ("top_left", "top_right", "center", "bottom_left", "bottom_right")
MARGIN = 40  # från sidan
STICKER_W = 320  # klistermärkets bredd i bilden; höjden följer bilden


class VersionError(Exception):
    """Fel med svensk text och HTTP-status för webben."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def sticker_path(root: Path, file: object) -> Path:
    """En .png i stickers/. Ingen sökväg utanför mappen."""
    folder = (root / "stickers").resolve()
    target = (folder / str(file)).resolve()
    if not (isinstance(file, str) and target.is_relative_to(folder) and target.suffix.lower() == ".png"
            and target.is_file()):
        raise VersionError(f"Klistermärket {file} finns inte. Välj en .png i stickers/.")
    return target


def sticker_box(position: str, width: int, height: int, profile: Profile) -> tuple[int, int]:
    """Var klistermärket hamnar: precis utanför kampanjens säkra zoner, MARGIN från sidan. Ryms det inte
    mellan zonerna blir det 400."""
    if position not in POSITIONS:
        raise VersionError(f"Platsen {position!r} finns inte. Välj {', '.join(POSITIONS)}.")
    top, bottom = math.ceil(HEIGHT * profile.safe_top), math.floor(HEIGHT * (1 - profile.safe_bottom))
    x = {"left": MARGIN, "right": WIDTH - width - MARGIN}.get(position.split("_")[-1], (WIDTH - width) // 2)
    y = {"top": top, "bottom": bottom - height}.get(position.split("_")[0], (HEIGHT - height) // 2)
    if y < top or y + height > bottom:
        raise VersionError("Klistermärket går in i kampanjens säkra zon, där plattformens knappar och text ligger. "
                           "Välj ett mindre klistermärke eller en annan plats.")
    return x, y
