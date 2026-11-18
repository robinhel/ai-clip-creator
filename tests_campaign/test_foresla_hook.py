"""Föreslå hook: Claude föreslår en ny hook för en ny version av klippet. Ingen riktig Claude."""

from __future__ import annotations

from campaign.brain import Brain

from .conftest import needs_ffmpeg, needs_subtitles
from .test_ny_version import _hook_on, _make, vclip  # noqa: F401


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
def test_a_suggested_hook_that_is_taken_or_too_long_twice_is_400(vclip, monkeypatch) -> None:  # noqa: F811
    client, _, clip_id = vclip
    fake = _claude_hook(monkeypatch, "No way")
    answer = _suggest(client, clip_id)
    assert answer.status_code == 400 and "Försök igen" in answer.json()["detail"], answer.text
    assert len(fake.messages.calls) == 2
    _claude_hook(monkeypatch, "This hook is far too long to fit in three short lines at the top of the clip")
    assert _suggest(client, clip_id).status_code == 400
    _claude_hook(monkeypatch, '" #fyp "')  # tom när citattecken och taggar är borta
    assert _suggest(client, clip_id).status_code == 400


@needs_ffmpeg
def test_a_suggested_hook_without_a_key_is_400_in_swedish(vclip, settings) -> None:  # noqa: F811
    client, _, clip_id = vclip
    settings.anthropic_api_key = ""
    answer = _suggest(client, clip_id)
    assert answer.status_code == 400 and "nyckel" in answer.json()["detail"].lower()
    assert _suggest(client, 999).status_code == 404
