"""VOD-filen och delarna i work/<id>/: hur stora de är, och Ta bort för att frigöra plats på disken.
Klippen i out/, transkriptet, chatten, valen och klipploggen rörs inte."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import Settings


class UnsafeVod(ValueError):
    """VOD-id:t är ingen mapp direkt i work/, till exempel '..' eller 'a/b'."""


def folder(vod_id: str, settings: Settings) -> Path:
    """work/<id>/. Kastar UnsafeVod när mappen hamnar någon annanstans, också via en länk."""
    path = settings.work_dir / vod_id
    if not vod_id or path.resolve().parent != settings.work_dir.resolve():
        raise UnsafeVod(f"VOD-id:t {vod_id!r} går inte att använda som mapp i work/.")
    return path


def files(vod_id: str, settings: Settings) -> list[Path]:
    """Det som Ta bort tar bort: VOD-filen, en halv hopslagning av delarna och delarna från twitch-dl."""
    base = folder(vod_id, settings)
    wanted = (base / f"{vod_id}.mp4", base / f"{vod_id}.del.mp4", base / "delar")
    return [path for path in wanted if path.is_symlink() or path.exists()]


def _bytes(path: Path) -> int:
    """Storleken utan att följa länkar: en länk till SSD:n räknas inte, och tas bara bort som länk."""
    if path.is_symlink() or not path.is_dir():
        return path.lstat().st_size
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_symlink() or p.is_file())


def size(vod_id: str, settings: Settings) -> int | None:
    """Byte som Ta bort frigör. None när inget finns att ta bort."""
    try:
        return sum(_bytes(path) for path in files(vod_id, settings)) or None
    except UnsafeVod:
        return None


def remove(vod_id: str, settings: Settings) -> int:
    """Ta bort VOD-filen och delarna. Returnerar hur många byte som frigjordes."""
    freed = 0
    for path in files(vod_id, settings):
        freed += _bytes(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return freed
