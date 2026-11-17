"""Tests for candidates.builder and candidates.math."""

from __future__ import annotations

import pytest

from clipsmith.candidates.builder import _dedupe, build_candidates
from clipsmith.candidates.math import compute_density_scores
from clipsmith.models.chat import ChatLog, ChatMessage
from clipsmith.models.twitch import Clip
from clipsmith.settings import CandidatesConfig


# ── helpers ──────────────────────────────────────────────────────────────────


def _msg(t: float, text: str = "hey", author: str = "user", hype: int = 0) -> ChatMessage:
    return ChatMessage(
        time_in_seconds=t,
        message=text,
        author=author,
        is_clip_command=text.strip().lower().startswith("!clip"),
        hype_emote_count=hype,
    )


def _chat(messages: list[ChatMessage]) -> ChatLog:
    return ChatLog(video_id="v1", messages=messages)


def _clip(offset: int, clip_id: str = "c1", views: int = 100) -> Clip:
    return Clip(
        id=clip_id,
        url="https://twitch.tv/clip/x",
        title="funny moment",
        creator_name="creator",
        video_id="v1",
        vod_offset=offset,
        duration=25.0,
        view_count=views,
        created_at="2026-04-27T20:30:00Z",
    )


def _cfg(**overrides) -> CandidatesConfig:
    return CandidatesConfig(**overrides)


def _transcript(segments: list[tuple[float, float, str]]):
    from clipsmith.models.transcript import Segment, Transcript

    segs = [Segment(start=s, end=e, text=t, words=[]) for s, e, t in segments]
    return Transcript(video_id="v1", language="es", segments=segs)


# ── density math ──────────────────────────────────────────────────────────────


def test_density_detects_spike():
    quiet = [_msg(float(i * 10)) for i in range(50)]
    burst_center = 300.0
    burst = [_msg(burst_center + i * 0.3) for i in range(40)]
    msgs = quiet + burst

    scores = compute_density_scores(msgs, window_s=15, peak_multiplier=4.0, step_s=5.0)
    assert scores, "expected at least one density peak"
    peak_times = [t for t, _ in scores]
    assert any(290 <= t <= 320 for t in peak_times), f"peak not near burst: {peak_times}"


def test_density_empty_chat():
    assert compute_density_scores([], window_s=15, peak_multiplier=4.0) == []


def test_density_hype_emotes_boost_score():
    base = [_msg(float(i)) for i in range(100)]
    plain_burst = [_msg(200.0 + i * 0.2) for i in range(20)]
    hyped_burst = [_msg(400.0 + i * 0.2, hype=2) for i in range(20)]

    plain_scores = compute_density_scores(base + plain_burst, window_s=15, peak_multiplier=2.0)
    hyped_scores = compute_density_scores(base + hyped_burst, window_s=15, peak_multiplier=2.0)

    plain_max = max((s for _, s in plain_scores), default=0)
    hyped_max = max((s for _, s in hyped_scores), default=0)
    assert hyped_max > plain_max


def _spam_chat(distinct: bool) -> list[ChatMessage]:
    """Quiet chat from many viewers, then 40 messages in 12 s: one spammer, or 40 different viewers."""
    quiet = [_msg(float(i * 10), author=f"u{i}") for i in range(50)]
    burst = [_msg(300.0 + i * 0.3, author=f"b{i}" if distinct else "spammer") for i in range(40)]
    return quiet + burst


def test_density_author_cap_ignores_one_spammer():
    near = lambda scores: [t for t, _ in scores if 290 <= t <= 320]  # noqa: E731
    assert near(compute_density_scores(_spam_chat(False), window_s=15, peak_multiplier=4.0))  # no cap: as before
    assert not near(compute_density_scores(_spam_chat(False), window_s=15, peak_multiplier=4.0, author_cap=3.0))
    assert near(compute_density_scores(_spam_chat(True), window_s=15, peak_multiplier=4.0, author_cap=3.0))


def _ramp(levels: list[int], start: float) -> list[ChatMessage]:
    """levels[k] messages from new viewers in the k-th 15 s window after start."""
    return [
        _msg(start + 15 * k + n * 15 / level, author=f"r{k}_{n}")
        for k, level in enumerate(levels)
        for n in range(level)
    ]


def test_density_surge_gain_favours_a_sudden_jump():
    quiet = [_msg(float(i * 5), author="a") for i in range(120)]  # 3 messages per 15 s for 10 minutes
    sudden = quiet + _ramp([8], 600.0)
    slow = quiet + _ramp([4, 5, 6, 7, 8], 600.0)  # the same level, reached step by step
    assert compute_density_scores(sudden, window_s=15, peak_multiplier=3.0) == []
    assert compute_density_scores(sudden, window_s=15, peak_multiplier=3.0, surge_gain=0.5)
    assert compute_density_scores(slow, window_s=15, peak_multiplier=3.0, surge_gain=0.5) == []


def test_builder_uses_the_author_cap_from_config():
    chat = _chat(_spam_chat(False))
    sources = lambda cfg: {s for c in build_candidates(chat, [], cfg) for s in c.sources}  # noqa: E731
    assert "chat_density" in sources(_cfg())
    assert "chat_density" not in sources(_cfg(density_author_cap=3.0))


# ── deduplication ────────────────────────────────────────────────────────────


def test_dedupe_merges_close_events():
    raw = [
        (100.0, 10.0, "chat_density", "spike at 100"),
        (110.0, 20.0, "clip_command", "!clip at 110"),
        (115.0, 5.0, "chat_density", "spike at 115"),
        (300.0, 15.0, "existing_clip", "clip at 300"),
    ]
    result = _dedupe(raw, window_s=60)
    assert len(result) == 2
    times = {c.t_center for c in result}
    assert any(abs(t - 110.0) < 1 for t in times)
    assert any(abs(t - 300.0) < 1 for t in times)


def test_dedupe_accumulates_scores():
    raw = [
        (50.0, 10.0, "chat_density", "a"),
        (55.0, 20.0, "clip_command", "b"),
    ]
    result = _dedupe(raw, window_s=60)
    assert len(result) == 1
    assert result[0].score == pytest.approx(30.0)
    assert set(result[0].sources) == {"chat_density", "clip_command"}


def test_dedupe_preserves_distinct_events():
    raw = [(float(i * 100), 5.0, "chat_density", f"t={i * 100}") for i in range(5)]
    result = _dedupe(raw, window_s=60)
    assert len(result) == 5


# ── build_candidates ──────────────────────────────────────────────────────────


def test_existing_clip_gets_boost():
    cfg = _cfg(existing_clip_boost=100.0, clip_command_boost=25.0, density_peak_multiplier=99.0)
    chat = _chat([_msg(float(i * 5)) for i in range(20)])
    clips = [_clip(offset=300, clip_id="c1", views=500)]

    candidates = build_candidates(chat, clips, cfg)
    assert candidates, "expected at least one candidate"
    top = candidates[0]
    assert top.t_center == pytest.approx(300.0)
    assert "existing_clip" in top.sources
    assert 1.0 <= top.score <= 100.0


def test_clip_command_creates_candidate():
    cfg = _cfg(density_peak_multiplier=99.0, clip_command_boost=25.0)
    msgs = [_msg(float(i * 10)) for i in range(30)]
    msgs.append(_msg(150.0, "!clip this", "viewer1"))
    chat = _chat(msgs)

    candidates = build_candidates(chat, [], cfg)
    clip_cmd_candidates = [c for c in candidates if "clip_command" in c.sources]
    assert clip_cmd_candidates, "!clip should create a candidate"
    assert any(abs(c.t_center - 150.0) < 1 for c in clip_cmd_candidates)


def test_candidates_sorted_by_score_descending():
    cfg = _cfg(existing_clip_boost=100.0, clip_command_boost=25.0, density_peak_multiplier=99.0)
    msgs = [_msg(float(i * 10)) for i in range(30)]
    msgs.append(_msg(100.0, "!clip", "a"))
    msgs.append(_msg(200.0, "!clip", "b"))
    chat = _chat(msgs)
    clips = [_clip(offset=200, clip_id="c1")]

    candidates = build_candidates(chat, clips, cfg)
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_no_candidates_for_completely_empty_data():
    cfg = _cfg()
    candidates = build_candidates(_chat([]), [], cfg)
    assert candidates == []


# ── transcript hype signal ────────────────────────────────────────────────────


def test_transcript_hype_keyword_creates_candidate():
    cfg = _cfg(density_peak_multiplier=99.0)
    tr = _transcript([(120.0, 123.0, " jajaja qué bueno!")])
    candidates = build_candidates(_chat([]), [], cfg, transcript=tr)
    hype = [c for c in candidates if "transcript_hype" in c.sources]
    assert hype, "hype keyword should create a candidate"
    assert any(abs(c.t_center - 121.5) < 2 for c in hype)


def test_transcript_hype_exclamation_scores():
    cfg = _cfg(transcript_hype_score=12.0)
    tr = _transcript([(50.0, 52.0, "¡increíble!")])
    candidates = build_candidates(_chat([]), [], cfg, transcript=tr)
    hype = [c for c in candidates if "transcript_hype" in c.sources]
    assert hype
    assert hype[0].score > 0


def test_transcript_no_hype_no_candidate():
    cfg = _cfg(density_peak_multiplier=99.0)
    tr = _transcript([(10.0, 12.0, "bien jugado"), (20.0, 22.0, "seguimos")])
    candidates = build_candidates(_chat([]), [], cfg, transcript=tr)
    hype = [c for c in candidates if "transcript_hype" in c.sources]
    assert not hype


def test_transcript_hype_merges_with_clip_command():
    cfg = _cfg(clip_command_boost=25.0, transcript_hype_score=12.0, density_peak_multiplier=99.0)
    msgs = [_msg(100.0, "!clip")]
    tr = _transcript([(101.0, 103.0, "jajaja wow")])
    candidates = build_candidates(_chat(msgs), [], cfg, transcript=tr)
    merged = [
        c for c in candidates if "clip_command" in c.sources and "transcript_hype" in c.sources
    ]
    assert merged, "clip_command and nearby transcript_hype should merge"
    assert merged[0].score > 25.0


# ── reaction words in chat (off by default) ──────────────────────────────────


def _reactions_chat() -> list[ChatMessage]:
    # Steady chat, then a burst at 100-110 s where half the messages react.
    quiet = [_msg(float(t), "hey") for t in range(0, 200, 4)]
    burst = [
        _msg(100.0 + i * 0.5, text)
        for i, text in enumerate(["LMAO", "no way!!", "W", "omg", "ok"] * 2)
    ]
    return sorted(quiet + burst, key=lambda m: m.time_in_seconds)


def test_reaction_words_are_off_by_default() -> None:
    msgs = _reactions_chat()
    assert CandidatesConfig().reaction_words == [] and CandidatesConfig().reaction_weight == 0.0
    assert compute_density_scores(msgs, window_s=10, peak_multiplier=2.0) == compute_density_scores(
        msgs, window_s=10, peak_multiplier=2.0, reaction_words=["lmao", "w"], reaction_weight=0.0
    )


def test_reaction_words_raise_the_score_as_whole_words_ignoring_case() -> None:
    words = ["lmao", "no way", "w", "omg", "??"]
    plain = dict(compute_density_scores(_reactions_chat(), window_s=10, peak_multiplier=2.0))
    loud = dict(
        compute_density_scores(
            _reactions_chat(),
            window_s=10,
            peak_multiplier=2.0,
            reaction_words=words,
            reaction_weight=1.0,
        )
    )
    best = max(plain, key=plain.get)
    assert loud[best] == plain[best] + 8  # LMAO, no way, W, omg twice; "ok" is not a reaction
    # "w" is a whole word: "wow" and "what" do not count, "W" and "w!" do. "??" counts inside "what??".
    msgs = [
        _msg(0.0, "wow"),
        _msg(1.0, "what"),
        _msg(2.0, "W"),
        _msg(3.0, "w!"),
        _msg(4.0, "what??"),
    ]
    off = compute_density_scores(msgs, window_s=10, peak_multiplier=0.0)[0][1]
    on = compute_density_scores(
        msgs, window_s=10, peak_multiplier=0.0, reaction_words=words, reaction_weight=1.0
    )
    assert on[0][1] == off + 3


def test_build_candidates_uses_the_reaction_words_from_the_config() -> None:
    cfg = CandidatesConfig(
        density_window_s=10,
        density_peak_multiplier=2.0,
        audio_energy_enabled=False,
        reaction_words=["lmao", "no way", "w", "omg"],
        reaction_weight=1.0,
    )
    with_words = build_candidates(_chat(_reactions_chat()), [], cfg)
    without = build_candidates(
        _chat(_reactions_chat()), [], cfg.model_copy(update={"reaction_weight": 0.0})
    )
    assert [c.t_center for c in with_words] == [c.t_center for c in without]
    assert "reactions" in " ".join(with_words[0].reasons)


def test_common_reactions_do_not_lower_the_bar_for_the_whole_stream() -> None:
    # Every message all stream long is "lol": no window stands out, with or without reaction words.
    msgs = [_msg(float(t), "lol") for t in range(0, 600, 2)]
    assert (
        compute_density_scores(
            msgs, window_s=10, peak_multiplier=1.5, reaction_words=["lol"], reaction_weight=1.0
        )
        == []
    )


def test_spam_with_repeated_letters_counts_as_a_reaction() -> None:
    words = ["w", "haha", "lmao", "lol"]
    spam = ["WWWWWW", "wWw", "ahahah", "hahaha", "LMAOOOO", "lolll"]
    msgs = [_msg(float(i), text) for i, text in enumerate([*spam, "wow", "what", "hello"])]
    off = compute_density_scores(msgs, window_s=20, peak_multiplier=0.0)[0][1]
    on = compute_density_scores(
        msgs, window_s=20, peak_multiplier=0.0, reaction_words=words, reaction_weight=1.0
    )
    assert on[0][1] == off + len(spam)


def test_density_scores_do_not_depend_on_the_message_order() -> None:
    msgs = _reactions_chat()
    kwargs = {
        "window_s": 10,
        "peak_multiplier": 2.0,
        "reaction_words": ["lmao", "w"],
        "reaction_weight": 1.0,
    }
    assert compute_density_scores(msgs[::-1], **kwargs) == compute_density_scores(msgs, **kwargs)
