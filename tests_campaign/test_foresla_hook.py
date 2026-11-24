"""Hooken i en ny version: en egen hook i bilden, rubriken och captionen, och Föreslå hook där Claude föreslår
en ny. Ingen riktig Claude."""

from __future__ import annotations

import pytest

from campaign.brain import Brain
from campaign.caption import build_caption
from campaign.profile import load_profile
from campaign.versions import NO_OWN

from .conftest import needs_ffmpeg, needs_subtitles
from .test_ny_version import _hook_on, _make, _spy, vclip  # noqa: F401


@needs_subtitles
def test_a_new_hook_goes_into_the_picture_the_title_and_the_caption(vclip, settings,  # noqa: F811
                                                                   monkeypatch) -> None:
    client, log, clip_id = vclip
    _hook_on(settings)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert log.get(clip_id)["caption"] == build_caption("No way", profile, "Bodycam")  # mallens caption
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, hook="  Nobody expected that ending ")
    assert answer.status_code == 201, answer.text
    version = answer.json()
    caption = build_caption("Nobody expected that ending", profile, "Bodycam")
    assert seen[-1]["title"] == "Nobody expected that ending"
    assert (version["title"], version["caption"]) == ("Nobody expected that ending", caption)
    txt = settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.txt"
    assert txt.read_text(encoding="utf-8") == caption + "\n"
    assert NO_OWN not in version["warnings"]


@needs_subtitles
def test_an_empty_hook_is_the_originals_and_a_hard_space_is_a_space(vclip, settings,  # noqa: F811
                                                                   monkeypatch) -> None:
    client, log, clip_id = vclip
    _hook_on(settings)
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, hook="   ")
    assert answer.status_code == 201, answer.text
    assert (answer.json()["title"], answer.json()["caption"]) == ("No way", log.get(clip_id)["caption"])
    assert seen[-1]["title"] == "No way" and NO_OWN in answer.json()["warnings"]
    answer = _make(client, clip_id, hook="Nobody\u00a0saw that coming")  # hårt blanksteg från en kopierad text
    assert answer.status_code == 201, answer.text
    assert answer.json()["title"] == "Nobody saw that coming"
    answer = _make(client, clip_id, hook="Nobody\u202fexpected that")  # smalt hårt blanksteg
    assert answer.status_code == 201, answer.text
    assert answer.json()["title"] == "Nobody expected that"


@needs_subtitles
def test_a_handwritten_caption_is_kept_with_a_new_hook(vclip, settings) -> None:  # noqa: F811
    client, log, clip_id = vclip
    log.update(clip_id, caption="Min egen text #bodycam")
    answer = _make(client, clip_id, hook="Nobody expected that ending")
    assert answer.status_code == 201, answer.text
    assert (answer.json()["title"], answer.json()["caption"]) == ("Nobody expected that ending",
                                                                  "Min egen text #bodycam")


def _claude_hook(monkeypatch, *hooks):
    """En låtsad Claude som svarar hookarna i tur och ordning, sedan den sista om och om igen."""
    from campaign import variant

    from .conftest import FakeClient

    fake = FakeClient({"hook": hooks[0]})
    answers = iter(hooks)
    real = fake.messages.create

    def create(**kwargs):
        fake.messages.answer = {"hook": next(answers, hooks[-1])}
        return real(**kwargs)

    fake.messages.create = create
    monkeypatch.setattr(variant, "Brain", lambda settings, budget: Brain(settings, budget, client=fake))
    return fake


def _suggest(client, clip_id):
    return client.post(f"/campaign/clips/{clip_id}/versions/hook")


@needs_subtitles
def test_a_suggested_hook_differs_from_the_used_ones_and_fits(vclip, settings, monkeypatch) -> None:  # noqa: F811
    client, _, clip_id = vclip
    _hook_on(settings)
    version = _make(client, clip_id, hook="Nobody saw that coming").json()
    fake = _claude_hook(monkeypatch, "nobody SAW that coming", "Bodycam never felt this tense #fyp")
    answer = _suggest(client, version["id"])  # en version: originalets hookar räknas
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"hook": "Bodycam never felt this tense"}
    assert len(fake.messages.calls) == 2
    sent = str(fake.messages.calls[0]["messages"])
    assert "No way" in sent and "Nobody saw that coming" in sent  # hookarna som redan finns skickas med


@needs_ffmpeg
@pytest.mark.parametrize("space", ["\u00a0", "\u202f"])  # hårt och smalt hårt blanksteg
def test_a_suggested_hook_with_a_hard_space_is_accepted(vclip, monkeypatch, space) -> None:  # noqa: F811
    client, _, clip_id = vclip
    fake = _claude_hook(monkeypatch, f"Nobody{space}saw that coming")
    answer = _suggest(client, clip_id)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"hook": "Nobody saw that coming"} and len(fake.messages.calls) == 1


@needs_ffmpeg
def test_a_suggested_hook_that_is_taken_or_too_long_twice_is_400(vclip, monkeypatch) -> None:  # noqa: F811
    client, _, clip_id = vclip
    fake = _claude_hook(monkeypatch, "No way")
    answer = _suggest(client, clip_id)
    assert answer.status_code == 400 and "Try again" in answer.json()["detail"], answer.text
    assert len(fake.messages.calls) == 2
    _claude_hook(monkeypatch, "THIS HOOK IS FAR TOO LONG TO FIT IN THREE SHORT LINES AT THE TOP OF THE CLIP")
    assert _suggest(client, clip_id).status_code == 400
    _claude_hook(monkeypatch, '" #fyp "')  # tom när citattecken och taggar är borta
    assert _suggest(client, clip_id).status_code == 400


@needs_ffmpeg
def test_a_suggested_hook_without_a_key_is_400_in_swedish(vclip, settings) -> None:  # noqa: F811
    client, _, clip_id = vclip
    settings.anthropic_api_key = ""
    answer = _suggest(client, clip_id)
    assert answer.status_code == 400 and "key" in answer.json()["detail"].lower()
    assert _suggest(client, 999).status_code == 404
