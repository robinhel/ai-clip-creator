"""Hopslagning av nedladdade VOD-delar. twitch-dl:s egen hopslagning fastnar när
VOD:en var live vid nedladdningen, eftersom spellistan då saknar slutmarkering."""

from __future__ import annotations

import subprocess

from campaign.download import join_segments
from campaign.media import probe

from .conftest import needs_ffmpeg


def _segments(folder) -> None:
    """Gör en VOD i delar som Twitch: en init-del och fmp4-delar, utan slutmarkering."""
    folder.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "6",
         "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-f", "hls", "-hls_time", "2",
         "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init-0.mp4",
         "-hls_segment_filename", str(folder / "%05d.mp4"), str(folder / "playlist.m3u8")],
        check=True,
    )
    text = (folder / "playlist.m3u8").read_text().replace("#EXT-X-ENDLIST\n", "")
    (folder / "playlist_downloaded.m3u8").write_text(text)


@needs_ffmpeg
def test_join_makes_one_whole_video_and_frees_the_disk(tmp_path) -> None:
    parts = tmp_path / "delar"
    _segments(parts)
    out = tmp_path / "123.mp4"

    join_segments(parts, out)

    info = probe(out)
    assert 5.5 < info["duration"] < 6.5
    assert info["has_audio"]
    assert not list(parts.glob("0*.mp4"))
    assert not (tmp_path / "123.del.mp4").exists()
