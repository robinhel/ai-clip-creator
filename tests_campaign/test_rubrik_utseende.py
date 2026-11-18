"""Rubriken över klippet: vit text på ett svart fält hela klippet, i stilen sans eller serif.
Som kampanjens exempelklipp. Inga riktiga anrop till Twitch, Claude, TikTok eller YouTube."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from campaign import finish
from campaign import profile_edit as edit
from campaign.finish import HOOK_FONT_SIZE, build_filter, top_text_end
from campaign.profile import Profile, load_profile
from clipsmith.rendering.clipper import ZOOM_TOP

from .conftest import PAKET, needs_ffmpeg
from .test_profiles_api import client, twitch  # noqa: F401  (fixtures)

FONTS = Path(finish.__file__).parent / "fonts"
SANS, SERIF = FONTS / "Montserrat-ExtraBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf"
EXAMPLES = [
    "World of Warcraft: Forever looks beautiful.",
    "World of Warcraft: Forever is a visual masterpiece!",
    "This WoW world is beautiful beyond words!",
]
# Rader som är bredast för sina tecken, uppmätta i ffmpeg (1113-1154 px på en rad i sans).
WIDE = [
    "WORLD OF WARCRAFT: FOREVER",
    "Midsummer Warmonger Moment",
    "MOMMY, MY WORGEN WARRIOR",
    "BEAUTIFUL BEYOND WORDS!",
    "Mammoth Warrior Wows Wild",
]
MAX_LINE_PX = 960
TABLE = json.loads((FONTS / "widths.json").read_text(encoding="utf-8"))


def _profile(style: str = "sans", **changes) -> Profile:
    return Profile(
        name="x", streamer="y", hook_text=True, hook_style=style, safe_top=0.125, **changes
    )


def _filters(graph: str, name: str) -> list[tuple[dict[str, str], str]]:
    """(alternativen, utgångens etikett) för varje filter med namnet, i vilken ordning alternativen än står."""
    found = []
    for part in re.split(r"(?<!\\);", graph):
        match = re.fullmatch(rf"\[[^\]]*\]{name}=(.*)\[(\w+)\]", part)
        if match:
            options = re.split(r"(?<!\\):", match.group(1))
            found.append((dict(o.split("=", 1) for o in options), match.group(2)))
    return found


def _hook_texts(graph: str) -> list[tuple[str, str, int]]:
    """(fontfil, text, y) för varje rad i rubriken, uppifrån. Varje rad är vit, centrerad och ordagrann."""
    rows = []
    for options, out in _filters(graph, "drawtext"):
        if out == "withhook" or re.fullmatch(r"hook\d+", out):
            assert options["expansion"] == "none" and options["x"] == "(w-text_w)/2"
            assert (options["fontsize"], options["fontcolor"]) == (str(HOOK_FONT_SIZE), "white")
            font = re.sub(r"\\(.)", r"\1", options["fontfile"].strip("'"))
            rows.append((font, options["text"], int(options["y"])))
    return sorted(rows, key=lambda row: row[2])


def _bars(graph: str) -> list[tuple[int, int]]:
    """(y, höjd) för varje svart fält i full bredd."""
    return [
        (int(o["y"]), int(o["h"]))
        for o, _out in _filters(graph, "drawbox")
        if (o["x"], o["w"], o["color"], o["t"]) == ("0", "iw", "black", "fill")
    ]


def _table_px(fonts: list[str], line: str) -> float:
    """Radens bredd vid HOOK_FONT_SIZE enligt tabellen. Okänt tecken räknas som fontens bredaste; med flera
    fonter räknas det bredaste värdet per tecken."""

    def char(name: str, ch: str) -> float:
        font = TABLE[name]
        widths = font["widths"]
        return widths.get(ch, max(widths.values())) * HOOK_FONT_SIZE / font["units_per_em"]

    return sum(max(char(name, ch) for name in fonts) for ch in line)


# ---------- R7: stilen ----------


def test_the_style_is_read_from_the_profile_and_sans_is_the_default(tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump({"name": "P", "streamer": "s", "on_screen_text": {"hook_style": "serif"}})
    )
    assert load_profile(path, tmp_path).hook_style == "serif"
    path.write_text(yaml.safe_dump({"name": "P", "streamer": "s"}))
    assert load_profile(path, tmp_path).hook_style == "sans"
    assert Profile(name="x", streamer="y").hook_style == "sans"


def test_hook_style_null_gives_sans(tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump({"name": "P", "streamer": "s", "on_screen_text": {"hook_style": None}})
    )
    profile = load_profile(path, tmp_path)
    assert profile.hook_style == "sans"
    assert not any("hook_style" in p for p in profile.problems(tmp_path))


def test_an_unknown_style_is_named_in_the_problems(tmp_path) -> None:
    assert any("hook_style" in p for p in _profile("comic").problems(tmp_path))
    assert not any("hook_style" in p for p in _profile("serif").problems(tmp_path))


@pytest.mark.parametrize(
    "style, font, text", [("sans", SANS, "Huge clutch"), ("serif", SERIF, "HUGE CLUTCH")]
)
def test_the_style_picks_the_font_and_serif_is_in_capitals(
    tmp_path, monkeypatch, style, font, text
) -> None:
    monkeypatch.chdir(tmp_path)  # fonten hittas oavsett arbetsmapp
    ((used, line, _y),) = _hook_texts(
        build_filter(_profile(style), False, True, None, title="Huge clutch")
    )
    assert Path(used) == font and Path(used).is_absolute() and Path(used).is_file()
    assert line == text


def _finish_graph(
    tmp_path, monkeypatch, profile: Profile, font_file: str | None, title: str = "Big play"
) -> str:
    seen: list = []
    monkeypatch.setattr(finish, "run", seen.append)
    monkeypatch.setattr(finish, "probe", lambda path: {"has_audio": True, "duration": 9.0})
    finish.finish_clip(
        tmp_path / "src.mp4",
        tmp_path / "out.mp4",
        profile,
        tmp_path,
        title=title,
        font_file=font_file,
    )
    (cmd,) = seen
    return cmd[cmd.index("-filter_complex") + 1]


@pytest.mark.parametrize("style, text", [("sans", "Big play"), ("serif", "BIG PLAY")])
def test_an_own_font_file_is_used_for_the_title_and_only_serif_gives_capitals(
    tmp_path, monkeypatch, style, text
) -> None:
    graph = _finish_graph(tmp_path, monkeypatch, _profile(style), "/fonts/Impact.ttf")
    assert _hook_texts(graph) == [("/fonts/Impact.ttf", text, 240)]  # 0,125 × 1920


def test_the_campaigns_white_text_keeps_its_font(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(finish, "find_font", lambda preferred=None: "/fonts/Arial.ttf")
    graph = _finish_graph(tmp_path, monkeypatch, _profile("serif", text="twitch.tv/y"), None)
    (white,) = [o for o, _out in _filters(graph, "drawtext") if o["text"] == "'twitch.tv/y'"]
    assert white["fontfile"] == "'/fonts/Arial.ttf'"
    assert [Path(font) for font, _text, _y in _hook_texts(graph)] == [SERIF]


# ---------- R8: svart fält ----------


@pytest.mark.parametrize("frame", ["fit", "zoom"])
@pytest.mark.parametrize(
    "title", ["Galet skott", EXAMPLES[1], "This boss fight went completely sideways for everyone"]
)
def test_outside_zoom_a_black_full_width_bar_lies_behind_the_title(tmp_path, frame, title) -> None:
    profile = _profile()
    graph = build_filter(profile, False, True, None, title=title, frame=frame)
    rows = [y for _font, _text, y in _hook_texts(graph)]
    end = top_text_end(profile, tmp_path, title, frame=frame)  # klistermärket läggs under
    if frame == "zoom":  # bakgrunden ovanför bilden är redan svart
        assert not _bars(graph) and rows and rows[-1] + HOOK_FONT_SIZE <= end
        return
    ((bar_y, bar_h),) = _bars(graph)
    assert graph.index("drawbox") < graph.index("drawtext")  # fältet under texten
    assert bar_y < rows[0] and rows[-1] + HOOK_FONT_SIZE < bar_y + bar_h  # luft runt texten
    assert bar_y + bar_h <= end


def test_no_title_gives_no_bar() -> None:
    assert "drawbox" not in build_filter(_profile(), False, True, None, title="", frame="fit")
    no_hook = Profile(name="x", streamer="y", hook_text=False)
    assert "drawbox" not in build_filter(
        no_hook, False, True, None, title="Galet skott", frame="fit"
    )


# ---------- Stacked: rubriken bara de första sekunderna, utan fält ----------
# I stacked ligger facecam överst. Ett fält hela klippet skulle täcka ansiktena, så rubriken ritas som före
# fältet: svart kant runt bokstäverna och bara de första 3 sekunderna.

STACKED_TITLE = "Ett galet skott från andra sidan banan"


def _hook_options(graph: str) -> list[dict[str, str]]:
    return [o for o, out in _filters(graph, "drawtext") if out == "withhook" or re.fullmatch(r"hook\d+", out)]


@pytest.mark.parametrize("style", ["sans", "serif"])
def test_in_stacked_the_title_has_a_black_border_for_three_seconds_and_no_bar(tmp_path, style) -> None:
    profile = _profile(style)
    graph = build_filter(profile, False, True, None, title=STACKED_TITLE, frame="stacked")
    assert "drawbox" not in graph
    options = _hook_options(graph)
    assert options and len(_hook_texts(graph)) == len(options)
    for o in options:
        assert o["enable"] == "'lt(t,3)'"
        assert (o["borderw"], o["bordercolor"]) == ("5", "black@0.9")
    rows = [y for _font, _text, y in _hook_texts(graph)]
    assert rows[0] == int(1920 * profile.safe_top)  # samma plats som i fit
    assert rows[-1] + HOOK_FONT_SIZE <= top_text_end(profile, tmp_path, STACKED_TITLE, frame="stacked")


@pytest.mark.parametrize("frame", ["fit", "center"])
def test_outside_stacked_and_zoom_the_title_keeps_the_bar_the_whole_clip(frame) -> None:
    graph = build_filter(_profile(), False, True, None, title=STACKED_TITLE, frame=frame)
    assert len(_bars(graph)) == 1
    assert "enable=" not in graph and "borderw=5" not in graph


def test_in_stacked_hook_text_false_gives_no_title() -> None:
    no_hook = Profile(name="x", streamer="y", hook_text=False)
    graph = build_filter(no_hook, False, True, None, title=STACKED_TITLE, frame="stacked")
    assert "drawbox" not in graph and "drawtext" not in graph


# ---------- R9: rader ----------


@pytest.mark.parametrize("style", ["sans", "serif"])
@pytest.mark.parametrize(
    "title", [*EXAMPLES, "This boss fight went completely sideways for everyone"]
)
def test_in_zoom_with_the_new_safe_zone_the_title_has_two_lines_above_the_picture(
    style, title
) -> None:
    profile = _profile(style)
    rows = [
        y
        for _font, _text, y in _hook_texts(
            build_filter(profile, False, True, None, title=title, frame="zoom")
        )
    ]
    assert 1 <= len(rows) <= 2
    assert rows[0] >= int(1920 * profile.safe_top)
    assert rows[-1] + HOOK_FONT_SIZE <= ZOOM_TOP


@pytest.mark.parametrize("style", ["sans", "serif"])
@pytest.mark.parametrize("title", EXAMPLES)
def test_the_examples_fit_without_cutting(style, title) -> None:
    # "WORLD OF WARCRAFT: FOREVER IS A VISUAL MASTERPIECE!" (51 tecken) är cirka 1750 px i serif och
    # ryms inte på två rader om 960 px. Det får tre rader utanför zoom; i zoom ryms två och raden kapas.
    cut_in_zoom = style == "serif" and title == EXAMPLES[1]
    for frame in ("fit",) if cut_in_zoom else ("zoom", "fit"):
        texts = [
            text
            for _f, text, _y in _hook_texts(
                build_filter(_profile(style), False, True, None, title=title, frame=frame)
            )
        ]
        assert len(texts) <= (3 if cut_in_zoom else 2) and "…" not in "".join(texts)
        assert " ".join(texts).replace("\\", "") == (title.upper() if style == "serif" else title)


@pytest.mark.parametrize("style, font", [("sans", SANS.name), ("serif", SERIF.name)])
@pytest.mark.parametrize("title", [*WIDE, *EXAMPLES])
def test_every_line_is_at_most_960_px_by_the_table(style, font, title) -> None:
    lines = finish._hook_lines(_profile(style), title, frame="fit")
    assert lines and "…" not in "".join(lines)
    assert " ".join(lines) == (title.upper() if style == "serif" else title)
    for line in lines:
        assert _table_px([font], line) <= MAX_LINE_PX, line


@pytest.mark.parametrize("style", ["sans", "serif"])
def test_with_an_own_font_the_widest_of_both_tables_counts(tmp_path, monkeypatch, style) -> None:
    profile = _profile(style)
    for title in WIDE:
        graph = _finish_graph(tmp_path, monkeypatch, profile, "/f/I.ttf", title)
        for _font, text, _y in _hook_texts(graph):
            assert _table_px([SANS.name, SERIF.name], text.replace("\\", "")) <= MAX_LINE_PX, text


def test_an_unknown_character_counts_as_the_widest() -> None:
    title = "ЖЖЖЖЖЖЖЖЖЖ ЖЖЖЖЖЖЖЖЖЖ"  # två ord om tio okända tecken
    lines = finish._hook_lines(_profile(), title, frame="fit")
    assert lines == ["ЖЖЖЖЖЖЖЖЖЖ", "ЖЖЖЖЖЖЖЖЖЖ"]  # 2 × 10 × bredaste + blanksteg > 960


def test_a_word_wider_than_a_line_is_split() -> None:
    word = "Supercalifragilisticexpialidocious"
    lines = finish._hook_lines(_profile(), word, frame="fit")
    assert len(lines) >= 2 and "".join(lines) == word
    assert all(_table_px([SANS.name], line) <= MAX_LINE_PX for line in lines)


@pytest.mark.parametrize("frame, most", [("fit", 3), ("zoom", 2)])
def test_a_long_title_is_cut_with_an_ellipsis_within_the_width(frame, most) -> None:
    title = "This boss fight went completely sideways for everyone and nobody saw it coming at all"
    lines = finish._hook_lines(_profile(), title, frame=frame)
    assert len(lines) == most and lines[-1].endswith("…")
    assert all(_table_px([SANS.name], line) <= MAX_LINE_PX for line in lines)


@pytest.mark.parametrize(
    "title, lines",
    [
        ("Huge clutch 🔥🔥", ["Huge clutch"]),
        ("Love it ❤️ now", ["Love it now"]),  # ❤ U+2764 och U+FE0F
        ("So ✨ clean", ["So clean"]),  # dingbat U+2728
        ("Top 1️⃣ play", ["Top 1 play"]),  # keycap: U+FE0F och U+20E3
        ("Family 👨‍👩‍👧 night", ["Family night"]),  # ZWJ U+200D mellan emoji
        ("Up ↑ and ★ away ⬛", ["Up and away"]),  # pilar, symboler, U+2B1B
        ("🔥 😂", []),
        ("❤ ✨ ⬆", []),
    ],
)
def test_emoji_and_symbols_without_glyphs_are_removed_from_the_title(title, lines) -> None:
    assert finish._hook_lines(_profile(), title, frame="fit") == lines


def test_ordinary_characters_stay_in_the_title() -> None:
    title = "Åsa’s “ÄÖ” … – — & : ' ! ? åäö"
    assert finish.hook_clean(title) == title
    assert " ".join(finish._hook_lines(_profile(), title, frame="fit")) == title


def test_the_manifest_has_the_burned_title_without_emoji(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(finish, "run", lambda cmd: None)
    monkeypatch.setattr(finish, "probe", lambda path: {"has_audio": True, "duration": 9.0})
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    manifest = finish.finish_clip(src, out, _profile(), tmp_path, title="🔥 Big ✨ play")
    assert manifest["hook_text"] == "Big play"
    manifest = finish.finish_clip(src, out, _profile(), tmp_path, title="🔥🔥")
    assert manifest["hook_text"] is None


def _width(font: Path, text: str) -> int:
    """Hur bred raden blir i ffmpeg, i pixlar (bbox runt de vita pixlarna)."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=2000x200",
            "-frames:v",
            "1",
            "-vf",
            f"drawtext=fontfile='{font}':expansion=none:text={finish._hook_esc(text)}:fontsize={HOOK_FONT_SIZE}:"
            "fontcolor=white:x=10:y=20,bbox",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return int(re.findall(r" w:(\d+)", out)[-1])


@needs_ffmpeg
@pytest.mark.parametrize("style, font", [("sans", SANS), ("serif", SERIF)])
def test_no_example_line_is_wider_than_960_px(style, font) -> None:
    for title in [*EXAMPLES, *WIDE]:
        for line in finish._hook_lines(_profile(style), title, frame="zoom"):
            assert _width(font, line) <= MAX_LINE_PX, (style, line)


@needs_ffmpeg
def test_a_rendered_wide_title_does_not_reach_the_edges() -> None:
    """En riktig bild: på grå bakgrund är fältets kanter (20 px in från varje sida) svarta, och texten finns."""
    title = "WORLD OF WARCRAFT: FOREVER"
    graph = build_filter(_profile(), False, False, None, title=title, frame="fit")
    ((bar_y, bar_h),) = _bars(graph)
    cmd = "ffmpeg -v error -f lavfi -i color=gray:s=1080x1920 -filter_complex GRAPH -map [vout]"
    cmd += " -frames:v 1 -f rawvideo -pix_fmt gray -"
    args = [graph if arg == "GRAPH" else arg for arg in cmd.split()]
    frame = subprocess.run(args, capture_output=True, check=True).stdout
    rows = [frame[y * 1080 : (y + 1) * 1080] for y in range(bar_y, bar_y + bar_h)]
    assert max(max(row[:21]) for row in rows) < 40 and max(max(row[1060:]) for row in rows) < 40
    assert max(max(row) for row in rows) > 200  # vit text i fältet


# ---------- R10: spara stilen ----------


def test_the_style_is_saved_from_the_settings_and_read_back(client, settings) -> None:  # noqa: F811
    assert (
        client.get("/campaign/profiles/skiimasktimmy").json()["on_screen_text"]["hook_style"]
        == "sans"
    )
    response = client.put("/campaign/profiles/skiimasktimmy/settings", json={"hook_style": "serif"})
    assert response.status_code == 200, response.text
    assert response.json()["on_screen_text"]["hook_style"] == "serif"
    data = yaml.safe_load(
        (settings.profiles_dir / "skiimasktimmy.yaml").read_text(encoding="utf-8")
    )
    assert data["on_screen_text"]["hook_style"] == "serif"
    assert load_profile("skiimasktimmy", settings.profiles_dir).hook_style == "serif"


def test_an_unknown_style_is_refused_by_the_settings(client, settings) -> None:  # noqa: F811
    before = (settings.profiles_dir / "skiimasktimmy.yaml").read_bytes()
    assert (
        client.put(
            "/campaign/profiles/skiimasktimmy/settings", json={"hook_style": "comic"}
        ).status_code
        == 422
    )
    assert (settings.profiles_dir / "skiimasktimmy.yaml").read_bytes() == before


# ---------- R11: säkra zonen uppe ----------


# Timmy står kvar på 0,07: test_ny_version.py (en annan planerares) räknar med 0,07 i Timmys profil.
def test_blizzard_has_the_new_top_safe_zone(tmp_path) -> None:
    shutil.copy(PAKET / "profiles" / "blizzard_nordics.yaml", tmp_path)
    profile = load_profile("blizzard_nordics", tmp_path)
    assert (profile.safe_top, profile.safe_bottom) == (0.125, 0.20)


def test_a_new_campaign_gets_the_new_top_safe_zone() -> None:
    body = {
        "streamer": "Ny",
        "clip_min_s": 10,
        "clip_max_s": 30,
        "text_position": "top",
        "overlay_required": False,
        "overlay_mode": "full",
        "facecam_required": False,
    }
    data = yaml.safe_load(edit.new_profile_text("ny", body, "config.ny.yaml"))
    assert data["safe_zone"] == {"top": 0.125, "bottom": 0.20}
