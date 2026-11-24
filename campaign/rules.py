"""Regelkontroll före export. Ett klipp med problem ska inte postas."""

from __future__ import annotations

from . import audio
from .caption import check_caption
from .profile import Profile

# Varningen gäller bara tills formatet är valt. Webben visar den inte för ett klipp som har ett format.
NO_FORMAT = "The clip has no format yet. Choose one when you approve."
# Samma varning i klipp från före översättningen. Den finns kvar i klipp.db och döljs på samma sätt.
OLD_NO_FORMAT = "Klippet har inget format ännu. Välj ett när du godkänner."
# Den dolda kampanjen för liveklipp utan kampanj (profiles/_ingen.yaml).
NO_CAMPAIGN = "_ingen"
NO_CAMPAIGN_PROBLEM = "The clip has no campaign. Choose a campaign first."
# Början på för kort-felet. drafts.with_advice lägger till ett råd efter det.
TOO_SHORT = "The campaign requires at least"


def check_source(info: dict) -> list[str]:
    """Kontroll av klippet från clipsmith innan vi lägger tid på att göra det klart."""
    if (info["width"], info["height"]) != (1080, 1920):
        return [
            f"The clip is {info['width']}×{info['height']}, not 1080×1920. "
            "Set reframe.mode: stacked (or center) in the config."
        ]
    return []


def check_campaign(profile: Profile) -> list[str]:
    """Ett klipp utan kampanj kan bara nekas: det har alltid ett problem tills en kampanj är vald."""
    return [NO_CAMPAIGN_PROBLEM] if profile.name == NO_CAMPAIGN else []


def _sound(info: dict, manifest: dict) -> list[str]:
    """Ett Twitch-klipp måste ha ljud. En egen video (manifestets own_video, från finish_clip) får vara utan."""
    if info["has_audio"] or manifest.get("own_video"):
        return []
    return ["The clip has no sound. The stream's sound must be heard."]


def _sound_rules(manifest: dict, profile: Profile) -> list[str]:
    """Musik med originalljudet på 0 %: en varning när kampanjen har en regel om ljud. Robin avgör."""
    silent = manifest.get("audio") == "music" and manifest.get("original_volume") == 0
    found = audio.rules(profile) if silent else []
    if not found:
        return []
    said = " ".join(r if r.endswith((".", "!", "?")) else f"{r}." for r in found)
    return [f"The original sound cannot be heard. The campaign has a rule about sound: {said}"]


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
    problems += check_campaign(profile)

    length = f"{info['duration']:.1f}"
    if info["duration"] < profile.min_seconds:
        problems.append(f"The clip is {length} s. {TOO_SHORT} {profile.min_seconds:g} s.")
    if info["duration"] > profile.max_seconds:
        problems.append(f"The clip is {length} s. The campaign allows at most {profile.max_seconds:g} s.")
    problems += _sound(info, manifest)
    if profile.text_required and not manifest.get("text_applied"):
        problems.append("The on-screen text is missing.")
    if profile.overlay_required and not manifest.get("overlay_applied"):
        problems.append("The overlay is missing. Put the image in overlays/ and run finish again.")
    problems += check_caption(caption, profile)
    warnings += _sound_rules(manifest, profile)

    if profile.formats:
        if clip_format is None:
            warnings.append(NO_FORMAT)
        elif clip_format not in profile.format_ids:
            problems.append(f"The format {clip_format!r} is not in the campaign's list.")
    if review is None:
        if profile.facecam_required:
            warnings.append("No image review was done. Check yourself that the facecam is visible.")
    else:
        if profile.facecam_required and not review.get("facecam_visible", False):
            problems.append("The image review found no facecam in the clip.")
        if profile.text_required and not review.get("text_visible", False):
            problems.append("The image review could not read the on-screen text.")
        if review.get("face_covered", False):
            problems.append("The image review saw text over the face.")
        # Claudes beskrivningar (notes, och problems i äldre granskningar) är inga avvikelser. De sparas i
        # granskningen och visas för sig, så att varningarna bara är sådant du ska göra något åt.
    return problems, warnings
