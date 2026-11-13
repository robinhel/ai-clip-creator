"""Blizzards kampanj: klipp av trailers och gameplay, utan overlay, facecam och text på bilden,
ofta utan tal. Påhittade Claude. Inga riktiga anrop."""

from __future__ import annotations

import subprocess

from campaign.api import _caption_problems
from campaign.caption import build_caption, check_caption
from campaign.profile import load_profile
from campaign.rules import check_clip
from clipsmith.candidates.audio import compute_audio_rms_series
from clipsmith.config.loaders import load_config
from clipsmith.llm.base import ClipPick
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment, Transcript
from clipsmith.selection.selector import select_clips

from .conftest import PAKET, needs_ffmpeg

CLIP = {"width": 1080, "height": 1920, "duration": 20.0, "has_audio": True}
NOTHING_ADDED = {"overlay_applied": False, "text_applied": False}


def _profile():
    return load_profile("blizzard_nordics", PAKET / "profiles")


def test_caption_is_text_first_then_ad_and_blizzardpartner_on_their_own_line() -> None:
    profile = _profile()
    caption = build_caption("The Lich King rises", profile, game="World of Warcraft")
    assert caption == "The Lich King rises @warcraft\n#ad #blizzardpartner #worldofwarcraft"
    assert check_caption(caption, profile) == []
    brief_example = "World of Warcraft: Forever looks beautiful. @warcraft\n#ad #blizzardpartner"
    assert check_caption(brief_example, profile) == []
    three_more = "Stormwind at dawn @warcraft\n#ad #blizzardpartner #worldofwarcraft #warcraft #azeroth"
    assert check_caption(three_more, profile) == []


def test_caption_with_wrong_order_missing_warcraft_or_too_many_hashtags_is_stopped() -> None:
    profile = _profile()
    wrong = {
        "#blizzardpartner before #ad": "The Lich King rises @warcraft\n#blizzardpartner #ad",
        "same line as the text": "The Lich King rises @warcraft #ad #blizzardpartner",
        "before the text": "#ad #blizzardpartner\nThe Lich King rises @warcraft",
        "not the whole tag": "The Lich King rises @warcraft\n#ad #blizzardpartnerx",
        "something between": "The Lich King rises @warcraft\n#ad #wow #blizzardpartner",
    }
    for why, caption in wrong.items():
        assert any("#ad #blizzardpartner" in p for p in check_caption(caption, profile)), why
    problems = check_caption("The Lich King rises\n#ad #blizzardpartner", profile)
    assert any("@warcraft" in p for p in problems)
    four_more = "The Lich King rises @warcraft\n#ad #blizzardpartner #worldofwarcraft #warcraft #wow #mmo"
    assert any("högst 3" in p for p in check_caption(four_more, profile))


def test_editing_the_caption_in_the_app_replaces_the_old_caption_problems() -> None:
    profile = _profile()
    good = "Stormwind at dawn @warcraft\n#ad #blizzardpartner"
    old = check_caption("Stormwind at dawn #ad #blizzardpartner #a #b #c #d", profile)
    assert len(old) == 3  # @warcraft saknas, taggarna på samma rad, fyra hashtags för mycket
    assert _caption_problems([*old, "Klippet är 4.0 s."], good, profile) == ["Klippet är 4.0 s."]


def test_clip_without_overlay_text_or_facecam_passes_the_rules() -> None:
    profile = _profile()
    caption = build_caption("The Lich King rises", profile)
    problems, warnings = check_clip(CLIP, NOTHING_ADDED, profile, caption, "epic_moment", review=None)
    assert problems == []
    assert warnings == []
    review = {"facecam_visible": False, "text_visible": False, "overlay_visible": False, "face_covered": False}
    assert check_clip(CLIP, NOTHING_ADDED, profile, caption, "cinematic_beat", review)[0] == []


class FakePicker:
    def __init__(self):
        self.windows: list[str] = []

    def pick(self, window, candidate, stream_context):
        self.windows.append(window)
        return ClipPick(include=True, start_offset_s=candidate.t_center - 5, end_offset_s=candidate.t_center + 15,
                        title_es="Dragon over Stormwind", reason="Big roar.", score=60)


@needs_ffmpeg
def test_audio_energy_is_measured_per_window_so_a_loud_moment_stands_out(tmp_path) -> None:
    """Utan tal är ljudet den enda signalen. En smäll vid 6-8 s ska ge en topp där, inte ett medel som
    ligger kvar högt resten av videon."""
    mp4 = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
         "-f", "lavfi", "-i", "anoisesrc=amplitude=0.9:sample_rate=48000:duration=2",
         "-filter_complex", "[0:a]volume=0.03[bg];[1:a]adelay=6000|6000[hit];[bg][hit]amix=inputs=2:normalize=0",
         "-t", "20", "-c:a", "aac", str(mp4)],
        check=True,
    )
    rms = dict(compute_audio_rms_series(mp4, 2.0))
    loud = max(rms, key=rms.get)
    assert 6 <= loud <= 8
    later = min(rms, key=lambda t: abs(t - 15))
    assert rms[later] < rms[loud] - 15  # 7 s efter smällen är det tyst igen


def test_a_moment_without_speech_still_goes_to_claude_with_the_blizzard_config() -> None:
    cfg = load_config(PAKET / "config.blizzard_nordics.yaml")
    music_only = Transcript(video_id="trailer", language="en",
                            segments=[Segment(start=2.0, end=4.0, text="For Azeroth.", words=[])])
    loud = CandidateMoment(t_center=100.0, score=15.0, sources=["audio_energy"], reasons=["audio peak"])
    picker = FakePicker()
    picks = select_clips([loud], music_only, picker, "ctx", cfg.clip)
    assert len(picker.windows) == 1
    assert [p.pick.title_es for p in picks] == ["Dragon over Stormwind"]
