"""Hämtning av en kort del av en VOD som Twitch spelar in just nu. twitch-dl körs inte på riktigt:
en fake gör som twitch-dl 3.3.2 med --no-join och skriver spellistan och delarna i --cache-dir."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from campaign import live_part
from campaign.live_part import PartFailed, PartNotReady, _run, fetch_part
from campaign.media import probe

from .conftest import needs_ffmpeg

SEGMENT_S = 10.0  # Twitch-delar är 10 s långa; filerna i testet är 1 s, bara spellistan säger 10


@pytest.fixture(scope="module")
def segment_files(tmp_path_factory) -> Path:
    """Tolv små .ts-delar med sammanhängande tider, som en riktig HLS-ström."""
    folder = tmp_path_factory.mktemp("ts")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x36:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "12",
            "-c:v",
            "libx264",
            "-g",
            "10",
            "-c:a",
            "aac",
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_segment_filename",
            str(folder / "%05d.ts"),
            str(folder / "index.m3u8"),
        ],
        check=True,
    )
    return folder


@pytest.fixture
def twitchdl(monkeypatch):
    """Byt ut körningen av twitch-dl mot en fake."""

    def install(fake):
        monkeypatch.setattr(live_part, "_run", fake)
        return fake

    return install


def _seconds(hms: str) -> int:
    h, m, s = (int(p) for p in hms.split(":"))
    return h * 3600 + m * 60 + s


class FakeTwitchDl:
    """Gör som twitch-dl download --no-join: spellistan i playlist.m3u8, delarna i start-slut som
    00002.ts osv., redan hämtade delar hämtas inte igen, och playlist_downloaded.m3u8 med dem."""

    def __init__(
        self,
        segment_files: Path,
        segments: int,
        code: int = 0,
        output: str = "",
        ended: bool = False,
    ) -> None:
        self.segment_files = segment_files
        self.segments = segments
        self.ended = ended  # streamen är slut: spellistan har #EXT-X-ENDLIST
        self.code = code
        self.output = output
        self.commands: list[list[str]] = []
        self.downloaded: list[int] = []

    def __call__(self, cmd: list[str], timeout: float) -> tuple[int, str]:
        self.commands.append(cmd)
        cache = Path(cmd[cmd.index("--cache-dir") + 1])
        start, end = _seconds(cmd[cmd.index("--start") + 1]), _seconds(cmd[cmd.index("--end") + 1])
        cache.mkdir(parents=True, exist_ok=True)
        playlist = (
            "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n"
            + "".join(f"#EXTINF:{SEGMENT_S:.3f},\n{i}.ts\n" for i in range(self.segments))
            + ("#EXT-X-ENDLIST\n" if self.ended else "")
        )
        (cache / "playlist.m3u8").write_text(playlist)
        chosen = [
            i for i in range(self.segments) if (i + 1) * SEGMENT_S > start and i * SEGMENT_S < end
        ]
        for i in chosen:
            target = cache / f"{i:05d}.ts"
            if not target.exists():
                shutil.copy(self.segment_files / f"{i:05d}.ts", target)
                self.downloaded.append(i)
        (cache / "playlist_downloaded.m3u8").write_text(
            "#EXTM3U\n" + "".join(f"#EXTINF:{SEGMENT_S:.3f},\n{i:05d}.ts\n" for i in chosen)
        )
        return self.code, self.output


@needs_ffmpeg
def test_part_starts_at_the_segment_before_start_and_joins_those_segments(
    tmp_path, segment_files, twitchdl
) -> None:
    fake = twitchdl(FakeTwitchDl(segment_files, segments=10))
    folder = tmp_path / "topp_1"

    start = fetch_part("123", 25, 55, folder)

    assert start == 20.0
    assert fake.downloaded == [2, 3, 4, 5]  # 20-30, 30-40, 40-50, 50-60
    out = folder / "topp_1.mp4"
    assert probe(out)["duration"] == pytest.approx(4.0, abs=0.3)  # fyra delar på 1 s i testet
    assert not (folder / "segments").exists()  # delarna tas bort efter sammanfogningen


@needs_ffmpeg
def test_part_not_ready_when_playlist_ends_before_end(tmp_path, segment_files, twitchdl) -> None:
    twitchdl(FakeTwitchDl(segment_files, segments=5))  # 50 s än så länge
    folder = tmp_path / "topp_1"

    with pytest.raises(PartNotReady):
        fetch_part("123", 25, 55, folder)

    assert list(folder.rglob("*.mp4")) == []


@needs_ffmpeg
def test_a_finished_vod_shorter_than_end_gives_what_there_is(
    tmp_path, segment_files, twitchdl
) -> None:
    """Streamen slutade mindre än 30 s efter toppen: spellistan blir aldrig så lång som slutet."""
    fake = twitchdl(FakeTwitchDl(segment_files, segments=5, ended=True))  # 50 s, och klar
    folder = tmp_path / "topp_1"

    start = fetch_part("123", 25, 55, folder)

    assert start == 20.0
    assert fake.downloaded == [2, 3, 4]
    assert probe(folder / "topp_1.mp4")["duration"] == pytest.approx(3.0, abs=0.3)


@needs_ffmpeg
def test_twitchdl_runs_without_join_and_with_one_cache_dir_per_folder(
    tmp_path, segment_files, twitchdl
) -> None:
    fake = twitchdl(FakeTwitchDl(segment_files, segments=10))

    fetch_part("123", 25, 55, tmp_path / "topp_1")
    fetch_part("123", 25, 55, tmp_path / "topp_2")

    first, second = fake.commands
    assert first[:5] == [sys.executable, "-m", "twitchdl", "download", "123"]
    assert "--no-join" in first
    caches = [Path(cmd[cmd.index("--cache-dir") + 1]) for cmd in fake.commands]
    assert caches[0] != caches[1]
    assert caches[0].is_relative_to(tmp_path / "topp_1")
    assert caches[1].is_relative_to(tmp_path / "topp_2")


@needs_ffmpeg
def test_second_call_in_same_folder_reuses_segments_when_playlist_has_grown(
    tmp_path, segment_files, twitchdl
) -> None:
    fake = twitchdl(FakeTwitchDl(segment_files, segments=5))
    folder = tmp_path / "topp_1"
    with pytest.raises(PartNotReady):
        fetch_part("123", 25, 55, folder)
    assert fake.downloaded == [2, 3, 4]

    fake.segments = 10  # Twitch har spelat in mer
    start = fetch_part("123", 25, 55, folder)

    assert start == 20.0
    assert fake.downloaded == [2, 3, 4, 5]  # 2-4 fanns redan i samma cache-mapp
    assert fake.commands[0] == fake.commands[1]
    assert (folder / "topp_1.mp4").exists()


@needs_ffmpeg
def test_twitchdl_error_with_long_enough_playlist_is_part_failed(
    tmp_path, segment_files, twitchdl
) -> None:
    twitchdl(
        FakeTwitchDl(
            segment_files, segments=10, code=1, output="Error: Server responded with HTTP 403"
        )
    )

    with pytest.raises(PartFailed) as error:
        fetch_part("123", 25, 55, tmp_path / "topp_1")

    assert "Kunde inte hämta" in str(error.value)
    assert "HTTP 403" in str(error.value)
    assert not (tmp_path / "topp_1" / "topp_1.mp4").exists()


def test_twitch_says_try_later_is_part_not_ready(tmp_path, twitchdl) -> None:
    def fake(cmd: list[str], timeout: float) -> tuple[int, str]:
        return 1, (
            "Error: Twitch did not return an access token.\n"
            "This sometimes happens with fresh VODs. Try again a bit later."
        )

    twitchdl(fake)

    with pytest.raises(PartNotReady):
        fetch_part("123", 25, 55, tmp_path / "topp_1")


def test_run_stops_a_command_that_hangs(tmp_path) -> None:
    with pytest.raises(PartFailed, match="svarade inte"):
        _run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
