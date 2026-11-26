"""Roliga repliker (steg 3): Haiku hittar repliker i transkriptet, och de når Claude bland de 20 kandidaterna.
Påhittad Haiku och Claude. Inga riktiga anrop."""

from __future__ import annotations

import json

from campaign import pipeline, repliker
from campaign.budget import BudgetExceeded
from clipsmith.llm.anthropic_provider import AnthropicProvider
from clipsmith.llm.base import ClipPick
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment
from clipsmith.selection.selector import _spread_candidates

from .test_natt_korning import _make, vod  # noqa: F401  (fixture)

GAP = 90
VIRAL = (4509.9, 4515.2)  # Laurens moment i körning 47: "They know what I'm wearing..."


class FakeBrain:
    """Låtsas-Haiku. answers tas i tur och ordning; ett undantag kastas."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def ask(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _line(t: float, quote: str = "They know what I'm wearing") -> dict:
    return {"t": t, "quote": quote, "why": "absurd"}


def _run47() -> list[CandidateMoment]:
    """132 kandidater som i körning 47: 67 som går igenom spridningen med 90 s och 65 strax före dem med lägre
    poäng. Kandidaten vid 4486 s har 32,8 och rang 34 efter spridningen."""
    times = [136.0 + 150 * k for k in range(67)]  # k = 29 ger 4486
    order = [29] + [k for k in reversed(range(67)) if k != 29]
    ranks = {k: (33 if k == 29 else (i - 1 if i <= 33 else i)) for i, k in enumerate(order)}
    out = []
    for k, t in enumerate(times):
        r = ranks[k]
        score = 32.8 + (33 - r) * 2 if r < 33 else 32.8 - (r - 33) * 0.4
        out.append(CandidateMoment(t, round(score, 2), ["chat_density"], ["chat"]))
        if k < 65:
            out.append(CandidateMoment(t - 45, round(score - 0.1, 2), ["audio_energy"], ["loud"]))
    return out


def _top(candidates: list[CandidateMoment]) -> list[CandidateMoment]:
    return _spread_candidates(sorted(candidates, key=lambda c: c.score, reverse=True), GAP, 20)


def _has_viral(top: list[CandidateMoment]) -> bool:
    return any(VIRAL[0] - 10 <= c.t_center <= VIRAL[1] + 10 for c in top)


def _segments(seconds: int, step: int = 5, text: str = "ok") -> list[Segment]:
    return [Segment(start=t, end=t + step, text=text, words=[]) for t in range(0, seconds, step)]


# ---------- merge ----------


def test_run_47_data_is_what_it_should_be() -> None:
    candidates = _run47()
    assert len(candidates) == 132
    spread = _spread_candidates(sorted(candidates, key=lambda c: c.score, reverse=True), GAP, 200)
    assert len(spread) == 67
    assert (spread[33].t_center, spread[33].score) == (4486.0, 32.8)
    assert not _has_viral(_top(candidates))  # utan repliker ser Claude aldrig momentet


def test_the_viral_line_reaches_claude_among_the_20() -> None:
    candidates = _run47()
    merged = repliker.merge(candidates, [_line(4512)], GAP)
    top = _top(merged)
    assert len(top) == 20 and _has_viral(top)
    lifted = next(c for c in top if repliker.SOURCE in c.sources)
    assert "They know what I'm wearing" in lifted.reasons[-1]
    assert lifted.t_center - 60 <= 4512 <= lifted.t_center + 30  # repliken är i Claudes fönster
    assert len(merged) == 132  # kandidaten vid 4486 lyftes, ingen ny lades till
    assert [c.t_center for c in candidates if c.score == 32.8] == [4486.0]  # originalet är orört


def test_at_most_slots_lines_and_the_signals_top_15_stay() -> None:
    candidates = _run47()
    signals_top = _spread_candidates(
        sorted(candidates, key=lambda c: c.score, reverse=True), GAP, 15
    )
    lines = [
        _line(t) for t in (4512, 8400, 5300, 6020, 1000, 2000, 3200, 6800)
    ]  # 8400: i fönstret för 8386 (topp 15)
    top = _top(repliker.merge(candidates, lines, GAP))
    assert len(top) == 20
    kept = {(c.t_center, c.score) for c in signals_top}
    assert kept <= {(c.t_center, c.score) for c in top}
    added = [c for c in top if (c.t_center, c.score) not in kept]
    assert len(added) == 5 and all(repliker.SOURCE in c.sources for c in added)
    assert any(
        repliker.SOURCE in c.sources for c in top if (c.t_center, c.score) in kept
    )  # 8400 fick citatet


def test_a_line_far_from_every_candidate_becomes_a_new_candidate() -> None:
    candidates = [
        CandidateMoment(t, 50.0 - t / 100, ["chat_density"], ["x"]) for t in range(0, 3000, 100)
    ]
    merged = repliker.merge(candidates, [_line(5000)], GAP)
    new = [c for c in merged if c.t_center == 5000]
    assert len(new) == 1 and new[0].sources == [repliker.SOURCE]
    assert new[0] in _top(merged)


def test_a_line_inside_a_top_candidates_window_only_adds_the_quote() -> None:
    candidates = [
        CandidateMoment(t, 100.0 - t / 100, ["chat_density"], ["x"]) for t in range(0, 6000, 200)
    ]
    merged = repliker.merge(candidates, [_line(410)], GAP)  # 400 är i topp 15, fönstret 340-430
    hit = next(c for c in merged if c.t_center == 400)
    assert repliker.SOURCE in hit.sources and len(merged) == len(candidates)
    assert [c.t_center for c in _top(merged)] == [c.t_center for c in _top(candidates)]


# ---------- find_lines ----------


def test_lines_outside_the_transcript_are_dropped() -> None:
    brain = FakeBrain(
        {"lines": [_line(250), _line(99999), _line(-3), {"t": "x", "quote": "q", "why": ""}]}
    )
    lines = repliker.find_lines(_segments(600), brain)
    assert [line["t"] for line in lines] == [250.0]
    call = brain.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["purpose"] == "repliker"
    assert "[250] ok" in call["content"]


def test_a_three_hour_transcript_is_one_call() -> None:
    text = (
        "honestly I have no idea what this guy is doing over there but we are going in anyway chat"
    )
    segments = _segments(
        3 * 3600, step=6, text=text
    )  # 18 ord på 6 s: 180 ord i minuten, snabbt prat
    brain = FakeBrain({"lines": []})
    repliker.find_lines(segments, brain)
    size = sum(len(call["content"]) for call in brain.calls)
    assert len(brain.calls) == 1 and size < repliker.CHUNK_CHARS


def test_a_very_long_transcript_is_split_in_as_few_parts_as_possible() -> None:
    segments = _segments(12 * 3600, step=2, text="a long line of talking " * 3)
    brain = FakeBrain({"lines": []})
    repliker.find_lines(segments, brain)
    total = sum(len(call["content"]) for call in brain.calls)
    assert len(brain.calls) == -(-total // repliker.CHUNK_CHARS)
    assert all(
        len(call["content"]) <= repliker.CHUNK_CHARS + 200 for call in brain.calls
    )  # högst en rad över


# ---------- add_lines ----------


def test_an_error_from_haiku_gives_a_run_without_lines(tmp_path) -> None:
    candidates = [CandidateMoment(100.0, 10.0, ["chat_density"], ["x"])]
    for error in (BudgetExceeded("Månadens budget är slut."), RuntimeError("nätverk")):
        got = repliker.add_lines(
            candidates, _segments(600), tmp_path / "repliker.json", FakeBrain(error), GAP
        )
        assert got == candidates
    assert not (
        tmp_path / "repliker.json"
    ).exists()  # ett fel sparas inte, nästa körning frågar igen


def test_saved_lines_are_used_again_without_a_new_call(tmp_path) -> None:
    path = tmp_path / "repliker.json"
    brain = FakeBrain({"lines": [_line(250)]})
    candidates = [CandidateMoment(100.0, 10.0, ["chat_density"], ["x"])]
    first = repliker.add_lines(candidates, _segments(600), path, brain, GAP)
    second = repliker.add_lines(candidates, _segments(600), path, brain, GAP)
    assert len(brain.calls) == 1 and first == second
    assert json.loads(path.read_text(encoding="utf-8"))["lines"][0]["t"] == 250


# ---------- make_clips ----------


class Picker(AnthropicProvider):
    """Claude som säger nej till allt och minns kandidaterna."""

    def __init__(self):
        super().__init__("test-key")
        self.seen: list[CandidateMoment] = []

    def pick(self, window, candidate, stream_context):
        self.seen.append(candidate)
        return ClipPick(
            include=False, start_offset_s=0, end_offset_s=20, title_es="x", reason="dull"
        )


def test_the_run_sends_haikus_line_to_claude_and_saves_it(settings, vod, monkeypatch) -> None:  # noqa: F811
    brain = FakeBrain({"lines": [_line(250, "I'm not even mad")]})
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: brain)
    vod.picker = Picker()
    _make(settings, vod)
    assert len(brain.calls) == 1
    assert any(
        repliker.SOURCE in c.sources and "I'm not even mad" in c.reasons[-1]
        for c in vod.picker.seen
    )
    assert (vod.mp4.parent / "repliker.json").exists()

    vod.picker = Picker()
    _make(settings, vod, run_id=8)  # en ny körning läser repliker.json
    assert len(brain.calls) == 1
    assert any(repliker.SOURCE in c.sources for c in vod.picker.seen)


def test_signals_mode_does_not_ask_haiku(settings, vod, monkeypatch) -> None:  # noqa: F811
    config = settings.root / "config.timmy.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  min_picks: 0", "  select: signals\n  min_picks: 0", 1
        ),
        encoding="utf-8",
    )
    brain = FakeBrain({"lines": [_line(250)]})
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: brain)
    _make(settings, vod)
    assert brain.calls == []
    assert not (vod.mp4.parent / "repliker.json").exists()


def test_a_broken_haiku_does_not_stop_the_run(settings, vod, monkeypatch) -> None:  # noqa: F811
    brain = FakeBrain(BudgetExceeded("Månadens budget är slut."))
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: brain)
    vod.picker = Picker()
    _, message = _make(settings, vod)
    assert len(brain.calls) == 1 and message == "Claude valde inga av 2 kandidater."
