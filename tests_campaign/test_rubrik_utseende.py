"""Rubriken över klippet: vit text på ett svart fält hela klippet, i stilen sans eller serif.
Som kampanjens exempelklipp. Inga riktiga anrop till Twitch, Claude, TikTok eller YouTube."""

from __future__ import annotations

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
NOT_ZOOM = ["", "fit", "stacked", "center", "webcam"]


def _profile(style: str = "sans", **changes) -> Profile:
    return Profile(
        name="x", streamer="y", hook_text=True, hook_style=style, safe_top=0.125, **changes
    )


def _hook_texts(graph: str) -> list[tuple[str, str, int]]:
    """(fontfil, text, y) för varje rad i rubriken."""
    return [
        (font, text, int(y))
        for font, text, y in re.findall(
            r"drawtext=fontfile='([^']*)':expansion=none:text=(.*?):x=\(w-text_w\)/2:y=(\d+):",
            graph,
        )
    ]


def _bars(graph: str) -> list[tuple[int, int]]:
    """(y, höjd) för varje svart fält i full bredd."""
    return [
        (int(y), int(h))
        for y, h in re.findall(r"drawbox=x=0:y=(\d+):w=iw:h=(\d+):color=black:t=fill", graph)
    ]


# ---------- R6: hela klippet ----------


def test_the_title_has_no_time_limit() -> None:
    graph = build_filter(_profile(), False, True, None, title="Huge clutch")
    assert "enable=" not in graph and "lt(t" not in graph
    assert not hasattr(finish, "HOOK_SECONDS")


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


def test_an_unknown_style_is_named_in_the_problems(tmp_path) -> None:
    assert any("hook_style" in p for p in _profile("comic").problems(tmp_path))
    assert not any("hook_style" in p for p in _profile("serif").problems(tmp_path))


def test_the_fonts_and_their_licences_are_in_the_package() -> None:
    for name in (
        "Montserrat-ExtraBold.ttf",
        "Montserrat-OFL.txt",
        "DMSerifDisplay-Regular.ttf",
        "DMSerifDisplay-OFL.txt",
    ):
        assert (FONTS / name).is_file(), name


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


def _finish_graph(tmp_path, monkeypatch, profile: Profile, font_file: str | None) -> str:
    seen: list = []
    monkeypatch.setattr(finish, "run", seen.append)
    monkeypatch.setattr(finish, "probe", lambda path: {"has_audio": True, "duration": 9.0})
    finish.finish_clip(
        tmp_path / "src.mp4",
        tmp_path / "out.mp4",
        profile,
        tmp_path,
        title="Big play",
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
    assert "drawtext=fontfile='/fonts/Arial.ttf':text='twitch.tv/y'" in graph
    assert [Path(font) for font, _text, _y in _hook_texts(graph)] == [SERIF]


# ---------- R8: svart fält ----------


@pytest.mark.parametrize("frame", NOT_ZOOM)
@pytest.mark.parametrize(
    "title", ["Galet skott", EXAMPLES[1], "This boss fight went completely sideways for everyone"]
)
def test_outside_zoom_a_black_full_width_bar_lies_behind_the_title(tmp_path, frame, title) -> None:
    profile = _profile()
    graph = build_filter(profile, False, True, None, title=title, frame=frame)
    rows = [y for _font, _text, y in _hook_texts(graph)]
    ((bar_y, bar_h),) = _bars(graph)
    assert graph.index("drawbox") < graph.index("drawtext")  # fältet under texten
    assert bar_y < rows[0] and rows[-1] + HOOK_FONT_SIZE < bar_y + bar_h  # luft runt texten
    assert bar_y + bar_h <= top_text_end(
        profile, tmp_path, title, frame=frame
    )  # klistermärket under fältet


def test_in_zoom_there_is_no_extra_bar() -> None:
    graph = build_filter(_profile(), False, True, None, title=EXAMPLES[0], frame="zoom")
    assert "drawbox" not in graph and _hook_texts(graph)


def test_no_title_gives_no_bar() -> None:
    assert "drawbox" not in build_filter(_profile(), False, True, None, title="", frame="fit")
    no_hook = Profile(name="x", streamer="y", hook_text=False)
    assert "drawbox" not in build_filter(
        no_hook, False, True, None, title="Galet skott", frame="fit"
    )


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
def test_the_examples_fit_on_two_lines_without_cutting(style, title) -> None:
    for frame in ("zoom", "fit"):
        texts = [
            text
            for _f, text, _y in _hook_texts(
                build_filter(_profile(style), False, True, None, title=title, frame=frame)
            )
        ]
        assert len(texts) <= 2 and "…" not in "".join(texts)
        assert " ".join(texts).replace("\\", "") == (title.upper() if style == "serif" else title)


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
    for title in EXAMPLES:
        for line in finish._hook_lines(_profile(style), title, frame="zoom"):
            assert _width(font, line) <= 960, (style, line)


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
