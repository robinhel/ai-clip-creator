"""Roliga repliker (steg 3): Haiku hittar repliker i transkriptet, och de når Claude bland de 20 kandidaterna.
Påhittad Haiku och Claude. Inga riktiga anrop."""

from __future__ import annotations

import json
import re

import pytest

from campaign import pipeline, repliker
from campaign.budget import BudgetExceeded
from clipsmith.llm.anthropic_provider import AnthropicProvider
from clipsmith.llm.base import ClipPick
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment, Transcript
from clipsmith.selection import selector
from clipsmith.selection.selector import _spread_candidates

from .test_natt_korning import _make, vod  # noqa: F401  (fixture)

GAP = 90
VIRAL = (4509.9, 4515.2)  # Laurens moment i körning 47: "They know what I'm wearing..."


class FakeBrain:
    """Låtsas-Haiku. answers tas i tur och ordning; ett undantag kastas, en funktion får anropets text."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def ask(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(kwargs["content"]) if callable(answer) else answer


def _line(t: float, quote: str = "They know what I'm wearing", funny: int = 5) -> dict:
    return {"t": t, "quote": quote, "why": "absurd", "funny": funny}


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
    """Har Claudes fönster (60 s före, 30 s efter mitten) för någon kandidat hela momentet?"""
    return any(c.t_center - 60 <= VIRAL[0] and VIRAL[1] <= c.t_center + 30 for c in top)


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
    assert lifted.t_center == 4512.0  # repliken ligger efter 4486: mitten flyttas, så reaktionen efter 4516 finns med
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


TALK = "honestly I have no idea what this guy is doing over there but we are going in anyway chat"
REAL = {  # Laurens moment i VOD 2932652958, riktig transkripttext
    4509.9: "They know what I'm wearing and they know what my car looks like.",
    4515.2: "How do I get changed?",
}


def _long_stream() -> list[Segment]:
    """3 timmar snabbt prat (18 ord på 6 s) med den riktiga texten runt 4512 s."""
    segments = [s for s in _segments(3 * 3600, step=6, text=TALK) if not 4506 <= s.start <= 4518]
    segments += [Segment(start=t, end=t + 5.3, text=text, words=[]) for t, text in REAL.items()]
    return sorted(segments, key=lambda s: s.start)


def _like_haiku(content: str) -> dict:
    """Som den riktiga Haiku på Macen: högst 2 repliker per anrop, hur långt anropet än är. Momentet först
    när det finns i texten."""
    rows = [(int(m[1]), m[2]) for m in re.finditer(r"^\[(\d+)\] (.*)$", content, re.M)]
    lines = (
        [_line(4512, "They know what I'm wearing", 9)]
        if "They know what I'm wearing" in content
        else []
    )
    lines += [_line(t, text[:30], 3) for t, text in rows[:: max(1, len(rows) // 2)]]
    return {"lines": lines[:2]}


def test_a_long_stream_gives_many_lines_and_the_real_moment() -> None:
    brain = FakeBrain(_like_haiku)
    lines = repliker.find_lines(_long_stream(), brain)
    assert len(brain.calls) >= 4  # bitar på cirka 40 000 tecken, inte ett enda anrop
    assert len(lines) >= len(brain.calls)  # minst en replik per bit med tal
    assert lines[0]["t"] == 4512 and lines[0]["funny"] == 9  # högst betyg först
    call = next(c for c in brain.calls if "They know what I'm wearing" in c["content"])
    assert (
        "[4509] They know what I'm wearing and they know what my car looks like." in call["content"]
    )
    assert "[4515] How do I get changed?" in call["content"]


def test_the_real_moment_reaches_claude_among_the_20_with_run_47() -> None:
    lines = repliker.find_lines(_long_stream(), FakeBrain(_like_haiku))
    top = _top(repliker.merge(_run47(), lines, GAP))
    assert len(top) == 20 and _has_viral(top)
    assert any("They know what I'm wearing" in r for c in top for r in c.reasons)


def test_a_line_outside_its_part_is_dropped() -> None:
    segments = _segments(600, text="x" * 400)  # 120 rader på 400 tecken: två bitar
    brain = FakeBrain(
        {
            "lines": [
                _line(250),
                _line(99999),
                _line(-3),
                {"t": "x", "quote": "q", "why": "", "funny": 5},
            ]
        }
    )
    lines = repliker.find_lines(segments, brain)
    assert len(brain.calls) == 2 and [line["t"] for line in lines] == [250.0]  # bara i första biten
    call = brain.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["purpose"] == "repliker"


def test_budget_stop_keeps_earlier_parts_and_asks_no_more() -> None:
    segments = _segments(1800, text="x" * 400)  # fyra bitar
    brain = FakeBrain(
        RuntimeError("nätverk"),
        {"lines": [_line(600)]},
        BudgetExceeded("Månadens budget är slut."),
        {"lines": [_line(1700)]},
    )
    problems: list[str] = []
    lines = repliker.find_lines(segments, brain, problems=problems)
    assert len(brain.calls) == 3 and [line["t"] for line in lines] == [600.0]
    assert len(problems) == 2


def test_a_very_long_transcript_is_split_in_as_few_parts_as_possible() -> None:
    segments = _segments(12 * 3600, step=2, text="a long line of talking " * 3)
    brain = FakeBrain({"lines": []})
    repliker.find_lines(segments, brain)
    total = sum(len(call["content"]) for call in brain.calls)
    assert len(brain.calls) == -(-total // repliker.CHUNK_CHARS)
    assert all(
        len(call["content"]) <= repliker.CHUNK_CHARS + 200 for call in brain.calls
    )  # högst en rad över


def test_each_parts_best_line_comes_before_the_next_round() -> None:
    def answer(content: str) -> dict:
        start = int(content[1 : content.index("]")])
        if start < 400:  # ett lugnt moment, bitens bästa
            return {"lines": [_line(100, "calm", 7)]}
        return {"lines": [_line(start + 10 + 90 * k, "loud", 8) for k in range(5)]}

    brain = FakeBrain(answer)
    lines = repliker.find_lines(_segments(1200, text="x" * 400), brain)  # tre bitar
    assert len(brain.calls) == 3 and len(lines) == 11
    merged = repliker.merge([], lines, GAP, slots=5)
    assert any(c.t_center == 100 and repliker.SOURCE in c.sources for c in merged)
    assert len(merged) == 5


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


def test_a_line_on_the_first_row_of_a_part_starting_mid_second_is_kept(tmp_path) -> None:
    segments = [Segment(start=4509.9, end=4515.2, text=REAL[4509.9], words=[])]
    segments += [Segment(start=t, end=t + 5, text="ok", words=[]) for t in range(4516, 4600, 5)]
    brain = FakeBrain({"lines": [_line(4509, funny=9)]})
    candidates = [CandidateMoment(100.0, 10.0, ["chat_density"], ["x"])]
    got = repliker.add_lines(candidates, segments, tmp_path / "repliker.json", brain, GAP)
    assert any(repliker.SOURCE in c.sources for c in got)


def test_an_old_cache_without_version_is_not_used(tmp_path) -> None:
    path = tmp_path / "repliker.json"
    old = {"span": [0, 600], "lines": [_line(100), _line(300)]}  # Macens gamla svar
    path.write_text(json.dumps(old), encoding="utf-8")
    brain = FakeBrain({"lines": [_line(250)]})
    candidates = [CandidateMoment(100.0, 10.0, ["chat_density"], ["x"])]
    repliker.add_lines(candidates, _segments(600), path, brain, GAP)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(brain.calls) == 1 and saved["version"] == repliker.VERSION
    saved["lines"] += [{"t": 300, "why": "utan citat"}, {"quote": "utan tid"}]
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert [line["t"] for line in repliker._cached(path, [0, 600])] == [250]


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


# ---------- clip.max_candidates ----------


def test_merge_with_30_keeps_the_signals_top_25_and_at_most_5_lines() -> None:
    candidates = _run47()
    signals_top = _spread_candidates(sorted(candidates, key=lambda c: c.score, reverse=True), GAP, 25)
    lines = [_line(t) for t in (4512, 5300, 6020, 1000, 2000, 3200, 6800, 9000, 9600)]
    top = _spread_candidates(
        sorted(repliker.merge(candidates, lines, GAP, max_n=30), key=lambda c: c.score, reverse=True), GAP, 30
    )
    assert len(top) == 30
    kept = {(c.t_center, c.score) for c in signals_top}
    assert kept <= {(c.t_center, c.score) for c in top}
    added = [c for c in top if (c.t_center, c.score) not in kept]
    assert len(added) == 5 and all(repliker.SOURCE in c.sources for c in added)


def _forty(settings, vod, max_candidates: int | None) -> None:  # noqa: F811
    """40 kandidater med 100 s mellan, ett transkript som täcker dem och kampanjens max_candidates."""
    vod.candidates = [CandidateMoment(50.0 + 100 * k, 60.0 - k, ["chat_density"], ["x"]) for k in range(40)]
    segments = [Segment(start=t, end=t + 5, text="wow", words=[]) for t in range(0, 4100, 5)]
    (vod.mp4.parent / "transcript.json").write_text(Transcript("123", "en", segments).to_json(), encoding="utf-8")
    if max_candidates is not None:
        path = settings.profiles_dir / "skiimasktimmy.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("clip:\n", f"clip:\n  max_candidates: {max_candidates}\n", 1), encoding="utf-8")


def test_the_campaigns_max_candidates_decides_how_many_claude_sees(settings, vod, monkeypatch) -> None:  # noqa: F811
    brain = FakeBrain({"lines": [_line(3333, "I'm not even mad")]})
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: brain)
    _forty(settings, vod, 30)
    vod.picker = Picker()
    _make(settings, vod)
    assert len(vod.picker.seen) == 30
    saved = json.loads((vod.mp4.parent / "candidates.valda.json").read_text(encoding="utf-8"))
    line = [c for c in saved if repliker.SOURCE in c["sources"] and "I'm not even mad" in c["reasons"][-1]]
    assert [c["score"] for c in line] == [60.0 - 24]  # golvet är signalernas 25:e (30 - 5), inte 15:e (20 - 5)
    assert not any(repliker.SOURCE in c["sources"] for c in json.loads(
        (vod.mp4.parent / "candidates.json").read_text(encoding="utf-8")))  # candidates.json är orörd


def test_without_max_candidates_claude_sees_20(settings, vod, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: FakeBrain({"lines": []}))
    _forty(settings, vod, None)
    vod.picker = Picker()
    _make(settings, vod)
    assert len(vod.picker.seen) == 20


# ---------- fönstret före mitten och den lyfta kandidatens mitt ----------


def test_a_lifted_candidate_whose_window_has_the_line_keeps_its_center() -> None:
    candidates = [CandidateMoment(t, 100.0 - t / 100, ["chat_density"], ["x"]) for t in range(3000, 9000, 200)]
    covers = CandidateMoment(1040.0, 1.0, ["audio_energy"], ["loud"])  # fönstret 980-1070 har 1000
    merged = repliker.merge(candidates + [covers], [_line(1000)], GAP)
    lifted = next(c for c in merged if repliker.SOURCE in c.sources)
    assert lifted.t_center == 1040.0 and lifted.score == 100.0 - 5800 / 100  # golvet som förut
    assert "They know what I'm wearing" in lifted.reasons[-1]

    far = CandidateMoment(1080.0, 1.0, ["audio_energy"], ["loud"])  # fönstret 1020-1110 saknar 1000
    merged = repliker.merge(candidates + [far], [_line(1000)], GAP)
    assert next(c for c in merged if repliker.SOURCE in c.sources).t_center == 1000.0


class WindowPicker(Picker):
    """Claude som minns transkriptet den fick per kandidat och kan kasta ett fel."""

    def __init__(self, error: Exception | None = None):
        super().__init__()
        self.windows: dict[float, str] = {}
        self.error = error

    def pick(self, window, candidate, stream_context):
        if self.error:
            raise self.error
        self.windows[candidate.t_center] = window
        return super().pick(window, candidate, stream_context)


def _window_run(settings, vod, monkeypatch, before: int | None, picker: WindowPicker) -> None:  # noqa: F811
    """Kandidaten vid 400 s och en rad 80 s före mitten; kampanjens clip.window_before_s om before är satt."""
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: FakeBrain({"lines": []}))
    segments = [Segment(start=320, end=324, text="the setup starts here", words=[]),
                Segment(start=400, end=404, text="the reaction", words=[])]
    (vod.mp4.parent / "transcript.json").write_text(Transcript("123", "en", segments).to_json(), encoding="utf-8")
    if before is not None:
        path = settings.profiles_dir / "skiimasktimmy.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("clip:\n", f"clip:\n  window_before_s: {before}\n", 1), encoding="utf-8")
    vod.picker = picker
    _make(settings, vod)


def test_window_before_s_90_gives_claude_the_line_80_s_before(settings, vod, monkeypatch) -> None:  # noqa: F811
    _window_run(settings, vod, monkeypatch, 90, WindowPicker())
    assert "[-80.0s] the setup starts here" in vod.picker.windows[400.0]
    assert selector.WINDOW_PRE_S == 60.0


def test_without_window_before_s_the_line_80_s_before_is_missing(settings, vod, monkeypatch) -> None:  # noqa: F811
    _window_run(settings, vod, monkeypatch, None, WindowPicker())
    assert "the reaction" in vod.picker.windows[400.0]
    assert "the setup starts here" not in vod.picker.windows[400.0]


def test_the_window_is_60_again_when_the_choice_fails(settings, vod, monkeypatch) -> None:  # noqa: F811
    with pytest.raises(RuntimeError, match="claude broke"):
        _window_run(settings, vod, monkeypatch, 90, WindowPicker(RuntimeError("claude broke")))
    assert selector.WINDOW_PRE_S == 60.0
