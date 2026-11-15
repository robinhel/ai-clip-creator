"""Tests for selection.selector — transcript windowing and clip selection logic."""

from __future__ import annotations

import pytest
from helpers import _seg, _transcript

from clipsmith.llm.base import ClipPick, LLMUnavailable
from clipsmith.llm.prompts import build_stream_context
from clipsmith.models.candidates import CandidateMoment
from clipsmith.selection.selector import (
    _extract_transcript_window,
    select_clips,
)
from clipsmith.settings import ClipConfig


def _candidate(t: float = 300.0, score: float = 50.0) -> CandidateMoment:
    return CandidateMoment(
        t_center=t,
        score=score,
        sources=["chat_density"],
        reasons=["spike at t=300"],
    )


def _cfg(**kwargs: object) -> ClipConfig:
    defaults: dict[str, object] = {
        "min_seconds": 15,
        "max_seconds": 30,
        "preroll_s": 25,
        "postroll_s": 10,
    }
    defaults.update(kwargs)
    return ClipConfig(**defaults)  # type: ignore[arg-type]


# ── transcript window extraction ──────────────────────────────────────────────


def test_extract_window_includes_segments_in_range() -> None:
    t = _transcript(
        _seg(0.0, 5.0, "intro"),
        _seg(240.0, 245.0, "before window"),
        _seg(260.0, 265.0, "in window"),
        _seg(300.0, 305.0, "center"),
        _seg(325.0, 330.0, "after center"),
        _seg(500.0, 505.0, "far future"),
    )
    window = _extract_transcript_window(t, t_center=300.0)
    assert "in window" in window
    assert "center" in window
    assert "after center" in window
    assert "intro" not in window
    assert "far future" not in window


def test_extract_window_empty_transcript() -> None:
    t = _transcript()
    window = _extract_transcript_window(t, t_center=100.0)
    assert "no transcript" in window.lower()


def test_extract_window_has_relative_timestamps() -> None:
    t = _transcript(_seg(300.0, 302.0, "right at center"))
    window = _extract_transcript_window(t, t_center=300.0)
    assert "+0.0s" in window or "+0" in window


# ── select_clips with mock picker ─────────────────────────────────────────────


class _AcceptAll:
    def __init__(self) -> None:
        self.calls: list[CandidateMoment] = []

    def pick(
        self, transcript_window: str, candidate: CandidateMoment, stream_context: str
    ) -> ClipPick:
        self.calls.append(candidate)
        return ClipPick(
            include=True,
            start_offset_s=candidate.t_center - 10,
            end_offset_s=candidate.t_center + 15,
            title_es="Momento gracioso",
            reason="Mock: accepted.",
        )


class _RejectAll:
    def pick(
        self, transcript_window: str, candidate: CandidateMoment, stream_context: str
    ) -> ClipPick:
        return ClipPick(
            include=False,
            start_offset_s=0.0,
            end_offset_s=0.0,
            title_es="n/a",
            reason="Mock: rejected.",
        )


class _FailAll:
    def pick(self, transcript_window: str, candidate: CandidateMoment, stream_context: str) -> None:
        return None


def _make_transcript_with_content() -> object:
    return _transcript(
        _seg(100.0, 105.0, " Muy gracioso"),
        _seg(290.0, 295.0, " Primer candidato"),
        _seg(490.0, 495.0, " Segundo candidato"),
    )


def test_select_clips_accept_all() -> None:
    candidates = [_candidate(300.0, 80.0), _candidate(500.0, 60.0)]
    transcript = _make_transcript_with_content()
    picker = _AcceptAll()
    picks = select_clips(candidates, transcript, picker, "ctx", _cfg(), max_candidates=10)  # type: ignore[arg-type]
    assert len(picks) == 2
    assert len(picker.calls) == 2


def test_select_clips_reject_all() -> None:
    candidates = [_candidate(300.0)]
    picks = select_clips(
        candidates, _make_transcript_with_content(), _RejectAll(), "ctx", _cfg(), min_picks=0
    )  # type: ignore[arg-type]
    assert picks == []


def test_select_clips_all_failed_stops_instead_of_no_clips() -> None:
    candidates = [_candidate(300.0)]
    with pytest.raises(LLMUnavailable):
        select_clips(
            candidates, _make_transcript_with_content(), _FailAll(), "ctx", _cfg(), min_picks=0
        )  # type: ignore[arg-type]


def test_select_clips_respects_max_candidates() -> None:
    # Candidates centered near transcript segments (100s, 290s, 490s) so none are skipped
    # for missing transcript. Spaced 200s apart to clear min_clip_gap_s=120.
    candidates = [_candidate(float(150 + i * 200), float(100 - i)) for i in range(10)]
    picker = _AcceptAll()
    select_clips(
        candidates, _make_transcript_with_content(), picker, "ctx", _cfg(), max_candidates=3
    )  # type: ignore[arg-type]
    assert len(picker.calls) == 3


def test_select_clips_clamped_duration() -> None:
    class _LongPick:
        def pick(self, tw: str, c: CandidateMoment, sc: str) -> ClipPick:
            return ClipPick(
                include=True,
                start_offset_s=c.t_center - 5,
                end_offset_s=c.t_center + 55,
                title_es="Largo",
                reason="test",
            )

    candidates = [_candidate(300.0)]
    picks = select_clips(
        candidates,
        _make_transcript_with_content(),
        _LongPick(),
        "ctx",
        _cfg(max_seconds=30),  # type: ignore[arg-type]
    )
    assert len(picks) == 1
    assert picks[0].pick.duration_s == pytest.approx(30.0)


def test_build_stream_context_contains_channel() -> None:
    ctx = build_stream_context("chuyelwuero", "Stream title", "3h21m4s")
    assert "chuyelwuero" in ctx
    assert "English" in ctx
    assert "3h21m4s" in ctx


def test_select_clips_default_forces_nothing() -> None:
    candidates = [_candidate(300.0)]
    picks = select_clips(candidates, _make_transcript_with_content(), _RejectAll(), "ctx", _cfg())  # type: ignore[arg-type]
    assert picks == []


def test_select_clips_min_picks_from_config() -> None:
    candidates = [_candidate(300.0)]
    picks = select_clips(
        candidates, _make_transcript_with_content(), _RejectAll(), "ctx", _cfg(min_picks=1)
    )  # type: ignore[arg-type]
    assert len(picks) == 1


def test_build_stream_context_says_focus_and_target_length() -> None:
    ctx = build_stream_context("timmy", "t", "1h", focus="jump scares", clip_range=(15, 30))
    assert "especially looking for: jump scares" in ctx
    assert "15-30 seconds" in ctx
    plain = build_stream_context("timmy", "t", "1h")
    assert "looking for" not in plain
    assert "Target clip length" not in plain


def test_select_clips_skips_a_moment_without_speech_by_default() -> None:
    picker = _AcceptAll()
    picks = select_clips(
        [_candidate(5000.0)], _make_transcript_with_content(), picker, "ctx", ClipConfig()
    )  # type: ignore[arg-type]
    assert picks == []
    assert picker.calls == []


# ── v2: whole sentences and calibration ──────────────────────────────────────

from helpers import _word


def _sentences() -> object:
    # Three sentences: 100-104, 105-118, 119-130
    return _transcript(
        _seg(
            100.0,
            118.0,
            " So the boss is here. Watch this, I am going to solo him right now!",
            [
                _word(100.0, 100.5, "So"),
                _word(100.5, 101.0, "the"),
                _word(101.0, 101.5, "boss"),
                _word(101.5, 102.0, "is"),
                _word(102.0, 104.0, "here."),
                _word(105.0, 106.0, "Watch"),
                _word(106.0, 107.0, "this,"),
                _word(107.0, 110.0, "I"),
                _word(110.0, 115.0, "am"),
                _word(115.0, 118.0, "now!"),
            ],
        ),
        _seg(119.0, 130.0, " No way.", [_word(119.0, 125.0, "No"), _word(125.0, 130.0, "way.")]),
    )


class _Fixed:
    def __init__(self, start: float, end: float, score: int = 50) -> None:
        self.start, self.end, self.score = start, end, score

    def pick(self, tw: str, c: CandidateMoment, sc: str) -> ClipPick:
        return ClipPick(
            include=True,
            start_offset_s=self.start,
            end_offset_s=self.end,
            title="T",
            reason="r",
            score=self.score,
        )


def test_v2_moves_start_and_end_to_the_nearest_sentence_borders() -> None:
    picks = select_clips(
        [_candidate(110.0)],
        _sentences(),
        _Fixed(106.5, 127.0),
        "ctx",
        _cfg(min_seconds=10, max_seconds=30),
    )  # type: ignore[arg-type]
    assert picks[0].pick.start_offset_s == pytest.approx(105.0)
    assert picks[0].pick.end_offset_s == pytest.approx(130.0)


def test_v2_end_border_must_keep_the_clip_length_within_limits() -> None:
    # nearest end 118 would give 13 s; min is 20, so the end goes to 130 (7 s away) instead
    picks = select_clips(
        [_candidate(110.0)],
        _sentences(),
        _Fixed(105.0, 123.0),
        "ctx",
        _cfg(min_seconds=20, max_seconds=30),
    )  # type: ignore[arg-type]
    assert picks[0].pick.end_offset_s == pytest.approx(130.0)


def test_v2_no_border_nearby_leaves_the_times_and_clamps() -> None:
    picks = select_clips(
        [_candidate(300.0)],
        _sentences(),
        _Fixed(290.0, 350.0),
        "ctx",
        _cfg(min_seconds=10, max_seconds=30, ask_without_speech=True),
    )  # type: ignore[arg-type]
    assert picks[0].pick.start_offset_s == pytest.approx(290.0)
    assert picks[0].pick.duration_s == pytest.approx(30.0)


def test_legacy_keeps_the_times_claude_gave() -> None:
    picks = select_clips(
        [_candidate(110.0)],
        _sentences(),
        _Fixed(106.5, 127.0),
        "ctx",
        _cfg(min_seconds=10, max_seconds=30, selection_mode="legacy"),
    )  # type: ignore[arg-type]
    assert (picks[0].pick.start_offset_s, picks[0].pick.end_offset_s) == (106.5, 127.0)


def test_selection_mode_defaults_to_v2() -> None:
    assert ClipConfig().selection_mode == "v2"


class _Ranker(_AcceptAll):
    def __init__(self, scores: list[int] | None) -> None:
        super().__init__()
        self.scores = scores
        self.ranked: list[list[str]] = []

    def rank(self, lines: list[str], stream_context: str) -> list[int] | None:
        self.ranked.append(lines)
        return self.scores


def test_v2_calibrates_the_accepted_clips_in_one_extra_call() -> None:
    picker = _Ranker([91, 40])
    picks = select_clips(
        [_candidate(300.0, 80.0), _candidate(500.0, 60.0)],
        _make_transcript_with_content(),
        picker,
        "ctx",
        _cfg(),
    )  # type: ignore[arg-type]
    assert len(picker.ranked) == 1 and len(picker.ranked[0]) == 2
    assert "Primer candidato" in picker.ranked[0][0]  # Claude sees what is said in the clip
    assert [p.pick.score for p in picks] == [91, 40]


def test_failed_calibration_keeps_the_first_scores() -> None:
    picker = _Ranker(None)
    picks = select_clips(
        [_candidate(300.0, 80.0), _candidate(500.0, 60.0)],
        _make_transcript_with_content(),
        picker,
        "ctx",
        _cfg(),
    )  # type: ignore[arg-type]
    assert [p.pick.score for p in picks] == [0, 0]


def test_no_calibration_for_one_clip_or_in_legacy() -> None:
    one = _Ranker([99])
    select_clips([_candidate(300.0)], _make_transcript_with_content(), one, "ctx", _cfg())  # type: ignore[arg-type]
    legacy = _Ranker([99, 98])
    select_clips(
        [_candidate(300.0, 80.0), _candidate(500.0, 60.0)],
        _make_transcript_with_content(),
        legacy,
        "ctx",
        _cfg(selection_mode="legacy"),
    )  # type: ignore[arg-type]
    assert one.ranked == [] and legacy.ranked == []


class _Payoff(_Fixed):
    def pick(self, tw: str, c: CandidateMoment, sc: str) -> ClipPick:
        pick = super().pick(tw, c, sc)
        pick.payoff_start_s, pick.payoff_end_s = 104.6, 125.0
        return pick


def test_v2_snap_never_cuts_the_payoff() -> None:
    # nearest borders are 105 and 118, but the payoff runs 104.6-125
    picks = select_clips(
        [_candidate(110.0)],
        _sentences(),
        _Payoff(104.5, 119.0),
        "ctx",
        _cfg(min_seconds=10, max_seconds=30),
    )  # type: ignore[arg-type]
    assert picks[0].pick.start_offset_s == pytest.approx(100.0)
    assert picks[0].pick.end_offset_s == pytest.approx(130.0)


def test_calibration_with_the_wrong_number_of_scores_keeps_the_first_scores(caplog) -> None:
    picker = _Ranker([91])
    picks = select_clips(
        [_candidate(300.0, 80.0), _candidate(500.0, 60.0)],
        _make_transcript_with_content(),
        picker,
        "ctx",
        _cfg(),
    )  # type: ignore[arg-type]
    assert [p.pick.score for p in picks] == [0, 0]
    assert "rank" in caplog.text
