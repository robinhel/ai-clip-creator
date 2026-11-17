"""Schemalagda uppladdningar. Låtsas-YouTube, låtsas-TikTok och låtsas-notis: inga riktiga anrop."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, schedule, schedule_api, scout
from campaign.cliplog import ClipLog
from campaign.tiktok import TikTok
from campaign.youtube import BUSY, YouTube, YouTubeError

from .conftest import set_helper, set_profile

YT_FIELDS = {"title": "Big play", "description": "#ad", "privacy": "public", "made_for_kids": False,
             "notify_subscribers": True}


class FakeYouTube(YouTube):
    calls: ClassVar[list[tuple]] = []
    error: YouTubeError | None = None
    today = 0

    def upload(self, clip_id, path, channel_id, video, duration_s):
        FakeYouTube.calls.append((clip_id, channel_id, video))
        if FakeYouTube.error:
            raise FakeYouTube.error
        return {"clip_id": clip_id, "video_id": "v1", "url": "https://www.youtube.com/shorts/v1"}

    def uploads_today(self):
        return FakeYouTube.today


class FakeTikTok(TikTok):
    calls: ClassVar[list[tuple]] = []
    publish = False

    def can_publish(self, open_id):
        return FakeTikTok.publish

    def upload(self, clip_id, path, open_id=None, post=None, duration_s=0):
        FakeTikTok.calls.append((clip_id, open_id, post))
        return {"clip_id": clip_id, "publish_id": "p1", "status": "processing"}


@pytest.fixture
def notes(monkeypatch) -> list[tuple]:
    sent: list[tuple] = []
    monkeypatch.setattr(schedule, "notify_mac", lambda message, url=None: sent.append((message, url)))
    monkeypatch.setattr(schedule, "YouTube", FakeYouTube)
    monkeypatch.setattr(schedule, "TikTok", FakeTikTok)
    FakeYouTube.calls, FakeYouTube.error, FakeYouTube.today = [], None, 0
    FakeTikTok.calls, FakeTikTok.publish = [], False
    return sent


@pytest.fixture
def clip_id(settings) -> int:
    path = settings.out_dir / "x" / "klipp.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mp4")
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="v", profile="skiimasktimmy", file=str(path), title="Timmy lands it", caption="c",
        status="godkand", start_s=10.0, end_s=40.0)


@pytest.fixture
def client(settings, notes) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(schedule_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _at(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def _post(client, clip_id, platform="youtube", at=None, fields=None, account="UC1"):
    return client.post(f"/campaign/clips/{clip_id}/schedule", json={
        "platform": platform, "at": at or _at(timedelta(hours=1)), "account": account,
        "fields": YT_FIELDS if fields is None else fields})


def _row(settings, post_id) -> dict:
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone())


def _run(settings, at: str, after: timedelta) -> None:
    schedule.Schedule(settings).run_due(now=datetime.fromisoformat(at) + after)


def test_post_runs_after_its_time_and_only_once(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    answer = _post(client, clip_id, at=at)
    assert answer.status_code == 201, answer.text
    post = answer.json()
    assert post["status"] == "waiting" and post["clip_title"] == "Timmy lands it"
    assert post["profile"] == "skiimasktimmy" and post["account"] == "UC1"

    _run(settings, at, -timedelta(seconds=5))
    assert FakeYouTube.calls == [] and _row(settings, post["id"])["status"] == "waiting"

    _run(settings, at, timedelta(seconds=10))
    _run(settings, at, timedelta(seconds=40))
    assert FakeYouTube.calls == [(clip_id, "UC1", {**YT_FIELDS})]
    row = _row(settings, post["id"])
    assert row["status"] == "done" and json.loads(row["result"])["video_id"] == "v1"
    assert len(notes) == 1
    message, url = notes[0]
    assert re.fullmatch(r"Timmy lands it är uppe på YouTube\. Skicka in länken i Content Rewards före kl\. "
                        r"\d\d:\d\d\.", message), message
    assert url == "http://localhost:3000/queue"


def test_status_is_running_before_the_upload(client, settings, clip_id, notes, monkeypatch):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    seen = []

    def upload(self, clip_id, path, channel_id, video, duration_s):
        seen.append(_row(settings, post_id)["status"])
        # Ett andra varv medan uppladdningen pågår laddar inte upp igen.
        schedule.Schedule(settings).run_due(now=datetime.fromisoformat(at) + timedelta(seconds=30))
        return {"video_id": "v1"}

    monkeypatch.setattr(FakeYouTube, "upload", upload)
    _run(settings, at, timedelta(seconds=1))
    assert seen == ["running"]
    assert _row(settings, post_id)["status"] == "done"


@pytest.mark.parametrize("change", [{"status": "nekad"}, {"problems": json.dumps(["Captionen saknar #ad."])}])
def test_rejected_or_broken_clip_fails_at_the_time(client, settings, clip_id, notes, change):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        for key, value in change.items():
            db.execute(f"UPDATE clips SET {key} = ? WHERE id = ?", (value, clip_id))
    _run(settings, at, timedelta(seconds=1))
    row = _row(settings, post_id)
    assert row["status"] == "failed" and row["error"]
    assert FakeYouTube.calls == []
    assert notes == [(f"Timmy lands it laddades inte upp: {row['error']}", "http://localhost:3000/queue")]


def test_late_under_six_hours_runs_and_says_late(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    _run(settings, at, timedelta(hours=5))
    assert _row(settings, post_id)["status"] == "done"
    assert "försenad" in notes[0][0].lower()
    listed = client.get("/campaign/schedule").json()
    assert listed[0]["late"] is True


def test_late_over_six_hours_fails(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    _run(settings, at, timedelta(hours=6, minutes=1))
    row = _row(settings, post_id)
    assert row["status"] == "failed" and row["error"] == "Missad: Macen var avstängd vid tiden."
    assert FakeYouTube.calls == []
    assert notes == [("Timmy lands it laddades inte upp: Missad: Macen var avstängd vid tiden.",
                      "http://localhost:3000/queue")]


def test_youtube_daily_limit_is_checked_at_the_time(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    FakeYouTube.today = 100
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "failed" and FakeYouTube.calls == []


def test_upload_error_fails_with_its_text(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    FakeYouTube.error = YouTubeError("YouTube svarade inte. Försök igen om en stund.")
    _run(settings, at, timedelta(seconds=1))
    row = _row(settings, post_id)
    assert row["status"] == "failed" and row["error"] == "YouTube svarade inte. Försök igen om en stund."


def test_tiktok_without_direct_post_becomes_a_reminder(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, "tiktok", at=at, fields={}, account="open1").json()["id"]
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "reminded"
    assert FakeTikTok.calls == []
    assert notes == [("Dags att posta Timmy lands it på TikTok", "http://localhost:3000/queue")]


def test_tiktok_with_direct_post_posts_with_saved_fields(client, settings, clip_id, notes):
    FakeTikTok.publish = True
    at = _at(timedelta(hours=1))
    fields = {"title": "Big play #ad", "privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True}
    post_id = _post(client, clip_id, "tiktok", at=at, fields=fields, account="open1").json()["id"]
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "done"
    assert FakeTikTok.calls[0][1] == "open1"
    assert FakeTikTok.calls[0][2]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert notes[0][0].startswith("Timmy lands it skickades till TikTok. Skicka in länken i Content Rewards före kl. ")


def test_reminder_stays_a_reminder_when_the_account_can_publish_later(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, "tiktok", at=at, fields={}, account="open1").json()["id"]
    FakeTikTok.publish = True  # kontot fick video.publish efter att påminnelsen schemalades
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "reminded"
    assert FakeTikTok.calls == []


def test_another_upload_going_on_waits_for_the_next_round(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    FakeYouTube.error = YouTubeError(BUSY, 409)
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "waiting" and notes == []
    FakeYouTube.error = None
    _run(settings, at, timedelta(seconds=31))
    assert _row(settings, post_id)["status"] == "done"


def _youtube_row(settings, clip_id, channel="UC1", status="uploaded", age=timedelta(hours=1)):
    """En uppladdning med den vanliga YouTube-knappen."""
    with YouTube(settings)._db() as db:
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, status, created) VALUES (?, ?, ?, ?)",
                   (clip_id, channel, status, (datetime.now(UTC) - age).isoformat(timespec="seconds")))


def _tiktok_row(settings, clip_id, status="processing", mode="direct", open_id="open1"):
    with TikTok(settings)._db() as db:
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, created, open_id, mode) "
                   "VALUES (?, 'p0', ?, ?, ?, ?)",
                   # 30 minuter: äldre än knappens spärr, yngre än gränsen då TikTok räknas som fast (1 h)
                   (clip_id, status, (datetime.now(UTC) - timedelta(minutes=30)).isoformat(), open_id, mode))


def test_clip_already_on_the_youtube_channel_cannot_be_scheduled(client, settings, clip_id):
    _youtube_row(settings, clip_id, status="failed")
    _youtube_row(settings, clip_id, channel="UC2")
    assert _post(client, clip_id).status_code == 201  # en misslyckad, eller en annan kanal, spärrar inte
    client.delete("/campaign/schedule/1")
    _youtube_row(settings, clip_id)
    answer = _post(client, clip_id)
    assert answer.status_code == 409 and answer.json()["detail"] == "Klippet finns redan på kanalen."


def test_button_upload_after_scheduling_stops_the_scheduled_upload(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    _youtube_row(settings, clip_id)  # Robin tryckte på knappen kl. 19, schemat är kl. 20
    _run(settings, at, timedelta(seconds=1))
    row = _row(settings, post_id)
    assert row["status"] == "failed" and row["error"] == "Klippet finns redan på kanalen."
    assert FakeYouTube.calls == []
    assert notes == [("Timmy lands it laddades inte upp: Klippet finns redan på kanalen.",
                      "http://localhost:3000/queue")]


def test_clip_already_direct_posted_to_the_tiktok_account_is_stopped(client, settings, clip_id, notes):
    FakeTikTok.publish = True
    fields = {"title": "Big play #ad", "privacy_level": "PUBLIC_TO_EVERYONE"}
    _tiktok_row(settings, clip_id, mode="draft")
    _tiktok_row(settings, clip_id, status="failed")
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, "tiktok", at=at, fields=fields, account="open1").json()["id"]
    _tiktok_row(settings, clip_id)
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["error"] == "Klippet finns redan på kanalen." and FakeTikTok.calls == []
    answer = _post(client, clip_id, "tiktok", fields=fields, account="open1")
    assert answer.status_code == 409 and answer.json()["detail"] == "Klippet finns redan på kanalen."


@pytest.mark.parametrize(("url", "opens"), [
    ("https://contentrewards.com/c/x", True), ("http://localhost:3000/queue", True),
    ("http://evil.example/queue", False), ("file:///etc/passwd", False), (None, False)])
def test_notice_opens_only_safe_addresses(monkeypatch, url, opens):
    calls = []
    monkeypatch.setattr(scout.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(scout.sys, "platform", "darwin")
    monkeypatch.setattr(scout.shutil, "which", lambda name: "/bin/terminal-notifier")
    scout.notify_mac("Hej", url)
    assert ("-open" in calls[0]) is opens


def test_second_waiting_post_for_same_platform_is_409(client, clip_id):
    assert _post(client, clip_id).status_code == 201
    assert _post(client, clip_id).status_code == 409
    assert _post(client, clip_id, "tiktok", fields={}, account=None).status_code == 201


def test_time_in_the_past_is_400(client, clip_id):
    answer = _post(client, clip_id, at=_at(-timedelta(minutes=1)))
    assert answer.status_code == 400 and "passerat" in answer.json()["detail"]


@pytest.mark.parametrize("fields", [{**YT_FIELDS, "privacy": None}, {k: v for k, v in YT_FIELDS.items()
                                                                     if k != "made_for_kids"}])
def test_invalid_youtube_fields_are_400(client, clip_id, fields):
    assert _post(client, clip_id, fields=fields).status_code == 400


def test_only_approved_clips_without_problems_can_be_scheduled(client, settings, clip_id):
    ClipLog(settings.data_dir / "klipp.db").update(clip_id, status="renderad")
    assert _post(client, clip_id).status_code == 400


def test_delete_only_waiting(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    first = _post(client, clip_id, at=at).json()["id"]
    assert client.delete(f"/campaign/schedule/{first}").status_code == 200
    assert _row(settings, first)["status"] == "cancelled"
    assert client.delete(f"/campaign/schedule/{first}").status_code == 409
    second = _post(client, clip_id, at=at).json()["id"]  # den avbrutna spärrar inte en ny
    _run(settings, at, timedelta(seconds=1))
    assert client.delete(f"/campaign/schedule/{second}").status_code == 409
    assert client.delete("/campaign/schedule/999").status_code == 404


def test_time_is_saved_as_utc_and_the_posted_zone_is_kept(client, settings, clip_id):
    utc = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    plus_one = (utc + timedelta(hours=1)).replace(tzinfo=None).isoformat() + "+01:00"  # samma ögonblick
    post = _post(client, clip_id, at=plus_one).json()
    assert post["at"] == utc.isoformat()
    assert _row(settings, post["id"])["at"] == utc.isoformat()
    # En tid utan tidszon går inte att tolka säkert.
    assert _post(client, clip_id, "tiktok", at=utc.replace(tzinfo=None).isoformat(), fields={}).status_code == 400


def test_list_waiting_first_then_finished(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    done = _post(client, clip_id, at=at).json()["id"]
    _run(settings, at, timedelta(seconds=1))
    waiting = _post(client, clip_id, at=_at(timedelta(hours=2))).json()["id"]
    listed = client.get("/campaign/schedule").json()
    assert [p["id"] for p in listed] == [waiting, done]
    assert set(listed[0]) == {"id", "clip_id", "clip_title", "profile", "platform", "account", "at", "fields",
                              "status", "error", "result", "late", "warning"}
    assert listed[0]["fields"]["privacy"] == "public" and listed[1]["result"]["video_id"] == "v1"


def test_thread_does_not_start_in_tests_or_when_turned_off(settings, monkeypatch):
    assert schedule.start(settings) is None
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setenv("KLIPP_SCHEDULE", "0")
    assert schedule.start(settings) is None


def test_interrupted_running_post_fails_on_start(client, settings, clip_id, notes, monkeypatch):
    post_id = _post(client, clip_id).json()["id"]
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE scheduled_posts SET status = 'running' WHERE id = ?", (post_id,))
    schedule.Schedule(settings).fail_interrupted()
    assert _row(settings, post_id)["status"] == "failed"


# ---------- hjälpen för inskick och kampanjens plattformar ----------

CR = "https://contentrewards.com/c/campaigns/abc"


def test_with_the_helper_the_notice_says_the_time_in_swedish_time(client, settings, clip_id, notes, monkeypatch):
    set_profile(settings, links={"content_rewards": CR})
    at = _at(timedelta(hours=1))
    _post(client, clip_id, at=at)
    monkeypatch.setattr(schedule, "_now", lambda: datetime(2026, 11, 20, 16, 53, 10, tzinfo=UTC))  # 17:53 i Sverige
    _run(settings, at, timedelta(seconds=1))
    assert notes == [("Timmy lands it är uppe på YouTube. Skicka in länken i Content Rewards före kl. 18:23.",
                      "http://localhost:3000/queue")]


def test_without_the_helper_the_notice_is_as_before(client, settings, clip_id, notes):
    set_helper(settings, False)
    set_profile(settings, links={"content_rewards": CR})
    at = _at(timedelta(hours=1))
    _post(client, clip_id, at=at)
    _run(settings, at, timedelta(seconds=1))
    assert notes == [("Timmy lands it är uppe på YouTube. Skicka in länken i Content Rewards inom 30 minuter.", CR)]


def test_a_reminder_notice_does_not_change_with_the_helper(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    _post(client, clip_id, "tiktok", at=at, fields={}, account="open1")
    _run(settings, at, timedelta(seconds=1))
    assert notes == [("Dags att posta Timmy lands it på TikTok", "http://localhost:3000/queue")]


@pytest.mark.parametrize(("platforms", "platform", "text"), [
    (["tiktok"], "youtube", "Kampanjen postar inte på YouTube."),
    (["youtube_shorts"], "tiktok", "Kampanjen postar inte på TikTok.")])
def test_a_platform_the_campaign_does_not_use_cannot_be_scheduled(client, settings, clip_id, platforms, platform,
                                                                  text):
    set_profile(settings, platforms=platforms)
    answer = _post(client, clip_id, platform, fields=YT_FIELDS if platform == "youtube" else {})
    assert (answer.status_code, answer.json()["detail"]) == (400, text)


def test_a_platform_removed_after_scheduling_fails_at_the_time(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    set_profile(settings, platforms=["tiktok"])
    _run(settings, at, timedelta(seconds=1))
    row = _row(settings, post_id)
    assert (row["status"], row["error"]) == ("failed", "Kampanjen postar inte på YouTube.")
    assert FakeYouTube.calls == []


def test_a_clip_posted_on_youtube_can_still_be_sent_to_tiktok_at_the_time(client, settings, clip_id, notes):
    FakeTikTok.publish = True
    at = _at(timedelta(hours=1))
    fields = {"title": "Big play #ad", "privacy_level": "PUBLIC_TO_EVERYONE"}
    post_id = _post(client, clip_id, "tiktok", at=at, fields=fields, account="open1").json()["id"]
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "youtube_shorts", "https://www.youtube.com/shorts/v1")
    _run(settings, at, timedelta(seconds=1))
    assert _row(settings, post_id)["status"] == "done" and len(FakeTikTok.calls) == 1



@pytest.mark.parametrize("status", ["nekad", "renderad", "underkand"])
def test_clip_file_still_stops_a_clip_that_is_not_approved(settings, clip_id, status):
    log = ClipLog(settings.data_dir / "klipp.db")
    log.update(clip_id, status=status)
    with pytest.raises(schedule.ScheduleError):
        schedule.clip_file(settings, log.get(clip_id), "youtube")


def _direct_row(settings, clip_id, status):
    with TikTok(settings)._db() as db:
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, created, open_id, mode) "
                   "VALUES (?, 'p', ?, ?, 'open1', 'direct')", (clip_id, status, datetime.now(UTC).isoformat()))


def test_each_round_asks_tiktok_about_direct_posts_that_are_processing(settings, clip_id, notes, monkeypatch):
    asked = []
    monkeypatch.setattr(FakeTikTok, "latest", lambda self, clip: asked.append(clip))
    other = ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="v", profile="skiimasktimmy", file="x.mp4", title="t", caption="c", status="godkand")
    _direct_row(settings, clip_id, "processing")
    _direct_row(settings, other, "published")
    schedule.Schedule(settings).run_due()
    assert asked == [clip_id]


def test_the_tiktok_notice_uses_the_upload_time(client, settings, clip_id, notes, monkeypatch):
    FakeTikTok.publish = True
    monkeypatch.setattr(FakeTikTok, "upload", lambda self, clip, path, account, post, duration: {
        "publish_id": "p1", "status": "processing", "created": "2026-11-20T16:40:00+00:00"})
    at = _at(timedelta(hours=1))
    fields = {"title": "Big play #ad", "privacy_level": "PUBLIC_TO_EVERYONE"}
    _post(client, clip_id, "tiktok", at=at, fields=fields, account="open1")
    _run(settings, at, timedelta(seconds=1))
    assert notes[0][0] == "Timmy lands it skickades till TikTok. Skicka in länken i Content Rewards före kl. 18:10."


# ---------- spärrarna för konton ----------


def _another_clip(settings, name: str) -> int:
    path = settings.out_dir / name / "klipp.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mp4")
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="v", profile="skiimasktimmy", file=str(path), title=name, caption="c",
        status="godkand", start_s=10.0, end_s=40.0)


def _stockholm(days: int, hour: int, minute: int = 0) -> str:
    """En tid i svensk tid, days dagar fram, som ISO med zon."""
    day = (datetime.now(scout.STOCKHOLM) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=scout.STOCKHOLM).isoformat()


def test_an_account_the_campaign_does_not_list_is_refused(client, settings, clip_id):
    set_profile(settings, accounts={"youtube_shorts": ["UC2"]})
    answer = _post(client, clip_id, account="UC1")
    assert answer.status_code == 400
    assert "UC2" in answer.json()["detail"]
    assert _post(client, clip_id, account="UC2").status_code == 201


def test_the_campaign_account_can_be_named_by_its_handle(client, settings, clip_id, monkeypatch):
    monkeypatch.setattr(FakeYouTube, "accounts",
                        lambda self: {"UC1": {"handle": "@vicecitywatchers", "title": "ViceCityWatchers"}})
    set_profile(settings, accounts={"youtube_shorts": ["@ViceCityWatchers"]})
    assert _post(client, clip_id, account="UC1").status_code == 201


def test_a_tiktok_reminder_without_account_is_allowed_with_campaign_accounts(client, settings, clip_id):
    set_profile(settings, accounts={"tiktok": ["open9"]})
    assert _post(client, clip_id, "tiktok", fields={}, account=None).status_code == 201


def test_the_day_limit_counts_posts_on_the_same_account_and_swedish_day(client, settings, clip_id):
    set_profile(settings, posting={"max_per_day": 2})
    second, third, fourth, fifth = (_another_clip(settings, n) for n in ("b", "c", "d", "e"))
    cancelled = _post(client, fifth, at=_stockholm(1, 8)).json()["id"]
    client.delete(f"/campaign/schedule/{cancelled}")  # en avbruten post räknas inte
    assert _post(client, clip_id, at=_stockholm(1, 10)).status_code == 201
    assert _post(client, second, at=_stockholm(1, 14)).status_code == 201
    full = _post(client, third, at=_stockholm(1, 23, 30))
    assert full.status_code == 400
    assert "högst 2" in full.json()["detail"]
    # 00:30 svensk tid är nästa dag, fast UTC-datumet är detsamma som 23:30 dagen före på vintern.
    assert _post(client, third, at=_stockholm(2, 0, 30)).status_code == 201
    assert _post(client, fourth, at=_stockholm(1, 18), account="UC2").status_code == 201  # ett annat konto


def test_uploads_with_the_buttons_count_but_tiktok_drafts_do_not(client, settings, clip_id):
    set_profile(settings, posting={"max_per_day": 1})
    other = _another_clip(settings, "b")
    created = datetime.fromisoformat(_stockholm(1, 9)).astimezone(UTC).isoformat(timespec="seconds")
    with TikTok(settings)._db() as db:
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, created, open_id, mode) "
                   "VALUES (?, 'p0', 'uploaded', ?, 'open1', 'draft')", (other, created))
    with YouTube(settings)._db() as db:
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, status, created) VALUES (?, 'UC1', 'failed', ?)",
                   (other, created))
    assert _post(client, clip_id, "tiktok", at=_stockholm(1, 12), fields={}, account="open1").status_code == 201
    assert _post(client, clip_id, at=_stockholm(1, 12)).status_code == 201  # den misslyckade räknas inte
    with YouTube(settings)._db() as db:
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, status, created) VALUES (?, 'UC2', 'done', ?)",
                   (other, created))
    third = _another_clip(settings, "c")
    assert _post(client, third, at=_stockholm(1, 15), account="UC2").status_code == 400


def test_the_youtube_button_refuses_an_account_the_campaign_does_not_list(settings, clip_id, notes, monkeypatch):
    from campaign import youtube_api

    monkeypatch.setattr(youtube_api, "YouTube", FakeYouTube)
    set_profile(settings, accounts={"youtube_shorts": ["UC2"]})
    app = FastAPI()
    app.include_router(youtube_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    answer = TestClient(app).post(f"/campaign/clips/{clip_id}/youtube", json={**YT_FIELDS, "channel_id": "UC1"})
    assert answer.status_code == 400
    assert FakeYouTube.calls == []


def test_a_post_close_to_another_on_the_same_account_gets_a_warning(client, settings, clip_id):
    other, far = _another_clip(settings, "b"), _another_clip(settings, "c")
    first = _post(client, clip_id, at=_stockholm(1, 12)).json()
    assert first["warning"] is None
    close = _post(client, other, at=_stockholm(1, 13)).json()
    assert "120 minuter" in close["warning"]
    assert _post(client, far, at=_stockholm(1, 16)).json()["warning"] is None
    listed = {post["id"]: post["warning"] for post in client.get("/campaign/schedule").json()}
    assert listed[first["id"]] is not None  # den första ligger nu också för nära


# ---------- ta bort ett godkänt klipp från Att posta ----------


def test_removing_an_approved_clip_cancels_its_waiting_upload(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "nekad"
    assert _row(settings, post_id)["status"] == "cancelled"
    _run(settings, at, timedelta(seconds=10))
    assert FakeYouTube.calls == []  # inget laddas upp för ett borttaget klipp


def test_a_clip_that_is_uploading_now_cannot_be_removed(client, settings, clip_id, notes):
    waiting = _post(client, clip_id, "youtube").json()["id"]
    running = _post(client, clip_id, "tiktok", fields={"title": "x"}, account="open1").json()["id"]
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE scheduled_posts SET status = 'running' WHERE id = ?", (running,))
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"})
    assert answer.status_code == 409
    assert "laddas upp" in answer.json()["detail"]
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["status"] == "godkand"
    assert _row(settings, waiting)["status"] == "waiting"  # inget ändras när svaret är nej


def test_removing_can_be_undone_but_the_rules_still_apply(client, settings, clip_id, notes):
    ClipLog(settings.data_dir / "klipp.db").update(clip_id, format="clutch")  # ett godkänt klipp har ett format
    post_id = _post(client, clip_id).json()["id"]
    client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"})
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "godkand"
    assert _row(settings, post_id)["status"] == "cancelled"  # den avbrutna uppladdningen startar inte igen

    log = ClipLog(settings.data_dir / "klipp.db")
    log.update(clip_id, status="nekad", problems=["Captionen saknar twitch.tv/skiimasktimmy"])
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"})
    assert answer.status_code == 400 and "problem" in answer.json()["detail"]


def test_undoing_the_approval_also_cancels_the_waiting_upload(client, settings, clip_id, notes):
    at = _at(timedelta(hours=1))
    post_id = _post(client, clip_id, at=at).json()["id"]
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "renderad"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "renderad"
    assert _row(settings, post_id)["status"] == "cancelled"
    _run(settings, at, timedelta(seconds=10))
    assert FakeYouTube.calls == []


def test_undoing_the_approval_is_refused_while_the_clip_uploads(client, settings, clip_id, notes):
    post_id = _post(client, clip_id).json()["id"]
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE scheduled_posts SET status = 'running' WHERE id = ?", (post_id,))
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "renderad"})
    assert answer.status_code == 409 and "laddas upp" in answer.json()["detail"]
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["status"] == "godkand"
