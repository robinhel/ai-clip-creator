"""Tests for rendering.captions — ASS subtitle generation."""

from __future__ import annotations

import itertools
import re

import pytest
from helpers import _seg, _transcript, _word

from clipsmith.models.transcript import Word
from clipsmith.rendering.captions import (
    _ass_time,
    _caption_lines,
    _render_ass,
    _write_ass,
)
from clipsmith.settings import CaptionConfig, ReframeConfig


def _cfg(**kw: object) -> CaptionConfig:
    defaults: dict[str, object] = {
        "font": "Arial",
        "font_size": 72,
        "outline": 3,
        "position": "bottom",
        "style": "outline",
    }
    defaults.update(kw)
    return CaptionConfig(**defaults)  # type: ignore[arg-type]


# ── _ass_time ─────────────────────────────────────────────────────────────────


def test_ass_time_zero() -> None:
    assert _ass_time(0.0) == "0:00:00.00"


def test_ass_time_one_second() -> None:
    assert _ass_time(1.0) == "0:00:01.00"


def test_ass_time_half_second() -> None:
    assert _ass_time(0.5) == "0:00:00.50"


def test_ass_time_minutes() -> None:
    assert _ass_time(90.25) == "0:01:30.25"


def test_ass_time_hours() -> None:
    assert _ass_time(3661.0) == "1:01:01.00"


def test_ass_time_clamps_negative() -> None:
    assert _ass_time(-3.0) == "0:00:00.00"


# ── _caption_lines ────────────────────────────────────────────────────────────


def test_caption_lines_uses_word_timestamps() -> None:
    words = [
        _word(300.0, 300.4, "Hola"),
        _word(300.5, 300.9, "mundo"),
    ]
    t = _transcript(_seg(300.0, 301.0, "Hola mundo", words))
    lines = _caption_lines(t, 300.0, 310.0)
    assert len(lines) >= 1
    for start, end, text in lines:
        assert start >= 0.0
        assert end > start


def test_caption_lines_relative_timestamps() -> None:
    words = [_word(305.0, 305.5, "Okay")]
    t = _transcript(_seg(305.0, 305.5, "Okay", words))
    lines = _caption_lines(t, 300.0, 310.0)
    assert lines[0][0] == pytest.approx(5.0, abs=0.01)


def test_caption_lines_karaoke_tags_present() -> None:
    words = [_word(300.0, 300.5, "Hola"), _word(300.6, 301.0, "mundo")]
    t = _transcript(_seg(300.0, 301.0, "Hola mundo", words))
    lines = _caption_lines(t, 300.0, 310.0)
    combined = " ".join(text for _, _, text in lines)
    assert r"{\kf" in combined


def test_caption_lines_excludes_out_of_range() -> None:
    words = [_word(100.0, 100.5, "antes")]
    t = _transcript(_seg(100.0, 100.5, "antes", words))
    lines = _caption_lines(t, 300.0, 330.0)
    assert lines == []


def test_caption_lines_fallback_no_words() -> None:
    t = _transcript(_seg(300.0, 303.0, "Hola mundo que tal esto"))
    lines = _caption_lines(t, 300.0, 310.0)
    assert len(lines) >= 1
    for start, end, _ in lines:
        assert 0.0 <= start
        assert end <= 10.0 + 0.1


def test_caption_lines_empty_transcript() -> None:
    t = _transcript()
    assert _caption_lines(t, 300.0, 310.0) == []


def test_caption_lines_groups_long_word_runs() -> None:
    words = [_word(300.0 + i * 0.5, 300.3 + i * 0.5, f"w{i}") for i in range(12)]
    t = _transcript(_seg(300.0, 306.0, " ".join(w.word for w in words), words))
    lines = _caption_lines(t, 300.0, 310.0)
    assert len(lines) >= 3


def _karaoke_cs(text: str) -> int:
    import re

    return sum(int(k) for k in re.findall(r"\\kf(\d+)", text))


def test_caption_lines_pause_splits_the_line() -> None:
    # Two words, then 2.3 s of silence, then two more: the text must not stay up during the pause.
    words = [
        _word(300.0, 300.3, "Allow"),
        _word(300.4, 300.7, "me."),
        _word(303.0, 303.3, "On"),
        _word(303.4, 303.7, "right."),
    ]
    t = _transcript(_seg(300.0, 303.7, "Allow me. On right.", words))
    lines = _caption_lines(t, 300.0, 310.0)
    assert len(lines) == 2
    assert lines[0][1] <= 1.1
    assert lines[1][0] == pytest.approx(3.0, abs=0.01)


def test_caption_lines_caps_a_stretched_word() -> None:
    # Whisper sometimes stretches a word over the silence after it (seen: 12.7 s for one word).
    words = [_word(300.0, 300.3, "No"), _word(300.4, 310.0, "way!")]
    t = _transcript(_seg(300.0, 310.0, "No way!", words))
    lines = _caption_lines(t, 300.0, 315.0)
    assert len(lines) == 1
    assert lines[0][1] <= 2.0


def test_caption_lines_karaoke_follows_the_speech_inside_a_line() -> None:
    # A short gap inside a line belongs to the word before it, so the highlight keeps pace.
    words = [_word(300.0, 300.3, "Hola"), _word(300.5, 300.8, "mundo")]
    t = _transcript(_seg(300.0, 300.8, "Hola mundo", words))
    ((_, _, text),) = _caption_lines(t, 300.0, 310.0)
    assert _karaoke_cs(text) == 80


def test_caption_lines_do_not_overlap() -> None:
    words = [_word(300.0 + i * 0.35, 300.3 + i * 0.35, f"w{i}") for i in range(10)]
    t = _transcript(_seg(300.0, 304.0, " ".join(w.word for w in words), words))
    lines = _caption_lines(t, 300.0, 310.0)
    for (_, end, _), (next_start, _, _) in itertools.pairwise(lines):
        assert end <= next_start + 1e-9


# ── _render_ass ───────────────────────────────────────────────────────────────


def test_render_ass_has_sections() -> None:
    ass = _render_ass([], _cfg())
    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass


def test_render_ass_uses_configured_font() -> None:
    ass = _render_ass([], _cfg(font="Impact"))
    assert "Impact" in ass


def test_render_ass_bottom_alignment() -> None:
    ass = _render_ass([], _cfg(position="bottom"))
    assert ",2," in ass


def test_render_ass_top_alignment() -> None:
    ass = _render_ass([], _cfg(position="top"))
    assert ",8," in ass


def test_render_ass_dialogue_lines() -> None:
    lines = [(0.0, 1.5, "Hola"), (2.0, 3.5, "Mundo")]
    ass = _render_ass(lines, _cfg())
    assert ass.count("Dialogue:") == 2
    assert "Hola" in ass
    assert "Mundo" in ass


def test_render_ass_playres() -> None:
    ass = _render_ass([], _cfg())
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass


# ── style "box" och säker zon ─────────────────────────────────────────────────

_ON = "{\\3a&H00&}"


def _four_words() -> list[tuple[float, float, str]]:
    words = [
        _word(300.0, 300.3, "no"),
        _word(300.4, 300.7, "way"),
        _word(300.8, 301.1, "bro"),
        _word(301.2, 301.5, "what"),
    ]
    t = _transcript(_seg(300.0, 301.5, "no way bro what", words))
    return _caption_lines(t, 300.0, 310.0, style="box")


def test_caption_config_defaults_to_box_style() -> None:
    cfg = CaptionConfig()
    assert cfg.style == "box"
    assert cfg.box_colour.startswith("&H")


def test_box_lines_have_at_most_three_words_in_capitals() -> None:
    for _, _, text in _four_words():
        plain = text.replace(_ON, "").replace("{\\3a&HFF&}", "")
        assert len(plain.split()) <= 3
        assert plain == plain.upper()


def test_box_highlights_one_word_per_event_in_spoken_order() -> None:
    lines = _four_words()
    active = [t.split(_ON)[1].split("{")[0] for _, _, t in lines]
    assert active == ["NO", "WAY", "BRO", "WHAT"]
    assert all(t.count(_ON) == 1 for _, _, t in lines)
    assert lines[1][0] == pytest.approx(0.4, abs=0.01)  # the box moves when the word is said


def test_box_events_do_not_overlap() -> None:
    lines = _four_words()
    for (_, e1, _), (s2, _, _) in itertools.pairwise(lines):
        assert e1 <= s2 + 1e-9


def test_render_box_has_box_layer_and_clean_text_layer() -> None:
    ass = _render_ass(
        [(0.0, 1.0, f"NO {_ON}WAY{{\\3a&HFF&}} BRO")], _cfg(style="box", box_colour="&H0000D7FF")
    )
    assert "Style: Box," in ass and ",3," in ass.split("Style: Box,")[1].split("\n")[0]
    assert "&H0000D7FF" in ass
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogues) == 2
    text_layer = next(d for d in dialogues if ",Text," in d)
    assert text_layer.endswith(",NO WAY BRO")


def _margin_v(ass: str) -> int:
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style:"))
    return int(style.split(",")[-2])


def test_box_captions_sit_above_the_bottom_safe_zone() -> None:
    assert _margin_v(_render_ass([], _cfg(style="box"))) == int(1920 * 0.20)


def test_outline_captions_keep_the_old_placement() -> None:
    assert _margin_v(_render_ass([], _cfg())) == 80
    stacked = ReframeConfig(mode="stacked", split_ratio=0.4)
    assert _margin_v(_render_ass([], _cfg(), stacked)) == 80


def test_stacked_captions_sit_on_the_seam() -> None:
    reframe = ReframeConfig(mode="stacked", split_ratio=0.4)
    ass = _render_ass([], _cfg(style="box", font_size=72), reframe)
    # bottom-aligned: the line's middle lands on the seam at 1920 * 0.4
    assert _margin_v(ass) == int(1920 * 0.6 - 72 / 2)
    assert ",2," in ass


def test_write_ass_uses_reframe_for_placement(tmp_path) -> None:
    words = [_word(300.0, 300.3, "hej")]
    t = _transcript(_seg(300.0, 300.3, "hej", words))
    out = tmp_path / "c.ass"
    _write_ass(
        t, 300.0, 310.0, _cfg(style="box"), out, ReframeConfig(mode="stacked", split_ratio=0.5)
    )
    ass = out.read_text(encoding="utf-8-sig")
    assert _margin_v(ass) == int(1920 * 0.5 - 72 / 2)
    assert "HEJ" in ass


def _plain_lines(lines: list[tuple[float, float, str]]) -> list[str]:
    return list(dict.fromkeys(re.sub(r"{[^}]*}", "", t) for _, _, t in lines))


def test_box_lines_fit_the_width_and_a_long_word_stands_alone() -> None:
    words = [
        _word(300.0, 300.3, "unbelievable"),
        _word(300.4, 300.7, "absolutely"),
        _word(300.8, 301.1, "a"),
        _word(301.2, 301.5, "supercalifragilistic"),
        _word(301.6, 301.9, "b"),
    ]
    t = _transcript(_seg(300.0, 301.9, "x", words))
    lines = _plain_lines(_caption_lines(t, 300.0, 310.0, style="box"))
    assert lines == ["UNBELIEVABLE", "ABSOLUTELY A", "SUPERCALIFRAGILISTIC", "B"]


@pytest.mark.parametrize("style", ["box", "outline"])
def test_braces_and_backslashes_are_removed_from_words(style: str) -> None:
    t = _transcript(_seg(300.0, 300.3, "x", [_word(300.0, 300.3, "a{b}\\c")]))
    text = _caption_lines(t, 300.0, 310.0, style=style)[0][2]
    assert re.sub(r"{\\(kf|3a)[^}]*}", "", text).upper() == "ABC"
    fallback = _caption_lines(_transcript(_seg(300.0, 301.0, "a{b}\\c")), 300.0, 310.0, style=style)
    assert fallback[0][2].upper() == "ABC"


def test_box_colour_must_be_an_ass_colour() -> None:
    from pydantic import ValidationError

    assert CaptionConfig(box_colour="&H0000d7ff").box_colour == "&H0000d7ff"
    with pytest.raises(ValidationError):
        CaptionConfig(box_colour="&H00FF}{\\b1")


# ── Whisper's music hallucinations ────────────────────────────────────────────
# Real cases from Blizzard trailers (15 Nov 2026): over pure music Whisper wrote " You" with 9 %
# certainty, and in a real voice-over it heard the true word "thunder." with only 6 %.


def _unsure(start: float, end: float, text: str, probability: float) -> Word:
    return Word(start=start, end=end, word=text, probability=probability)


def test_unsure_you_over_music_gives_no_caption() -> None:
    t = _transcript(_seg(302.5, 303.2, " You", [_unsure(302.5, 303.2, " You", 0.09)]))
    assert _caption_lines(t, 300.0, 310.0) == []
    assert _caption_lines(t, 300.0, 310.0, "box") == []


def test_unsure_thanks_for_watching_gives_no_caption() -> None:
    words = [
        _unsure(301.0, 301.3, " Thanks", 0.2),
        _unsure(301.3, 301.5, " for", 0.4),
        _unsure(301.5, 302.0, " watching!", 0.3),
    ]
    t = _transcript(_seg(301.0, 302.0, " Thanks for watching!", words))
    assert _caption_lines(t, 300.0, 310.0) == []


def test_hallucination_is_dropped_but_real_speech_next_to_it_stays() -> None:
    t = _transcript(
        _seg(301.0, 301.5, " You", [_unsure(301.0, 301.5, " You", 0.09)]),
        _seg(305.0, 305.5, " thunder.", [_unsure(305.0, 305.5, " thunder.", 0.06)]),
    )
    text = " ".join(line[2] for line in _caption_lines(t, 300.0, 310.0))
    assert "thunder." in text
    assert "You" not in text


def test_sure_thank_you_is_real_speech_and_stays() -> None:
    words = [_unsure(301.0, 301.3, " Thank", 0.95), _unsure(301.3, 301.6, " you.", 0.9)]
    t = _transcript(_seg(301.0, 301.6, " Thank you.", words))
    text = " ".join(line[2] for line in _caption_lines(t, 300.0, 310.0))
    assert "Thank" in text and "you." in text
