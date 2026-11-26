"""Virala klipp (campaign/viral.py): insamling, avvikare och länkar. Inga riktiga nätverksanrop."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from campaign import viral
from campaign.profile import Profile
from clipsmith.models.twitch import Clip

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
CR_URL = "https://contentrewards.com/c/campaigns/abc"


def tiktok(days_ago: float) -> str:
    """En TikTok-länk vars id bär postdatumet (unix-sekunder gånger 2**32)."""
    seconds = int(NOW.timestamp() - days_ago * 86400)
    return f"https://www.tiktok.com/@klippare/video/{seconds << 32}"


def twitch_clip(clip_id: str, views: int, days_ago: float, vod_offset: int | None = 100) -> Clip:
    created = datetime.fromtimestamp(NOW.timestamp() - days_ago * 86400, UTC)
    return Clip(
        id=clip_id,
        url=f"https://clips.twitch.tv/{clip_id}",
        title=f"Klipp {clip_id}",
        creator_name="tittare",
        video_id="2000" if vod_offset is not None else "",
        vod_offset=vod_offset,
        duration=30.0,
        view_count=views,
        created_at=created.isoformat().replace("+00:00", "Z"),
    )


class FakeTwitch:
    clips: list[Clip] = []
    logins: list[str] = []
    kwargs: dict = {}

    def __init__(self, client_id: str, client_secret: str):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_user_id(self, login: str) -> str:
        FakeTwitch.logins.append(login)
        return "42"

    def get_clips(self, broadcaster_id: str, **kwargs) -> list[Clip]:
        FakeTwitch.kwargs = kwargs
        return list(FakeTwitch.clips)


@pytest.fixture
def root(settings, monkeypatch) -> Path:
    monkeypatch.setenv("KLIPP_ROOT", str(settings.root))
    monkeypatch.setattr(viral, "_now", lambda: NOW)
    monkeypatch.setattr(viral, "TwitchClient", FakeTwitch)
    monkeypatch.setattr(
        viral,
        "load_secrets",
        lambda: SimpleNamespace(twitch_client_id="id", twitch_client_secret="hemlis"),
    )
    FakeTwitch.clips = []
    FakeTwitch.logins = []
    FakeTwitch.kwargs = {}
    return settings.root


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="testkamp", streamer="TestStreamer", raw={"links": {"content_rewards": CR_URL}}
    )


def cr_page(clips: list[dict]) -> dict:
    base = {
        "rank": 1,
        "platform": "tiktok",
        "name": "klippare",
        "earner_rank": None,
        "video_url": "https://cdn.contentrewards.com/a.mp4",
        "thumbnail_url": None,
        "title": "CR-klipp",
    }
    return {"clips": [{**base, **c} for c in clips], "earners": [], "geo": {}}


def test_twitch_klipp_far_plats_och_avvikare(root, profile, monkeypatch):
    FakeTwitch.clips = [
        twitch_clip("a", 10000, 10),
        twitch_clip("b", 10000, 1),
        twitch_clip("c", 1000, 1),
    ]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    clips = viral.collect(profile)

    assert FakeTwitch.logins == ["teststreamer"]
    assert [c["id"] for c in clips] == ["b", "a", "c"]  # 10 000/dag, sedan 1 000/dag i hämtad ordning
    b = clips[0]
    assert b["source"] == "twitch" and b["url"] == "https://clips.twitch.tv/b"
    assert b["views_per_day"] == pytest.approx(10000)
    assert b["outlier"] == pytest.approx(10)  # median 1 000
    assert b["location"] == {
        "vod_id": "2000",
        "start_s": 70.0,  # vod_offset är klippets slut
        "end_s": 100.0,
        "method": "vod_offset",
        "confidence": 0.8,
    }
    assert b["measured"] is False and b["duration"] == 30.0 and b["creator"] == "tittare"
    assert b["posted_at"].startswith("2026-11-25")


def test_twitch_bara_senaste_60_dagarna(root, profile, monkeypatch):
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    viral.collect(profile)

    assert FakeTwitch.kwargs["started_at"] == NOW - timedelta(days=60)
    assert FakeTwitch.kwargs["ended_at"] == NOW  # utan ended_at ger Helix bara en vecka


def test_twitch_plats_via_locate_from_twitch(root, profile, monkeypatch):
    seen: list[dict] = []

    def from_twitch(clip: dict):
        seen.append(clip)
        return {"vod_id": "spy"}

    monkeypatch.setattr(viral.locate, "from_twitch", from_twitch)
    FakeTwitch.clips = [twitch_clip("a", 10, 2)]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    assert viral.collect(profile)[0]["location"] == {"vod_id": "spy"}
    assert seen[0]["video_id"] == "2000" and seen[0]["vod_offset"] == 100


def test_twitch_klipp_utan_vod_saknar_plats(root, profile, monkeypatch):
    FakeTwitch.clips = [twitch_clip("a", 10, 2, vod_offset=None)]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    assert viral.collect(profile)[0]["location"] is None


def test_content_rewards_datum_ur_tiktok_id(root, profile, monkeypatch):
    urls: list[str] = []

    def fetch(url):
        urls.append(url)
        return cr_page(
            [
                {"post_url": tiktok(2), "views": 2000},
                {"post_url": tiktok(10), "views": 1000},
                {"post_url": "https://youtube.com/shorts/x", "views": 99999, "platform": "youtube"},
            ]
        )

    monkeypatch.setattr(viral.examples, "fetch", fetch)

    clips = viral.collect(profile)

    assert urls == [CR_URL]
    by_url = {c["url"]: c for c in clips}
    fast = by_url[tiktok(2)]
    assert fast["source"] == "content_rewards" and fast["location"] is None
    assert fast["views_per_day"] == pytest.approx(1000)
    assert fast["outlier"] == pytest.approx(1000 / 550)  # median av 1000 och 100
    assert fast["video_url"] == "https://cdn.contentrewards.com/a.mp4"
    youtube = by_url["https://youtube.com/shorts/x"]
    assert (
        youtube["posted_at"] is None
        and youtube["views_per_day"] is None
        and youtube["outlier"] is None
    )
    assert clips[-1] is youtube  # utan datum sist


def test_kallorna_raknas_var_for_sig(root, profile, monkeypatch):
    FakeTwitch.clips = [twitch_clip("a", 1000, 1), twitch_clip("b", 3000, 1)]
    monkeypatch.setattr(
        viral.examples, "fetch", lambda url: cr_page([{"post_url": tiktok(1), "views": 50000}])
    )

    clips = {c["id"]: c for c in viral.collect(profile)}

    assert clips[tiktok(1)]["outlier"] == pytest.approx(1)
    assert clips["b"]["outlier"] == pytest.approx(1.5)


def rated(clip_id: str, source: str, per_day: float | None, views: int = 50000) -> dict:
    return {"id": clip_id, "source": source, "views": views, "views_per_day": per_day, "outlier": None}


def test_egen_lank_jamfors_med_alla_klipp():
    clips = [
        rated("t1", "twitch", 8000),
        rated("t2", "twitch", 4200),
        rated("t3", "twitch", 850),
        rated("c1", "content_rewards", 7500),
        rated("c2", "content_rewards", 2300),
        rated("lank", "link", 21000),
    ]

    by_id = {c["id"]: c for c in viral._with_outliers(clips)}

    # alla sex: 850, 2300, 4200, 7500, 8000, 21000 ger medianen (4200 + 7500) / 2 = 5850
    assert by_id["lank"]["outlier"] == pytest.approx(21000 / 5850)
    # Twitch och Content Rewards räknas som förut, per källa
    assert by_id["t1"]["outlier"] == pytest.approx(8000 / 4200)
    assert by_id["t3"]["outlier"] == pytest.approx(850 / 4200)
    assert by_id["c1"]["outlier"] == pytest.approx(7500 / 4900)
    assert by_id["c2"]["outlier"] == pytest.approx(2300 / 4900)


def test_lank_utan_datum_och_ensam_lank():
    clips = [rated("t1", "twitch", 100), rated("lank", "link", None)]
    assert {c["id"]: c for c in viral._with_outliers(clips)}["lank"]["outlier"] is None

    assert viral._with_outliers([rated("lank", "link", 500)])[0]["outlier"] == pytest.approx(1)


def test_fel_och_saknade_nycklar_ger_tom_kalla(root, profile, monkeypatch):
    monkeypatch.setattr(
        viral, "load_secrets", lambda: SimpleNamespace(twitch_client_id="", twitch_client_secret="")
    )

    def broken(url):
        raise RuntimeError("Content Rewards does not answer.")

    monkeypatch.setattr(viral.examples, "fetch", broken)

    assert viral.collect(profile) == []


def test_utan_content_rewards_lank_hamtas_inget(root, monkeypatch):
    def fetch(url):
        raise AssertionError("ska inte hämtas")

    monkeypatch.setattr(viral.examples, "fetch", fetch)

    assert viral.collect(Profile(name="ingen", streamer="X")) == []


def test_sparas_och_laddas_per_profil(root, profile):
    assert viral.load(profile) == []
    viral.save(profile, [{"id": "a"}])

    assert (root / "klippdata" / "viral" / "testkamp.json").exists()
    assert viral.load("testkamp") == [{"id": "a"}]


def test_save_skriver_atomiskt(root, profile, monkeypatch):
    moved: list[tuple[Path, Path]] = []
    replace = os.replace

    def spy(src, dst):
        moved.append((Path(src), Path(dst)))
        replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)

    viral.save(profile, [{"id": "a"}])

    path = root / "klippdata" / "viral" / "testkamp.json"
    assert len(moved) == 1 and moved[0][1] == path and moved[0][0].parent == path.parent
    assert sorted(path.parent.iterdir()) == [path]  # ingen tmp-fil kvar
    assert viral.load(profile) == [{"id": "a"}]


def test_check_link_nekar_andra_sidor():
    with pytest.raises(ValueError):
        viral.check_link("https://x.com/1")
    viral.check_link(URL)


def test_profil_som_namn(root, monkeypatch):
    FakeTwitch.clips = [twitch_clip("a", 10, 1)]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    viral.collect("skiimasktimmy")

    assert FakeTwitch.logins == ["skiimasktimmy"]
    assert viral.load("skiimasktimmy")[0]["id"] == "a"


def test_collect_behaller_lankar_och_matning(root, profile, monkeypatch):
    viral.save(
        profile,
        [
            {"id": "https://x.com/v", "source": "link", "views_per_day": 5.0, "measured": True},
            {"id": "a", "source": "twitch", "measured": True, "taste": {"cuts": 3}},
        ],
    )
    FakeTwitch.clips = [twitch_clip("a", 10, 1)]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    clips = {c["id"]: c for c in viral.collect(profile)}

    assert clips["https://x.com/v"]["source"] == "link"
    assert clips["a"]["measured"] is True and clips["a"]["taste"] == {"cuts": 3}
    assert clips["a"]["views"] == 10
    assert {c["id"] for c in viral.load(profile)} == {"a", "https://x.com/v"}


def test_collect_behaller_sparad_plats_nar_den_nya_saknas(root, profile, monkeypatch):
    place = {"vod_id": "2000", "start_s": 50.0, "end_s": 71.0, "method": "transcript"}
    viral.save(
        profile,
        [
            {"id": tiktok(2), "source": "content_rewards", "location": place},
            {"id": "a", "source": "twitch", "location": {"vod_id": "gammal"}},
        ],
    )
    FakeTwitch.clips = [twitch_clip("a", 10, 1)]
    monkeypatch.setattr(
        viral.examples, "fetch", lambda url: cr_page([{"post_url": tiktok(2), "views": 10}])
    )

    clips = {c["id"]: c for c in viral.collect(profile)}

    assert clips[tiktok(2)]["location"] == place  # CR-klippet har ingen plats i en ny hämtning
    assert clips["a"]["location"]["vod_id"] == "2000"  # en ny plats ersätter den sparade


def test_noll_visningar_raknas_i_medianen(root, profile, monkeypatch):
    FakeTwitch.clips = [twitch_clip("a", 0, 1), twitch_clip("b", 1000, 1), twitch_clip("c", 3000, 1)]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    clips = {c["id"]: c for c in viral.collect(profile)}

    assert clips["c"]["outlier"] == pytest.approx(3)  # median av 0, 1000 och 3000
    assert clips["a"]["outlier"] is None  # under 1 000 visningar: ingen avvikare


def test_under_1000_visningar_ar_ingen_avvikare_men_raknas_i_medianen():
    clips = [
        rated("a", "twitch", 999, views=999),
        rated("b", "twitch", 1000, views=1000),
        rated("c", "twitch", 3000, views=3000),
    ]

    by_id = {c["id"]: c for c in viral._with_outliers(clips)}

    assert viral.MIN_VIEWS == 1000
    assert by_id["a"]["outlier"] is None
    assert by_id["b"]["outlier"] == pytest.approx(1)  # median av 999, 1000 och 3000
    assert by_id["c"]["outlier"] == pytest.approx(3)  # utan 999 i medianen hade det blivit 1,5


def test_collect_sparar_under_laset(root, profile, monkeypatch):
    seen: list[bool] = []
    save = viral.save

    def spy(profile, clips):
        seen.append(viral.LOCK.locked())
        save(profile, clips)

    monkeypatch.setattr(viral, "save", spy)
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    viral.collect(profile)

    assert seen == [True]


class DownloadError(Exception):
    """yt_dlp.utils.DownloadError i låtsasmodulen."""


class FakeYDL:
    """Låtsas-yt-dlp: extract_info läser bara fakta, process_ie_result skriver videon."""

    info: dict = {}
    opts: dict = {}
    files: list[Path] = []
    error: Exception | None = None

    def __init__(self, opts):
        FakeYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        assert download is False  # fakta först, nedladdningen efter kontrollen
        if FakeYDL.error:
            raise FakeYDL.error
        return dict(self.info, webpage_url=url)

    def process_ie_result(self, info, download=True):
        assert download is True
        path = Path(self.prepare_filename(info))
        path.write_bytes(b"video")
        FakeYDL.files.append(path)
        return info

    def prepare_filename(self, info):
        return FakeYDL.opts["outtmpl"].replace("%(id)s", info["id"]).replace("%(ext)s", "mp4")


@pytest.fixture
def ydl(monkeypatch):
    module = ModuleType("yt_dlp")
    module.YoutubeDL = FakeYDL
    module.utils = ModuleType("yt_dlp.utils")
    module.utils.DownloadError = DownloadError
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", module.utils)
    FakeYDL.files = []
    FakeYDL.opts = {}
    FakeYDL.error = None
    FakeYDL.info = {
        "id": "v1",
        "title": "Galet klipp",
        "uploader": "någon",
        "view_count": 4000,
        "timestamp": int(NOW.timestamp()) - 4 * 86400,
        "duration": 21.5,
    }
    return FakeYDL


@pytest.fixture
def taste(monkeypatch):
    module = ModuleType("campaign.taste")
    seen: list[Path] = []

    def measure(video_path):
        assert Path(video_path).read_bytes() == b"video"
        seen.append(Path(video_path))
        return {"cuts_per_10s": 2}

    module.measure = measure
    monkeypatch.setitem(sys.modules, "campaign.taste", module)
    return seen


@pytest.fixture
def located(monkeypatch):
    """Låtsas hitta stället i strömmen: sparar filen som locate fick och om den fanns då."""
    calls: list[tuple[Path, bool]] = []
    answer: dict = {"place": None}

    def fake_locate(clip, vod_transcripts=None, words=None):
        path = Path(clip["path"])
        calls.append((path, path.exists()))
        return answer["place"]

    monkeypatch.setattr(viral.locate, "locate", fake_locate)
    return calls, answer


URL = "https://www.tiktok.com/@n/video/1"


@pytest.mark.usefixtures("root", "located")
def test_add_link_mater_och_raderar_videon(profile, ydl, taste):
    clip = viral.add_link(profile, URL)

    expected = {
        "id": URL,
        "source": "link",
        "url": URL,
        "views": 4000,
        "title": "Galet klipp",
        "creator": "någon",
        "duration": 21.5,
        "location": None,
        "measured": True,
        "taste": {"cuts_per_10s": 2},
    }
    assert {key: clip[key] for key in expected} == expected
    assert clip["views_per_day"] == pytest.approx(1000)
    assert clip["posted_at"].startswith("2026-11-22")
    assert taste == ydl.files
    assert not ydl.files[0].exists()
    assert viral.load(profile) == [clip]


@pytest.mark.usefixtures("root", "located")
def test_add_link_egna_visningar_och_upload_date(profile, ydl, taste):
    del ydl.info["timestamp"]
    ydl.info["upload_date"] = "20261116"

    clip = viral.add_link(profile, URL, views=500)

    assert clip["views"] == 500 and clip["posted_at"].startswith("2026-11-16")
    assert clip["views_per_day"] == pytest.approx(500 / 10.5)


@pytest.mark.usefixtures("root", "located")
def test_add_link_raderar_videon_vid_fel(profile, ydl, monkeypatch):
    module = ModuleType("campaign.taste")

    def measure(video_path):
        raise RuntimeError("trasig video")

    module.measure = measure
    monkeypatch.setitem(sys.modules, "campaign.taste", module)

    with pytest.raises(RuntimeError):
        viral.add_link(profile, URL)
    assert not ydl.files[0].exists()


@pytest.mark.usefixtures("root", "located")
def test_add_link_samma_lank_ersatts(profile, ydl, taste):
    viral.add_link(profile, URL)
    viral.add_link(profile, URL, views=8000)

    assert [c["views"] for c in viral.load(profile)] == [8000]


@pytest.mark.usefixtures("root", "located")
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://www.tiktok.com/@n/video/1",
        "https://tiktok.com.evil.io/x",
        "https://localhost:1/x",
        "https://eviltiktok.com/x",
    ],
)
def test_add_link_bara_https_och_kanda_varden(profile, ydl, taste, url):
    with pytest.raises(ValueError):
        viral.add_link(profile, url)
    assert ydl.files == [] and ydl.opts == {}


@pytest.mark.usefixtures("root", "located")
@pytest.mark.parametrize(
    "url",
    [
        "https://tiktok.com/@n/video/1",
        "https://vm.tiktok.com/abc",
        "https://www.instagram.com/reel/abc",
        "https://m.youtube.com/shorts/abc",
        "https://youtu.be/abc",
    ],
)
def test_add_link_tillater_kanda_varden(profile, ydl, taste, url):
    assert viral.add_link(profile, url)["url"] == url


@pytest.mark.usefixtures("root", "located")
def test_add_link_hamtar_hogst_200_mb(profile, ydl, taste):
    viral.add_link(profile, URL)

    assert ydl.opts["max_filesize"] == 200 * 1024 * 1024


@pytest.mark.usefixtures("root", "located")
def test_add_link_listar_kanaler_och_spellistor_platt(profile, ydl, taste):
    viral.add_link(profile, URL)

    assert ydl.opts["extract_flat"] == "in_playlist"  # varje video i en spellista hämtas inte


@pytest.mark.usefixtures("root", "located")
@pytest.mark.parametrize("kind", ["playlist", "multi_video"])
def test_add_link_spellista_nekas_innan_nedladdning(profile, ydl, taste, kind):
    ydl.info["_type"] = kind

    with pytest.raises(ValueError):
        viral.add_link(profile, URL)
    assert ydl.files == [] and taste == []


@pytest.mark.usefixtures("root", "located")
def test_add_link_downloaderror_blir_runtimeerror(profile, ydl, taste):
    ydl.error = DownloadError("ERROR: [TikTok] 1: Video not available")

    with pytest.raises(RuntimeError, match="Could not download"):
        viral.add_link(profile, URL)
    assert viral.load(profile) == []


@pytest.mark.usefixtures("root", "located", "taste")
def test_add_link_sparar_under_laset(profile, ydl, monkeypatch):
    seen: list[bool] = []
    save = viral.save

    def spy(profile, clips):
        seen.append(viral.LOCK.locked())
        save(profile, clips)

    monkeypatch.setattr(viral, "save", spy)

    viral.add_link(profile, URL)

    assert seen == [True]


@pytest.mark.usefixtures("root", "taste")
def test_add_link_hittar_stallet_innan_videon_raderas(profile, ydl, located):
    calls, answer = located
    place = {"vod_id": "2000", "start_s": 50.0, "end_s": 71.5, "method": "transcript"}
    answer["place"] = place

    clip = viral.add_link(profile, URL)

    assert clip["location"] == place
    assert calls == [(ydl.files[0], True)]  # filen fanns när locate körde
    assert not ydl.files[0].exists()


@pytest.mark.usefixtures("root", "taste")
def test_add_link_fortsatter_nar_locate_kastar(profile, ydl, monkeypatch):
    def broken(clip, vod_transcripts=None, words=None):
        raise RuntimeError("whisper kraschade")

    monkeypatch.setattr(viral.locate, "locate", broken)

    clip = viral.add_link(profile, URL)

    assert clip["location"] is None and clip["measured"] is True
    assert not ydl.files[0].exists()
    assert viral.load(profile) == [clip]
