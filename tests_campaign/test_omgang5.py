"""Omgång 5: senaste streams, poster per klipp, förlopp i transkriberingen och intäkter.
Påhittad Twitch och påhittad Berget. Inga riktiga anrop."""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from campaign import estimate, streams
from campaign.cliplog import ClipLog
from campaign.media import probe

from .conftest import PAKET, make_clip, needs_ffmpeg
from .test_api import _clip, client  # noqa: F401  (fixture)

THUMB = "https://static-cdn.jtvnw.net/cf_vods/x/thumb/thumb0-640x360.jpg"
OLD_FIELDS = ("vod_id", "channel", "channel_name", "title", "game", "duration_s", "created_at", "thumbnail_url",
              "campaigns", "run_id", "run_status", "clip_count")


def _node(vod_id: str, created: str, game: str = "Bodycam") -> dict:
    return {"id": vod_id, "title": f"Stream {vod_id}", "lengthSeconds": 13360, "createdAt": created,
            "game": {"name": game}, "previewThumbnailURL": THUMB}


class FakeStreams:
    def __init__(self):
        self.users = {"skiimasktimmy": ("SkiiMaskTimmy", [_node("2925991945", "2026-11-11T18:01:44Z"),
                                                          _node("2925070238", "2026-11-10T18:01:02Z")])}
        self.down: set[str] = set()
        self.queries: list[str] = []

    def gql(self, query: str) -> dict:
        self.queries.append(query)
        login = query.split('"')[1]
        if login in self.down:
            raise ConnectionError("Twitch svarar inte")
        user = self.users.get(login)
        return {"data": {"user": user and {"login": login, "displayName": user[0],
                                           "videos": {"edges": [{"node": n} for n in user[1]]}}}}


@pytest.fixture
def twitch_streams(client, monkeypatch) -> FakeStreams:  # noqa: F811
    fake = FakeStreams()
    monkeypatch.setattr(streams, "_cache", {})
    monkeypatch.setattr(streams.twitch, "gql_query", fake.gql)
    # Inga Helix-nycklar: tittarklippen räknas inte och inget riktigt anrop görs.
    monkeypatch.setattr(streams, "load_secrets", lambda: SimpleNamespace(twitch_client_id="", twitch_client_secret=""))
    return fake


def _second_campaign(settings, name: str, channel: str) -> None:
    """En kampanj till med egen config, så att den får en egen kanal."""
    config = (settings.root / "config.timmy.yaml").read_text().replace("  - skiimasktimmy\n", f"  - {channel}\n")
    (settings.root / f"config.{name}.yaml").write_text(config)
    profile = (settings.profiles_dir / "skiimasktimmy.yaml").read_text().replace(
        "clipsmith_config: config.timmy.yaml", f"clipsmith_config: config.{name}.yaml")
    (settings.profiles_dir / f"{name}.yaml").write_text(profile)


def _done_run(settings, run_id_vod: str) -> int:
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    run = Run(vod_id=run_id_vod, channel="skiimasktimmy", status=RunStatus.done)
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, run_id_vod, "skiimasktimmy", "Bodycam")
    return run_id


# ---------- 1. senaste streams ----------


def test_streams_are_newest_first_with_campaigns_and_the_latest_run(client, settings, twitch_streams) -> None:  # noqa: F811
    run_id = _done_run(settings, "2925070238")
    ClipLog(settings.data_dir / "klipp.db").add(vod_id="2925070238", profile="skiimasktimmy", file="a.mp4",
                                                 title="x", caption="c")
    response = client.get("/campaign/streams")
    assert response.status_code == 200, response.text
    first, second = response.json()
    assert {key: first[key] for key in OLD_FIELDS} == {"vod_id": "2925991945", "channel": "skiimasktimmy", "channel_name": "SkiiMaskTimmy",
                     "title": "Stream 2925991945", "game": "Bodycam", "duration_s": 13360,
                     "created_at": "2026-11-11T18:01:44Z", "thumbnail_url": THUMB,
                     "campaigns": ["skiimasktimmy"], "run_id": None, "run_status": None, "clip_count": 0}
    assert (second["vod_id"], second["run_id"], second["run_status"], second["clip_count"]) == (
        "2925070238", run_id, "done", 1)
    assert "videos(first: 8, type: ARCHIVE, sort: TIME)" in twitch_streams.queries[0]
    assert "previewThumbnailURL(width: 640, height: 360)" in twitch_streams.queries[0]
    assert " viewCount status " in twitch_streams.queries[0]



def test_twitchs_placeholder_image_for_a_stream_in_progress_gives_no_image(client, settings, twitch_streams) -> None:  # noqa: F811
    twitch_streams.users["skiimasktimmy"][1][0]["previewThumbnailURL"] = (
        "https://vod-secure.twitch.tv/_404/404_processing_640x360.png")
    first, second = client.get("/campaign/streams").json()
    assert first["thumbnail_url"] is None
    assert second["thumbnail_url"] == THUMB


def test_each_channel_is_asked_once_and_the_answer_is_cached_five_minutes(client, settings, twitch_streams,  # noqa: F811
                                                                          monkeypatch) -> None:
    _second_campaign(settings, "timmy2", "skiimasktimmy")
    clock = {"t": 1000.0}
    monkeypatch.setattr(streams, "monotonic", lambda: clock["t"])
    found = client.get("/campaign/streams").json()
    assert len(twitch_streams.queries) == 1
    assert found[0]["campaigns"] == ["skiimasktimmy", "timmy2"]
    client.get("/campaign/streams")
    assert len(twitch_streams.queries) == 1
    clock["t"] += 301
    client.get("/campaign/streams")
    assert len(twitch_streams.queries) == 2


def test_a_channel_that_fails_is_skipped_and_all_failing_gives_502(client, settings, twitch_streams) -> None:  # noqa: F811
    _second_campaign(settings, "ny", "nykanal")  # Twitch känner inte kanalen
    assert [s["channel"] for s in client.get("/campaign/streams").json()] == ["skiimasktimmy"] * 2
    streams._cache.clear()
    twitch_streams.down = {"skiimasktimmy", "nykanal"}
    response = client.get("/campaign/streams")
    assert response.status_code == 502
    assert "Twitch" in response.json()["detail"]


def test_streams_without_campaigns_is_an_empty_list(client, settings, twitch_streams) -> None:  # noqa: F811
    for path in settings.profiles_dir.glob("*.yaml"):
        path.unlink()
    assert client.get("/campaign/streams").json() == []
    assert twitch_streams.queries == []


def test_one_project_per_stream_and_campaign_the_newest_run_wins(client, settings) -> None:  # noqa: F811
    """Kör igen och en ny körning på samma VOD ger inget nytt projekt. Projektet visar den senaste körningen."""
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    _second_campaign(settings, "annan", "skiimasktimmy")  # två kampanjer på samma kanal
    log = ClipLog(settings.data_dir / "klipp.db")
    db = get_session()
    ids = []
    for vod_id, profile, status in [("2925070238", "skiimasktimmy", RunStatus.failed),
                                    ("2925070238", "skiimasktimmy", RunStatus.done),
                                    ("2925991945", "skiimasktimmy", RunStatus.done),
                                    ("2925070238", "annan", RunStatus.done),
                                    ("2925070238", "skiimasktimmy", RunStatus.running)]:
        run = Run(vod_id=vod_id, channel="skiimasktimmy", status=status)
        db.add(run)
        db.commit()
        ids.append(run.id)
        log.add_run(run.id, vod_id, profile, "Bodycam")
    db.close()
    listed = [(r["run_id"], r["vod_id"], r["profile"], r["status"]) for r in client.get("/campaign/runs").json()]
    assert listed == [(ids[4], "2925070238", "skiimasktimmy", "running"),
                      (ids[3], "2925070238", "annan", "done"),
                      (ids[2], "2925991945", "skiimasktimmy", "done")]
    # En gammal körning går fortfarande att öppna med sin länk.
    assert client.get(f"/campaign/runs/{ids[0]}").json()["status"] == "failed"


# ---------- 2. poster ----------


@needs_ffmpeg
def test_poster_is_made_once_and_again_when_the_clip_is_newer(client, settings) -> None:  # noqa: F811
    video = make_clip(settings.out_dir / "123" / "klara" / "clip_01_a.mp4", seconds=3)
    clip_id = _clip(settings, file=str(video))
    response = client.get(f"/campaign/clips/{clip_id}/poster")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    poster = video.with_name(".clip_01_a.poster.jpg")
    assert poster.read_bytes() == response.content
    assert probe(poster)["width"] == 360

    old = poster.stat().st_mtime_ns
    os.utime(poster, ns=(old - 10**9, old - 10**9))
    os.utime(video, ns=(old - 2 * 10**9, old - 2 * 10**9))
    client.get(f"/campaign/clips/{clip_id}/poster")
    assert poster.stat().st_mtime_ns == old - 10**9  # inte gjord igen

    os.utime(video, ns=(old, old))
    client.get(f"/campaign/clips/{clip_id}/poster")
    assert poster.stat().st_mtime_ns > old - 10**9  # klippet är nyare: gjord igen


def test_poster_without_file_or_outside_out_is_404(client, settings, tmp_path) -> None:  # noqa: F811
    assert client.get(f"/campaign/clips/{_clip(settings)}/poster").status_code == 404
    outside = tmp_path / "x.mp4"
    outside.write_bytes(b"x")
    assert client.get(f"/campaign/clips/{_clip(settings, file=str(outside))}/poster").status_code == 404
    assert client.get("/campaign/clips/999/poster").status_code == 404


def test_every_clip_has_a_poster_url_with_the_same_version(client, settings) -> None:  # noqa: F811
    clip_id = _clip(settings)
    (clip,) = client.get("/campaign/clips").json()
    version = clip["video_url"].split("?v=")[1]
    assert clip["poster_url"] == f"/campaign/clips/{clip_id}/poster?v={version}"


# ---------- 3. förlopp i transkriberingen ----------


@needs_ffmpeg
def test_transcribe_vod_tells_each_part(settings) -> None:
    from campaign.transcribe import transcribe_vod

    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    parts: list[tuple[int, int]] = []
    transcribe_vod(mp4, "123", mp4.parent / "transcript.json", post=lambda audio, fields: {"segments": []},
                   model="m", language="en", chunk_s=3, on_part=lambda n, total: parts.append((n, total)))
    assert parts == [(1, 2), (2, 2)]


def test_make_clips_emits_one_event_per_part_between_15_and_40(settings, monkeypatch) -> None:
    from campaign import pipeline
    from campaign.profile import load_profile

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.berget_api_key = "test"
    mp4 = settings.work_dir / "123" / "123.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()

    def fake_transcribe(mp4, vod_id, out, *, on_part=None, **kwargs):
        for n in (1, 2, 3, 4):
            on_part(n, 4)
        out.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pipeline, "download_vod", lambda vod_id, work_dir, **k: mp4)
    monkeypatch.setattr(pipeline, "transcribe_vod", fake_transcribe)
    monkeypatch.setattr(pipeline, "process_vod", lambda *a, **k: (mp4.parent / "candidates.json").write_text("[]"))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: [])
    events: list[tuple] = []
    pipeline.make_clips("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, game=None,
                        emit=lambda *e: events.append(e))
    parts = [e for e in events if e[2].startswith("Part ")]
    assert [e[2] for e in parts] == ["Part 1 of 4", "Part 2 of 4", "Part 3 of 4", "Part 4 of 4"]
    assert {e[0] for e in parts} == {"transcribe"}
    assert all(15.0 <= e[1] <= 40.0 for e in parts)
    assert [e[1] for e in parts] == sorted(e[1] for e in parts)


def test_progress_has_the_latest_message_per_step_and_for_the_run() -> None:
    start = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    events = [(stage, pct, start + timedelta(minutes=i), message) for i, (stage, pct, message) in enumerate([
        ("download", 5.0, "Downloading the VOD"), ("transcribe", 15.0, "Transcribing with Berget"),
        ("transcribe", 15.0, "Part 1 of 3"), ("transcribe", 23.3, "Part 2 of 3")])]
    progress = estimate.progress("running", events, 13950, now=start)
    steps = {s["id"]: s["message"] for s in progress["steps"]}
    assert steps == {"download": "Downloading the VOD", "transcribe": "Part 2 of 3", "moments": None, "select": None,
                     "clip": None, "finish": None}
    assert progress["message"] == "Part 2 of 3"
    # clipsmiths egna händelser har ingen text: stegets senaste text ligger kvar
    progress = estimate.progress("running", [*events, ("transcribe", 30.0, start, "")], 13950, now=start)
    assert {s["id"]: s["message"] for s in progress["steps"]}["transcribe"] == "Part 2 of 3"
    assert progress["message"] is None


def test_run_detail_has_the_messages(client, settings) -> None:  # noqa: F811
    from clipsmith.db.models import PipelineEvent
    from clipsmith.db.session import get_session

    run_id = _done_run(settings, "2925070238")
    db = get_session()
    db.add(PipelineEvent(run_id=run_id, stage="transcribe", pct=20.0, message="Del 2 av 5"))
    db.commit()
    db.close()
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert run["message"] == "Del 2 av 5"
    assert {s["id"]: s["message"] for s in run["steps"]}["transcribe"] == "Del 2 av 5"


# ---------- 4. intäkter ----------


def test_summary_has_earnings_per_clip_month_and_paid(client, settings) -> None:  # noqa: F811
    cheap = (settings.profiles_dir / "skiimasktimmy.yaml").read_text().replace("cpm_usd: 1.00", "cpm_usd: 0.50")
    (settings.profiles_dir / "billig.yaml").write_text(cheap)
    log = ClipLog(settings.data_dir / "klipp.db")

    def clip(views, status="inskickad", profile="skiimasktimmy", month="2026-11"):
        clip_id = log.add(vod_id="1", profile=profile, file="a.mp4", title="t", caption="c", status=status)
        log.update(clip_id, views=views)
        with sqlite3.connect(settings.data_dir / "klipp.db") as db:
            db.execute("UPDATE clips SET created = ? WHERE id = ?", (f"{month}-05T10:00:00+00:00", clip_id))
        return clip_id

    a = clip(2500, month="2026-10")
    b = clip(1500)  # under min_views
    c = clip(1_000_000)  # taket 150
    d = clip(3000, status="betald")
    e = clip(3000, profile="billig")  # 1.5 är under min_payout 2
    log.add(vod_id="1", profile="skiimasktimmy", file="b.mp4", title="t", caption="c")  # inga visningar

    earnings = client.get("/campaign/summary").json()["earnings"]
    assert earnings["per_clip"] == [{"id": a, "views": 2500, "usd": 2.5}, {"id": b, "views": 1500, "usd": 0.0},
                                    {"id": c, "views": 1_000_000, "usd": 150.0},
                                    {"id": d, "views": 3000, "usd": 3.0}, {"id": e, "views": 3000, "usd": 0.0}]
    assert earnings["estimated_usd"] == 155.5
    assert earnings["paid_usd"] == 3.0
    assert earnings["by_month"] == [{"month": "2026-10", "usd": 2.5}, {"month": "2026-11", "usd": 153.0}]


def test_summary_without_clips_has_zero_earnings(client) -> None:  # noqa: F811
    assert client.get("/campaign/summary").json()["earnings"] == {
        "estimated_usd": 0.0, "paid_usd": 0.0, "by_month": [], "per_clip": []}


def test_twitchs_placeholder_image_gives_no_image_in_the_vod_facts(monkeypatch) -> None:
    from campaign import vodinfo

    monkeypatch.setattr(vodinfo, "_cache", {})
    monkeypatch.setattr(vodinfo.twitch, "get_video", lambda vod_id: {"title": "Live", "lengthSeconds": 60})
    monkeypatch.setattr(vodinfo.twitch, "gql_query", lambda q: {"data": {"video": {
        "previewThumbnailURL": "https://vod-secure.twitch.tv/_404/404_processing_640x360.png",
        "owner": {"login": "skiimasktimmy", "displayName": "SkiiMaskTimmy"}}}})
    assert vodinfo.vod_facts("2926877904")["thumbnail_url"] is None


def test_earnings_count_each_post_on_its_own_and_use_what_whop_paid(client, settings) -> None:  # noqa: F811
    """min_views och taket gäller varje inlägg. Utbetalt är vad Whop betalade, när det är ifyllt."""
    log = ClipLog(settings.data_dir / "klipp.db")
    two = log.add(vod_id="1", profile="skiimasktimmy", file="a.mp4", title="t", caption="c", status="godkand")
    tiktok = log.add_post(two, "tiktok", "https://www.tiktok.com/@r/video/1")
    reels = log.add_post(two, "instagram_reels", "https://www.instagram.com/reel/1")
    log.update_post(tiktok, views=1500)
    log.update_post(reels, views=1500)  # 3 000 tillsammans, men inget inlägg når 2 000
    paid = log.add(vod_id="1", profile="skiimasktimmy", file="b.mp4", title="t", caption="c", status="godkand")
    shorts = log.add_post(paid, "youtube_shorts", "https://www.youtube.com/shorts/1")
    log.update_post(shorts, views=3000, status="betald", paid_usd=4.2)
    guess = log.add(vod_id="1", profile="skiimasktimmy", file="c.mp4", title="t", caption="c", status="godkand")
    log.update_post(log.add_post(guess, "tiktok", "https://www.tiktok.com/@r/video/2"), views=5000, status="betald")

    earnings = client.get("/campaign/summary").json()["earnings"]
    assert {c["id"]: c["usd"] for c in earnings["per_clip"]} == {two: 0.0, paid: 3.0, guess: 5.0}
    assert earnings["estimated_usd"] == 8.0
    assert earnings["paid_usd"] == 9.2  # 4.2 som Whop betalade, plus uppskattade 5.0 där beloppet saknas

    # Ångra på Betald: beloppet står kvar i inlägget, men det är inte utbetalt längre.
    log.update_post(shorts, status="analytics_inskickad")
    assert client.get("/campaign/summary").json()["earnings"]["paid_usd"] == 5.0
