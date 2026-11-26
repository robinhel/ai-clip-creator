"""Minnet: Claude får se kampanjens klipp som nådde visningar, och dem som inte gjorde det, och Robins ja och nej."""

from __future__ import annotations

from campaign.cliplog import ClipLog
from campaign.memory import selection_memory

from .test_natt_korning import FakePicker, _make, _no, _yes, vod  # noqa: F401  (fixture)


def _add(log: ClipLog, title: str, status: str, views: int | None = None, profile: str = "skiimasktimmy") -> int:
    clip_id = log.add(vod_id="1", profile=profile, file=f"/x/{title}.mp4", title=title, caption="c", status=status)
    if views is not None:
        log.update(clip_id, views=views)
    return clip_id


def test_memory_lists_hits_misses_and_robins_yes_and_no(tmp_path) -> None:
    log = ClipLog(tmp_path / "klipp.db")
    _add(log, "Huge clutch", "betald", 9000)
    _add(log, "Funny fail", "inskickad", 2400)
    _add(log, "Quiet loot", "postad", 300)
    _add(log, "Rage quit", "godkand")
    _add(log, "Menu talk", "nekad")
    _add(log, "Other streamer", "betald", 50000, profile="xqc")  # en annan kampanj räknas inte
    _add(log, "Waiting", "renderad")  # inte granskat än
    text = selection_memory(log, "skiimasktimmy", min_views=2000)
    hits, misses = text.index("Huge clutch"), text.index("Quiet loot")
    assert text.index("Funny fail") > hits  # flest visningar först
    assert hits < misses and "2000" in text  # gränsen står med, så att Claude vet vad som räknas
    assert "9000" in text and "300" in text
    assert "Rage quit" in text and "Menu talk" in text
    assert text.index("Rage quit") < text.index("Menu talk")  # godkända före nekade
    assert "Other streamer" not in text and "Waiting" not in text
    assert len(text) < 700  # kort: texten ingår i varje val


def test_no_clips_gives_no_memory(tmp_path) -> None:
    log = ClipLog(tmp_path / "klipp.db")
    _add(log, "Waiting", "renderad")
    assert selection_memory(log, "skiimasktimmy") == ""


def test_memory_keeps_the_newest_and_short_titles(tmp_path) -> None:
    log = ClipLog(tmp_path / "klipp.db")
    for i in range(10):
        _add(log, f"Rejected {i}", "nekad")
    _add(log, "x" * 300, "godkand")
    text = selection_memory(log, "skiimasktimmy")
    assert "Rejected 9" in text and "Rejected 0" not in text  # bara de senaste
    assert "x" * 100 not in text


def test_claude_gets_the_memory_after_the_focus(settings, vod) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    _add(log, "Huge clutch", "betald", 9000)
    vod.picker = FakePicker([_yes("clutch"), _no()])
    _make(settings, vod, focus="jump scares")
    ctx = vod.picker.contexts[0]
    assert "especially looking for: jump scares" in ctx
    assert ctx.index("jump scares") < ctx.index("Huge clutch")


def _fake_taste(monkeypatch, text: str) -> None:
    """campaign/taste.py byggs av en annan del. Här en låtsasmodul med bara taste_text."""
    import sys
    import types

    import campaign

    fake = types.ModuleType("campaign.taste")
    fake.taste_text = lambda profile: text
    monkeypatch.setitem(sys.modules, "campaign.taste", fake)
    monkeypatch.setattr(campaign, "taste", fake, raising=False)


def test_claude_gets_the_taste_after_the_memory(settings, vod, monkeypatch) -> None:  # noqa: F811
    _fake_taste(monkeypatch, "viral clips in this niche open on the payoff within 1 s")
    log = ClipLog(settings.data_dir / "klipp.db")
    _add(log, "Huge clutch", "betald", 9000)
    vod.picker = FakePicker([_yes("clutch"), _no()])
    _make(settings, vod, focus="jump scares")
    ctx = vod.picker.contexts[0]
    assert ctx.index("jump scares") < ctx.index("Huge clutch") < ctx.index("open on the payoff within 1 s")


def test_an_empty_taste_changes_nothing(settings, monkeypatch) -> None:
    from campaign import pipeline
    from campaign.profile import load_profile

    _fake_taste(monkeypatch, "")
    log = ClipLog(settings.data_dir / "klipp.db")
    _add(log, "Huge clutch", "betald", 9000)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    memory = selection_memory(log, "skiimasktimmy", min_views=profile.min_views or 2000)
    assert pipeline._with_memory("jump scares", profile, settings) == f"jump scares, and {memory}"
    assert pipeline._with_memory("", profile, settings) == memory
