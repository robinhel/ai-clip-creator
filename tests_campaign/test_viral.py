"""Virala klipp (campaign/viral.py): insamling, avvikare och länkar. Inga riktiga nätverksanrop."""

from __future__ import annotations

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
        twitch_clip("a", 1000, 10),
        twitch_clip("b", 1000, 1),
        twitch_clip("c", 100, 1),
    ]
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    clips = viral.collect(profile)

    assert FakeTwitch.logins == ["teststreamer"]
    assert [c["id"] for c in clips] == ["b", "a", "c"]  # 1000/dag, sedan 100/dag i hämtad ordning
    b = clips[0]
    assert b["source"] == "twitch" and b["url"] == "https://clips.twitch.tv/b"
    assert b["views_per_day"] == pytest.approx(1000)
    assert b["outlier"] == pytest.approx(10)  # median 100
    assert b["location"] == {
        "vod_id": "2000",
        "start_s": 100,
        "end_s": 130.0,
        "method": "vod_offset",
        "confidence": 1.0,
    }
    assert b["measured"] is False and b["duration"] == 30.0 and b["creator"] == "tittare"
    assert b["posted_at"].startswith("2026-11-25")


def test_twitch_bara_senaste_60_dagarna(root, profile, monkeypatch):
    monkeypatch.setattr(viral.examples, "fetch", lambda url: cr_page([]))

    viral.collect(profile)

    assert FakeTwitch.kwargs["started_at"] == NOW - timedelta(days=60)


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
    FakeTwitch.clips = [twitch_clip("a", 10, 1), twitch_clip("b", 30, 1)]
    monkeypatch.setattr(
        viral.examples, "fetch", lambda url: cr_page([{"post_url": tiktok(1), "views": 50000}])
    )

    clips = {c["id"]: c for c in viral.collect(profile)}

    assert clips[tiktok(1)]["outlier"] == pytest.approx(1)
    assert clips["b"]["outlier"] == pytest.approx(1.5)


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


class FakeYDL:
    info: dict = {}
    opts: dict = {}
    files: list[Path] = []

    def __init__(self, opts):
        FakeYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        assert download
        path = Path(self.prepare_filename(self.info))
        path.write_bytes(b"video")
        FakeYDL.files.append(path)
        return dict(self.info, webpage_url=url)

    def prepare_filename(self, info):
        return FakeYDL.opts["outtmpl"].replace("%(id)s", info["id"]).replace("%(ext)s", "mp4")


@pytest.fixture
def ydl(monkeypatch):
    module = ModuleType("yt_dlp")
    module.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    FakeYDL.files = []
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


def test_add_link_mater_och_raderar_videon(root, profile, ydl, taste, located):
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


def test_add_link_egna_visningar_och_upload_date(root, profile, ydl, taste, located):
    del ydl.info["timestamp"]
    ydl.info["upload_date"] = "20261116"

    clip = viral.add_link(profile, URL, views=500)

    assert clip["views"] == 500 and clip["posted_at"].startswith("2026-11-16")
    assert clip["views_per_day"] == pytest.approx(500 / 10.5)


def test_add_link_raderar_videon_vid_fel(root, profile, ydl, located, monkeypatch):
    module = ModuleType("campaign.taste")

    def measure(video_path):
        raise RuntimeError("trasig video")

    module.measure = measure
    monkeypatch.setitem(sys.modules, "campaign.taste", module)

    with pytest.raises(RuntimeError):
        viral.add_link(profile, URL)
    assert not ydl.files[0].exists()


def test_add_link_samma_lank_ersatts(root, profile, ydl, taste, located):
    viral.add_link(profile, URL)
    viral.add_link(profile, URL, views=8000)

    assert [c["views"] for c in viral.load(profile)] == [8000]


def test_add_link_kraver_https(root, profile, ydl, taste, located):
    with pytest.raises(ValueError):
        viral.add_link(profile, "file:///etc/passwd")
    assert ydl.files == []


def test_add_link_hittar_stallet_innan_videon_raderas(root, profile, ydl, taste, located):
    calls, answer = located
    place = {"vod_id": "2000", "start_s": 50.0, "end_s": 71.5, "method": "transcript"}
    answer["place"] = place

    clip = viral.add_link(profile, URL)

    assert clip["location"] == place
    assert calls == [(ydl.files[0], True)]  # filen fanns när locate körde
    assert not ydl.files[0].exists()
