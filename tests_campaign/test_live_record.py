"""Inspelaren av Twitchs levande ström. Ingen riktig Twitch: spellistor är text i testet, och nätet,
klockan och väntan byts mot fakes."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from campaign import live_record
from campaign.live_part import PartFailed, PartNotReady
from campaign.live_record import Recorder, cut, live_playlist, parse
from campaign.media import probe

from .conftest import needs_ffmpeg

T0 = dt.datetime(2026, 11, 18, 16, 56, 0, 199000, tzinfo=dt.UTC).timestamp()


def _pdt(t: float) -> str:
    """Tiden som Twitch skriver den: 2026-11-18T16:56:00.199Z."""
    ms = round(t * 1000)
    return (
        dt.datetime.fromtimestamp(ms // 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{ms % 1000:03d}Z"
    )


def _media(first_seq: int, start: float, titles: list[str], ads: str = "") -> str:
    """En mediaspellista som Twitchs: delar på 2 s med egen PDT, prefetch-delar sist."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:5",
        f"#EXT-X-MEDIA-SEQUENCE:{first_seq}",
    ]
    if ads:
        lines.append(ads)
    for i, title in enumerate(titles):
        seq = first_seq + i
        lines += [
            f"#EXT-X-PROGRAM-DATE-TIME:{_pdt(start + 2 * i)}",
            f"#EXTINF:2.000,{title}",
            f"https://seg/{seq}.ts",
        ]
    lines += [f"#EXT-X-TWITCH-PREFETCH:https://seg/prefetch{i}.ts" for i in range(2)]
    return "\n".join(lines) + "\n"


def _ad_range(
    start: float, duration: float, cls: bool = True, ad_id: str = "stitched-ad-1763484962-30"
) -> str:
    klass = ',CLASS="twitch-stitched-ad"' if cls else ""
    return (
        f'#EXT-X-DATERANGE:ID="{ad_id}"{klass},START-DATE="{_pdt(start)}",DURATION={duration},'
        'X-TV-TWITCH-AD-ROLL-TYPE="MIDROLL",X-TV-TWITCH-AD-COMMERCIAL-ID="a,b"'
    )


# --- parse ---------------------------------------------------------------------------------------


def test_parse_reads_seq_wall_clock_and_duration_and_skips_prefetch():
    segs = parse(_media(100, T0, ["live", "live", "live"]))
    assert [s.seq for s in segs] == [100, 101, 102]
    assert [s.start_wall for s in segs] == pytest.approx([T0, T0 + 2, T0 + 4], abs=1e-3)
    assert [s.dur for s in segs] == [2.0, 2.0, 2.0]
    assert [s.url for s in segs] == [
        "https://seg/100.ts",
        "https://seg/101.ts",
        "https://seg/102.ts",
    ]
    assert not any(s.ad for s in segs)


def test_parse_marks_amazon_segments_as_ads():
    text = _media(7, T0, ["live", "Amazon|8a2f", "Amazon|8a2f", "live"]).replace(
        "#EXTINF:2.000,live\nhttps://seg/10.ts",
        "#EXT-X-DISCONTINUITY\n#EXTINF:2.000,live\nhttps://seg/10.ts",
    )
    assert [s.ad for s in parse(text)] == [False, True, True, False]


def test_parse_marks_segments_inside_a_stitched_ad_range_even_without_amazon_title():
    text = _media(0, T0, ["live"] * 5, ads=_ad_range(T0 + 2, 4.0))
    assert [s.ad for s in parse(text)] == [False, True, True, False, False]


def test_parse_knows_an_ad_range_by_its_id_alone():
    text = _media(0, T0, ["live"] * 3, ads=_ad_range(T0, 2.0, cls=False))
    assert [s.ad for s in parse(text)] == [True, False, False]


def test_parse_ignores_other_dateranges():
    text = _media(0, T0, ["live"] * 2, ads=_ad_range(T0, 4.0, cls=False, ad_id="source-1763"))
    assert [s.ad for s in parse(text)] == [False, False]


def test_parse_of_an_empty_playlist_is_empty():
    assert parse("#EXTM3U\n#EXT-X-TARGETDURATION:5\n") == []


# --- live_playlist ------------------------------------------------------------------------------

MASTER = """#EXTM3U
#EXT-X-TWITCH-INFO:NODE="video-edge",SERVING-ID="x"
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="chunked",NAME="1080p60 (source)",AUTOSELECT=YES,DEFAULT=YES
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,CODECS="avc1.64002A,mp4a.40.2",VIDEO="chunked",FRAME-RATE=60.000
https://video-edge.example/v1/playlist/source.m3u8
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="720p60",NAME="720p60",AUTOSELECT=YES,DEFAULT=YES
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,CODECS="avc1.4D401F,mp4a.40.2",VIDEO="720p60",FRAME-RATE=60.000
https://video-edge.example/v1/playlist/720.m3u8
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="audio_only",NAME="audio_only",AUTOSELECT=NO,DEFAULT=NO
#EXT-X-STREAM-INF:BANDWIDTH=160000,CODECS="mp4a.40.2",VIDEO="audio_only"
https://video-edge.example/v1/playlist/audio.m3u8
"""


def _status_error(
    code: int, url: str = "https://usher.example/x.m3u8?token=hemligt"
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"{code}", request=request, response=httpx.Response(code, request=request)
    )


@pytest.fixture
def gql(monkeypatch):
    """Byt GQL mot en fake som svarar med token, eller null när kanalen inte är live."""
    sent = []

    def install(token):
        def fake(query):
            sent.append(query)
            return {"data": {"streamPlaybackAccessToken": token}}

        monkeypatch.setattr(live_record.twitch, "gql_query", fake)
        return sent

    return install


def test_live_playlist_is_none_when_twitch_has_no_token(gql, monkeypatch):
    sent = gql(None)
    monkeypatch.setattr(live_record, "_fetch", lambda url: pytest.fail("usher ska inte frågas"))
    assert live_playlist("IShowSpeed") is None
    assert 'channelName: "ishowspeed"' in sent[0]
    assert 'playerType: "site"' in sent[0]


def test_live_playlist_is_none_when_usher_says_404(gql, monkeypatch):
    gql({"value": '{"channel":"ishowspeed"}', "signature": "abc"})

    def fake(url):
        raise _status_error(404, url)

    monkeypatch.setattr(live_record, "_fetch", fake)
    assert live_playlist("ishowspeed") is None


def test_live_playlist_picks_the_source_quality(gql, monkeypatch):
    gql({"value": '{"channel":"ishowspeed"}', "signature": "abc"})
    asked = []

    def fake(url):
        asked.append(url)
        return MASTER.encode()

    monkeypatch.setattr(live_record, "_fetch", fake)
    assert live_playlist("ishowspeed") == "https://video-edge.example/v1/playlist/source.m3u8"
    query = httpx.URL(asked[0])
    assert query.host == "usher.ttvnw.net"
    assert query.path == "/api/channel/hls/ishowspeed.m3u8"
    assert query.params["sig"] == "abc"
    assert query.params["token"] == '{"channel":"ishowspeed"}'
    assert query.params["allow_source"] == "true"


def test_live_playlist_passes_other_errors_on(gql, monkeypatch):
    gql({"value": "{}", "signature": "abc"})

    def fake(url):
        raise _status_error(500, url)

    monkeypatch.setattr(live_record, "_fetch", fake)
    with pytest.raises(httpx.HTTPStatusError):
        live_playlist("ishowspeed")


def test_live_playlist_refuses_a_login_that_is_not_a_twitch_name(gql):
    gql(None)
    with pytest.raises(ValueError):
        live_playlist('speed"){ evil }')


# --- Recorder -----------------------------------------------------------------------------------


class FakeTwitch:
    """Spellistor per varv, delar som bytes. playlists[i] är svaret på i:te läsningen av spellistan;
    en int där är en HTTP-status som fel. Tar listan slut svarar spellistan 404 och kanalen är inte live."""

    def __init__(self, playlists: list, offline_after: int | None = None):
        self.playlists = list(playlists)
        self.reads = 0
        self.resolved = 0
        self.fetched: list[str] = []
        self.waits: list[float] = []
        self.clock = T0 + 10
        self.offline_after = offline_after

    def playlist(self, login: str) -> str | None:
        self.resolved += 1
        if self.offline_after is not None and self.resolved > self.offline_after:
            return None
        if self.reads >= len(self.playlists):
            return None
        return f"https://edge/{login}/{self.resolved}.m3u8?token=hemligt"

    def get(self, url: str) -> bytes:
        if "/seg/" in url:
            self.fetched.append(url)
            return f"data {url}".encode()
        self.reads += 1
        if self.reads > len(self.playlists):
            raise _status_error(404, url)
        answer = self.playlists[self.reads - 1]
        if isinstance(answer, int):
            raise _status_error(answer, url)
        return answer.encode()

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock += seconds


def _recorder(tmp_path: Path, fake: FakeTwitch, keep_s: float = 600.0) -> Recorder:
    rec = Recorder("ishowspeed", tmp_path / "buffer", keep_s=keep_s)
    rec.playlist = fake.playlist
    rec.get = fake.get
    rec.now = fake.now
    rec.sleep = fake.sleep
    return rec


def _index(folder: Path) -> list[list[str]]:
    return [line.split("\t") for line in (folder / "index.tsv").read_text().splitlines()]


def test_run_saves_new_segments_once_and_skips_ads_and_prefetch(tmp_path):
    fake = FakeTwitch(
        [
            _media(10, T0, ["live", "live", "live"]),
            _media(11, T0 + 2, ["live", "live", "Amazon|1"]),
            _media(13, T0 + 6, ["Amazon|1", "live"]),
        ]
    )
    rec = _recorder(tmp_path, fake)
    rec.run()

    rows = _index(rec.folder)
    assert [int(r[0]) for r in rows] == [10, 11, 12, 14]
    assert [float(r[1]) for r in rows] == pytest.approx([T0, T0 + 2, T0 + 4, T0 + 8], abs=1e-3)
    assert [float(r[2]) for r in rows] == [2.0] * 4
    for row in rows:
        assert (rec.folder / row[3]).read_bytes().startswith(b"data https://seg/")
    assert sorted(fake.fetched) == sorted(f"https://seg/{n}.ts" for n in (10, 11, 12, 14))
    assert not any("prefetch" in url for url in fake.fetched)
    assert fake.waits == [2.0, 2.0, 2.0, 1.0]  # en läsning varannan sekund, sist 404 och inte live


def test_run_stops_when_stop_is_called(tmp_path):
    fake = FakeTwitch([_media(0, T0, ["live"])] * 100)
    rec = _recorder(tmp_path, fake)

    def sleep(seconds):
        fake.sleep(seconds)
        if len(fake.waits) == 3:
            rec.stop()

    rec.sleep = sleep
    rec.run()
    assert fake.reads == 3


def test_run_ends_at_once_when_the_channel_is_not_live(tmp_path):
    fake = FakeTwitch([])
    rec = _recorder(tmp_path, fake)
    rec.run()
    assert fake.waits == []
    assert fake.resolved == 1


def test_run_asks_for_a_new_url_after_403_and_404_and_waits_longer_each_time(tmp_path):
    fake = FakeTwitch(
        [403, 404] + [403] * 8 + [_media(0, T0, ["live"]), 404, _media(1, T0 + 2, ["live"])]
    )
    rec = _recorder(tmp_path, fake)
    rec.run()
    assert fake.waits == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60, 2, 1, 2, 1]
    assert (
        fake.resolved == 13
    )  # en ny URL efter varje 403 och 404, och sista frågan svarar inte live
    assert [int(r[0]) for r in _index(rec.folder)] == [0, 1]


def test_run_waits_and_asks_again_when_asking_twitch_fails(tmp_path):
    fake = FakeTwitch([_media(0, T0, ["live"])])
    rec = _recorder(tmp_path, fake)
    calls = []

    def playlist(login):
        calls.append(login)
        if len(calls) == 1:
            raise httpx.ConnectError("nere")
        return fake.playlist(login)

    rec.playlist = playlist
    rec.run()
    assert fake.waits[0] == 1
    assert [int(r[0]) for r in _index(rec.folder)] == [0]


def test_run_keeps_the_url_after_other_errors(tmp_path):
    fake = FakeTwitch([500, _media(0, T0, ["live"])])
    rec = _recorder(tmp_path, fake)
    rec.run()
    assert fake.waits[:1] == [1]
    assert fake.resolved == 2  # den första URL:en och sista frågan när listan är slut


def test_run_ends_when_the_channel_goes_offline_after_a_404(tmp_path):
    fake = FakeTwitch([_media(0, T0, ["live"]), 404, _media(1, T0 + 2, ["live"])], offline_after=1)
    rec = _recorder(tmp_path, fake)
    rec.run()
    assert fake.reads == 2
    assert [int(r[0]) for r in _index(rec.folder)] == [0]


def test_run_tries_a_failing_segment_again_next_round(tmp_path):
    fake = FakeTwitch([_media(0, T0, ["live"]), _media(0, T0, ["live"])])
    rec = _recorder(tmp_path, fake)
    get = fake.get
    failed = []

    def flaky(url):
        if "/seg/" in url and not failed:
            failed.append(url)
            raise httpx.ConnectTimeout("timeout")
        return get(url)

    rec.get = flaky
    rec.run()
    assert [int(r[0]) for r in _index(rec.folder)] == [0]


def test_run_cleans_up_segments_older_than_keep_s(tmp_path):
    rounds = [_media(i, T0 + 2 * i, ["live"]) for i in range(10)]
    fake = FakeTwitch(rounds)
    fake.clock = T0 + 2
    rec = _recorder(tmp_path, fake, keep_s=6.0)
    rec.run()
    rows = _index(rec.folder)
    # varje varv städar först; det sista, med 404, vid T0 + 22: delar som slutade före T0 + 16 är borta
    assert [int(r[0]) for r in rows] == [7, 8, 9]
    assert sorted(p.name for p in rec.folder.glob("*.ts")) == sorted(r[3] for r in rows)


def test_a_new_session_keeps_the_old_segments_even_when_seq_starts_over(tmp_path):
    first = FakeTwitch([_media(0, T0, ["live", "live"])])
    _recorder(tmp_path, first).run()
    second = FakeTwitch([_media(0, T0 + 30, ["live", "live"])])
    second.clock = T0 + 40
    rec = _recorder(tmp_path, second)
    rec.run()
    rows = _index(rec.folder)
    assert [int(r[0]) for r in rows] == [0, 1, 0, 1]
    assert len({r[3] for r in rows}) == 4
    assert all((rec.folder / r[3]).exists() for r in rows)


def test_run_never_logs_the_token(tmp_path, caplog):
    fake = FakeTwitch([403, _media(0, T0, ["live"])])
    rec = _recorder(tmp_path, fake)
    with caplog.at_level("DEBUG"):
        rec.run()
    assert "hemligt" not in caplog.text
    assert "edge/" not in caplog.text


def test_httpx_never_logs_twitchs_urls_when_the_recorder_fetches(
    gql, tmp_path, monkeypatch, caplog
):
    """httpx skriver hela URL:en på INFO. Usher-URL:en har sig och token, och videons adresser har
    signerade vägar. Den riktiga loggningen i httpx, med ett låtsasnät under den."""
    gql({"value": '{"channel":"ishowspeed","hemligt":true}', "signature": "hemligsig"})
    edge = "https://video-edge-c1.abs.hls.ttvnw.net/v1/"
    master = MASTER.replace("https://video-edge.example/v1/", edge + "hemligt-")
    media = _media(0, T0, ["live", "live"]).replace("https://seg/", edge + "segment/hemligt-")

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "usher.ttvnw.net":
            return httpx.Response(200, text=master)
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text=media)
        return httpx.Response(200, content=b"ts")

    net = httpx.MockTransport(answer)
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: net.handle_request(request)
    )
    rec = Recorder("ishowspeed", tmp_path / "buffer")
    rec.sleep = lambda seconds: rec.stop()  # ett varv
    with caplog.at_level("INFO", logger="httpx"):
        rec.run()
        httpx.get("https://example.test/annat")  # annan kod loggas som förut
    assert len(_index(rec.folder)) == 2  # spellistan och delarna hämtades genom httpx
    assert "example.test/annat" in caplog.text
    assert "hemligt" not in caplog.text
    assert "ttvnw" not in caplog.text


# --- cut ----------------------------------------------------------------------------------------

SEG_S = 2.0


@pytest.fixture(scope="module")
def ts_files(tmp_path_factory) -> list[Path]:
    """Sex små .ts-delar på 2 s med sammanhängande tider, som Twitchs."""
    folder = tmp_path_factory.mktemp("ts")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12",
         "-c:v", "libx264", "-g", "20", "-c:a", "aac", "-f", "hls", "-hls_time", "2",
         "-hls_segment_filename", str(folder / "%05d.ts"), str(folder / "index.m3u8")],
        check=True,
    )  # fmt: skip
    return sorted(folder.glob("*.ts"))


@pytest.fixture
def buffer(tmp_path, ts_files) -> Path:
    """En buffert som inspelaren skriver den: delarna och index.tsv med seq, start, längd och filnamn."""
    folder = tmp_path / "buffer"
    folder.mkdir()
    lines = []
    for i, src in enumerate(ts_files):
        start = T0 + SEG_S * i
        name = f"{round(start * 1000)}.ts"
        shutil.copy(src, folder / name)
        lines.append(f"{i}\t{start:.3f}\t{SEG_S}\t{name}\n")
    (folder / "index.tsv").write_text("".join(lines))
    return folder


@needs_ffmpeg
def test_cut_joins_the_segments_that_cover_the_window(buffer, tmp_path):
    out = tmp_path / "del-1"
    start = cut(buffer, T0 + 3.0, T0 + 7.0, out)
    assert start == pytest.approx(T0 + 2.0, abs=1e-3)  # början av delen som 3.0 ligger i
    video = out / "del-1.mp4"
    assert video.exists()
    assert probe(video)["duration"] == pytest.approx(6.0, abs=0.5)  # delarna 2, 3 och 4
    assert len(list(buffer.glob("*.ts"))) == 6  # bufferten är kvar: delarna länkades
    assert not (out / "segments").exists()


@needs_ffmpeg
def test_cut_again_in_the_same_folder_works(buffer, tmp_path):
    out = tmp_path / "del-2"
    cut(buffer, T0, T0 + 4.0, out)
    assert cut(buffer, T0 + 4.0, T0 + 8.0, out) == pytest.approx(T0 + 4.0, abs=1e-3)
    assert probe(out / "del-2.mp4")["duration"] == pytest.approx(4.0, abs=0.5)


def test_cut_is_not_ready_when_the_end_is_not_recorded_yet(buffer, tmp_path):
    with pytest.raises(PartNotReady):
        cut(buffer, T0 + 4.0, T0 + 12.5, tmp_path / "del")


def test_cut_fails_when_the_start_is_already_cleaned_up(buffer, tmp_path):
    with pytest.raises(PartFailed, match="städa"):
        cut(buffer, T0 - 10.0, T0 + 4.0, tmp_path / "del")


def test_cut_fails_when_there_are_no_segments(tmp_path):
    folder = tmp_path / "buffer"
    folder.mkdir()
    with pytest.raises(PartFailed):
        cut(folder, T0, T0 + 4.0, tmp_path / "del")
    (folder / "index.tsv").write_text("")
    with pytest.raises(PartFailed):
        cut(folder, T0, T0 + 4.0, tmp_path / "del")


def test_cut_fails_when_holes_are_more_than_30_percent(buffer, tmp_path):
    lines = (buffer / "index.tsv").read_text().splitlines(keepends=True)
    (buffer / "index.tsv").write_text("".join(lines[:1] + lines[3:]))  # 4 s reklam av 10 s fönster
    with pytest.raises(PartFailed, match="[Hh]ål"):
        cut(buffer, T0, T0 + 10.0, tmp_path / "del")


@needs_ffmpeg
def test_cut_accepts_a_small_hole(buffer, tmp_path):
    lines = (buffer / "index.tsv").read_text().splitlines(keepends=True)
    (buffer / "index.tsv").write_text("".join(lines[:2] + lines[3:]))  # 2 s av 10 s
    assert cut(buffer, T0, T0 + 10.0, tmp_path / "del") == pytest.approx(T0, abs=1e-3)
    assert (tmp_path / "del" / "del.mp4").exists()


@needs_ffmpeg
def test_cut_pushes_a_hole_together_and_says_where_the_pictures_come_from(buffer, tmp_path):
    lines = (buffer / "index.tsv").read_text().splitlines(keepends=True)
    (buffer / "index.tsv").write_text("".join(lines[:2] + lines[3:]))  # reklam T0+4 till T0+6
    spans = cut(buffer, T0, T0 + 10.0, tmp_path / "del")
    assert spans == [
        pytest.approx((T0, T0 + 4.0), abs=1e-3),
        pytest.approx((T0 + 6.0, T0 + 10.0), abs=1e-3),
    ]
    video = tmp_path / "del" / "del.mp4"
    assert probe(video)["duration"] == pytest.approx(8.0, abs=0.3)  # ingen lucka på 2 s i filen
    audio = tmp_path / "ljud.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", str(audio)], check=True)
    seconds = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert float(seconds) == pytest.approx(8.0, abs=0.3)  # ljudet, som transkriberas, följer bilden


@needs_ffmpeg
def test_cut_fails_with_a_short_text_when_a_segment_was_cleaned_up_meanwhile(buffer, tmp_path):
    rows = _index(buffer)
    (buffer / rows[1][3]).unlink()
    with pytest.raises(PartFailed, match="städa"):
        cut(buffer, T0, T0 + 4.0, tmp_path / "del")


def test_cut_skips_a_half_written_last_line(buffer, tmp_path):
    with (buffer / "index.tsv").open("a") as index:
        index.write("6\t17634")
    with pytest.raises(PartNotReady):
        cut(buffer, T0, T0 + 12.5, tmp_path / "del")
