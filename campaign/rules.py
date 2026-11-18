"""Regelkontroll före export. Ett klipp med problem ska inte postas."""

from __future__ import annotations

from . import audio
from .caption import check_caption
from .profile import Profile

# Varningen gäller bara tills formatet är valt. Webben visar den inte för ett klipp som har ett format.
NO_FORMAT = "Klippet har inget format ännu. Välj ett när du godkänner."


def check_source(info: dict) -> list[str]:
    """Kontroll av klippet från clipsmith innan vi lägger tid på att göra det klart."""
    if (info["width"], info["height"]) != (1080, 1920):
        return [
            f"Klippet är {info['width']}×{info['height']}, inte 1080×1920. "
            "Sätt reframe.mode: stacked (eller center) i config."
        ]
    return []


def _sound(info: dict, manifest: dict) -> list[str]:
    """Ett Twitch-klipp måste ha ljud. En egen video (manifestets own_video, från finish_clip) får vara utan."""
    if info["has_audio"] or manifest.get("own_video"):
        return []
    return ["Klippet saknar ljud. Streamens ljud måste höras."]


def _sound_rules(manifest: dict, profile: Profile) -> list[str]:
    """Musik med originalljudet på 0 %: en varning när kampanjen har en regel om ljud. Robin avgör."""
    silent = manifest.get("audio") == "music" and manifest.get("original_volume") == 0
    found = audio.rules(profile) if silent else []
    if not found:
        return []
    said = " ".join(r if r.endswith((".", "!", "?")) else f"{r}." for r in found)
    return [f"Originalljudet hörs inte. Kampanjen har en regel om ljud: {said}"]


def check_clip(
    info: dict,
    manifest: dict,
    profile: Profile,
    caption: str,
    clip_format: str | None = None,
    review: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Returnerar (problem, varningar). Problem stoppar klippet, varningar gör det inte.

    `review` är svaret från bildgranskningen (review.py), om den har körts.
    """
    problems: list[str] = list(check_source(info))
    warnings: list[str] = []

    length = f"{info['duration']:.1f}".replace(".", ",")  # svenskt talformat: 4,6 s
    if info["duration"] < profile.min_seconds:
        low = f"{profile.min_seconds:g}".replace(".", ",")
        problems.append(f"Klippet är {length} s. Kampanjen kräver minst {low} s.")
    if info["duration"] > profile.max_seconds:
        high = f"{profile.max_seconds:g}".replace(".", ",")
        problems.append(f"Klippet är {length} s. Profilens tak är {high} s.")
    problems += _sound(info, manifest)
    if profile.text_required and not manifest.get("text_applied"):
        problems.append("Texten på skärmen saknas.")
    if profile.overlay_required and not manifest.get("overlay_applied"):
        problems.append("Overlayen saknas. Lägg bilden i overlays/ och kör finish igen.")
    problems += check_caption(caption, profile)
    warnings += _sound_rules(manifest, profile)

    if profile.formats:
        if clip_format is None:
            warnings.append(NO_FORMAT)
        elif clip_format not in profile.format_ids:
            problems.append(f"Formatet {clip_format!r} finns inte i kampanjens lista.")
    if review is None:
        if profile.facecam_required:
            warnings.append("Ingen bildgranskning gjord. Kontrollera själv att facecam syns.")
    else:
        if profile.facecam_required and not review.get("facecam_visible", False):
            problems.append("Bildgranskningen hittade ingen facecam i klippet.")
        if profile.text_required and not review.get("text_visible", False):
            problems.append("Bildgranskningen kunde inte läsa texten på skärmen.")
        if review.get("face_covered", False):
            problems.append("Bildgranskningen såg text över ansiktet.")
        # Claudes beskrivningar (notes, och problems i äldre granskningar) är inga avvikelser. De sparas i
        # granskningen och visas för sig, så att varningarna bara är sådant du ska göra något åt.
    return problems, warnings
