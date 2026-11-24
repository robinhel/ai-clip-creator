"""Samma klipp på flera konton på samma plattform (funktion 7). Låtsas-YouTube, låtsas-TikTok och låtsas-Claude:
inga riktiga anrop."""

from __future__ import annotations

import sqlite3
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, preferences
from campaign.cli import main
from campaign.cliplog import ClipLog

from .conftest import needs_ffmpeg, set_helper, set_profile

TT_A = "https://www.tiktok.com/@a/video/111"
TT_B = "https://www.tiktok.com/@b/video/222"
YT = "https://www.youtube.com/shorts/vid1"


def _clip(settings, name: str = "v", title: str = "Timmy lands it") -> int:
    path = settings.out_dir / name / "klipp.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="v", profile="skiimasktimmy", file=str(path), title=title, caption="c", status="godkand",
        start_s=10.0, end_s=40.0)


@pytest.fixture
def client(settings) -> TestClient:
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


# ---------- 1. inlägg per konto ----------


def _old_db(path) -> None:
    """En logg från före kontona: posts med UNIQUE (clip_id, platform), user_version 1."""
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id TEXT, profile TEXT, start_s REAL, end_s REAL,
            title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT, status TEXT, problems TEXT,
            warnings TEXT, review TEXT, platform TEXT, url TEXT, views INTEGER, likes INTEGER, comments INTEGER,
            shares INTEGER, note TEXT, created TEXT, updated TEXT)""")
        db.execute("""CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT, url TEXT,
            status TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
            paid_usd REAL, created TEXT, updated TEXT, UNIQUE (clip_id, platform))""")
        db.execute("""CREATE TABLE tiktok_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, publish_id TEXT, status TEXT, error TEXT,
            created TEXT, checked TEXT, open_id TEXT, mode TEXT DEFAULT 'draft', privacy_level TEXT, post_id TEXT)""")
        db.execute("""CREATE TABLE youtube_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, channel_id TEXT, video_id TEXT,
            status TEXT, error TEXT, privacy TEXT, created TEXT, checked TEXT)""")
        for i in (1, 2, 3):
            db.execute("INSERT INTO clips (id, vod_id, profile, title, status) VALUES (?, 'v', 'skiimasktimmy', 't', "
                       "'postad')", (i,))
        rows = [(1, "tiktok", TT_A, "inskickad", 3000, 1.5), (1, "youtube_shorts", YT, "postad", 400, None),
                (2, "tiktok", "https://www.tiktok.com/@x/video/999", "postad", 50, None),  # för hand
                (3, "instagram_reels", "https://www.instagram.com/reel/abc/", "betald", 9000, 4.2)]
        db.executemany("INSERT INTO posts (clip_id, platform, url, status, views, paid_usd, created, updated) "
                       "VALUES (?, ?, ?, ?, ?, ?, '2026-11-10T10:00:00+00:00', '2026-11-10T10:00:00+00:00')", rows)
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, open_id, mode, post_id) "
                   "VALUES (1, 'p1', 'published', 'openA', 'direct', '111')")
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, open_id, mode, post_id) "
                   "VALUES (2, 'p2', 'published', 'openB', 'direct', '555')")  # ett annat inlägg: ingen koppling
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, video_id, status) "
                   "VALUES (1, 'UC1', 'vid1', 'processed')")
        db.execute("PRAGMA user_version = 1")


def _summary(path) -> tuple[int, int, float]:
    with sqlite3.connect(path) as db:
        return db.execute("SELECT COUNT(*), SUM(views), SUM(COALESCE(paid_usd, 0)) FROM posts").fetchone()


def test_the_migration_keeps_every_post_and_fills_the_account_where_it_is_safe(settings) -> None:
    path = settings.data_dir / "klipp.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _old_db(path)
    before = _summary(path)
    log = ClipLog(path)
    assert _summary(path) == before == (4, 12450, 5.7)
    accounts = {(p["clip_id"], p["platform"]): p["account"] for p in log.posts()}
    assert accounts == {(1, "tiktok"): "openA", (1, "youtube_shorts"): "UC1", (2, "tiktok"): None,
                        (3, "instagram_reels"): None}
    with sqlite3.connect(path) as db:  # kontomigreringen (2) är körd; projekten (3) kommer efter den
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 2
        assert "account" in {row[1] for row in db.execute("PRAGMA table_info(posts)")}
    ClipLog(path)  # en gång till ändrar inget
    assert _summary(path) == before
    assert {(p["clip_id"], p["platform"]): p["account"] for p in ClipLog(path).posts()} == accounts


def test_the_migration_works_with_upload_tables_from_before_accounts(settings) -> None:
    # tiktok_uploads från före flera konton saknar open_id, mode och post_id; youtube_uploads saknar video_id.
    # Kolumnerna läggs till först när TikTok-delen öppnar tabellen, efter ClipLog vid start.
    path = settings.data_dir / "klipp.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _old_db(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE tiktok_uploads")
        db.execute("""CREATE TABLE tiktok_uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER,
            publish_id TEXT, status TEXT, error TEXT, created TEXT, checked TEXT)""")
        db.execute("DROP TABLE youtube_uploads")
        db.execute("""CREATE TABLE youtube_uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER,
            channel_id TEXT, status TEXT)""")
    before = _summary(path)
    log = ClipLog(path)
    assert _summary(path) == before
    assert {p["account"] for p in log.posts()} == {None}
    with sqlite3.connect(path) as db:  # kontomigreringen (2) är körd; projekten (3) kommer efter den
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 2
        assert "account" in {row[1] for row in db.execute("PRAGMA table_info(posts)")}


def test_a_post_is_unique_per_clip_platform_and_account(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings)
    log.add_post(clip_id, "tiktok", TT_A, account="openA")
    log.add_post(clip_id, "tiktok", TT_B, account="openB")  # ett annat konto går
    with pytest.raises(sqlite3.IntegrityError):
        log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@a/video/3", account="openA")
    log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@c/video/4")  # för hand, utan konto
    with pytest.raises(sqlite3.IntegrityError):  # två utan konto är fortfarande samma plattform två gånger
        log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@c/video/5")
    assert [p["account"] for p in log.posts(clip_id)] == ["openA", "openB", None]


def test_the_helper_saves_the_account_of_the_upload(settings) -> None:
    set_helper(settings, True)
    clip_id = _clip(settings)
    preferences.add_post(settings, clip_id, "tiktok", TT_A, account="openA")
    preferences.add_post(settings, clip_id, "tiktok", TT_B, account="openB")
    preferences.add_post(settings, clip_id, "tiktok", TT_A, account="openA")  # samma igen: inget fel
    assert [(p["url"], p["account"]) for p in ClipLog(settings.data_dir / "klipp.db").posts(clip_id)] == [
        (TT_A, "openA"), (TT_B, "openB")]


def test_the_youtube_upload_makes_a_post_on_its_channel(settings, monkeypatch) -> None:
    from campaign.youtube import YouTube

    set_helper(settings, True)
    clip_id = _clip(settings)
    monkeypatch.setattr(YouTube, "_upload", lambda self, clip_id, path, channel_id, video, metadata, size: {
        "clip_id": clip_id, "video_id": "vid1", "url": YT, "status": "uploaded", "channel_id": "UC9"})
    video = {"title": "Big", "description": "", "privacy": "public", "made_for_kids": False}
    YouTube(settings).upload(clip_id, settings.out_dir / "v" / "klipp.mp4", None, video, 30.0)
    (post,) = ClipLog(settings.data_dir / "klipp.db").posts(clip_id)
    assert (post["platform"], post["account"]) == ("youtube_shorts", "UC9")


def test_the_post_list_has_the_account_and_null_for_a_hand_logged_post(client, settings) -> None:
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account="openA")
    answer = client.post(f"/campaign/clips/{clip_id}/posts", json={"platform": "youtube_shorts", "url": YT})
    assert answer.status_code == 201, answer.text
    assert [(p["platform"], p["account"]) for p in answer.json()["posts"]] == [
        ("tiktok", "openA"), ("youtube_shorts", None)]


def test_one_account_campaign_still_refuses_a_second_post_on_the_platform_by_hand(client, settings) -> None:
    """Hjälpen har lagt in inlägget på kontot. Spara som postlänk med samma länk går; en annan länk är 409."""
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account="openA")
    same = client.post(f"/campaign/clips/{clip_id}/posts", json={"platform": "tiktok", "url": TT_A})
    assert same.status_code == 200
    other = client.post(f"/campaign/clips/{clip_id}/posts", json={"platform": "tiktok", "url": TT_B})
    assert other.status_code == 409


def test_the_cli_changes_the_hand_logged_link_and_leaves_the_accounts_posts(settings, monkeypatch) -> None:
    monkeypatch.setenv("KLIPP_ROOT", str(settings.root))
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_post(clip_id, "tiktok", TT_A, account="openA")
    assert main(["posted", str(clip_id), "https://www.tiktok.com/@c/video/7"]) == 0
    assert main(["posted", str(clip_id), "https://www.tiktok.com/@c/video/8"]) == 0
    assert [(p["url"], p["account"]) for p in log.posts(clip_id)] == [
        (TT_A, "openA"), ("https://www.tiktok.com/@c/video/8", None)]


# ---------- 2. reglerna i schemat och på knapparna ----------

from datetime import datetime, timedelta  # noqa: E402

from campaign import schedule, schedule_api, tiktok_api, youtube_api  # noqa: E402
from campaign.tiktok import TikTok  # noqa: E402
from campaign.youtube import YouTube  # noqa: E402

from .test_schedule import YT_FIELDS, FakeTikTok, FakeYouTube, _stockholm, notes  # noqa: E402, F401

ONE = "The clip already has a post on this account."
SAME = "The same text is already on another account for this clip. Write a new text or press Suggest new text."


@pytest.fixture
def sched(settings, notes, monkeypatch) -> TestClient:  # noqa: F811
    monkeypatch.setattr(youtube_api, "YouTube", FakeYouTube)
    monkeypatch.setattr(tiktok_api, "TikTok", FakeTikTok)
    app = FastAPI()
    app.state.active_run_id = None
    for router in (api.router, schedule_api.router, youtube_api.router, tiktok_api.router):
        app.include_router(router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _schedule(client, clip_id, account, platform="tiktok", title="Huge clutch", at=None):
    fields = {**YT_FIELDS, "title": title} if platform == "youtube" else {"title": title,
                                                                         "privacy_level": "SELF_ONLY"}
    return client.post(f"/campaign/clips/{clip_id}/schedule", json={
        "platform": platform, "account": account, "at": at or _stockholm(1, 12), "fields": fields})


def _youtube_upload(settings, clip_id, channel, title, status="processed") -> None:
    with YouTube(settings)._db() as db:
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, video_id, status, created, title, description) "
                   "VALUES (?, ?, 'v9', ?, ?, ?, '#ad')", (clip_id, channel, status, datetime.now().isoformat(), title))


def test_a_clip_on_account_a_can_go_to_b_but_not_to_a_again(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account="openA")
    assert _schedule(sched, clip_id, "openB").status_code == 201
    again = _schedule(sched, clip_id, "openA", title="Something else")
    assert (again.status_code, again.json()["detail"]) == (409, ONE)


@pytest.mark.parametrize(("accounts", "account"), [({"tiktok": ["openA"]}, "openA"), ({}, None)])
def test_a_campaign_with_one_or_no_account_allows_one_post_per_platform_as_before(sched, settings, accounts,
                                                                                   account) -> None:
    """Ett inlägg loggat för hand (utan konto) stoppar också, när kampanjen inte har flera konton."""
    set_profile(settings, accounts=accounts)
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account=account)
    answer = _schedule(sched, clip_id, "openA", title="Something else")
    assert (answer.status_code, answer.json()["detail"]) == (400, "The clip already has a post on TikTok.")


def test_without_several_accounts_a_second_scheduled_post_on_the_platform_is_409(sched, settings) -> None:
    clip_id = _clip(settings)
    assert _schedule(sched, clip_id, "UC1", "youtube").status_code == 201
    second = _schedule(sched, clip_id, "UC2", "youtube", title="Other")
    assert second.status_code == 409 and "already scheduled" in second.json()["detail"]


def test_the_sixth_account_is_refused_but_the_fifth_is_fine(sched, settings) -> None:
    set_profile(settings, accounts={"youtube_shorts": [f"UC{i}" for i in range(1, 7)]})
    clip_id = _clip(settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    for i in range(1, 5):
        log.add_post(clip_id, "youtube_shorts", f"https://www.youtube.com/shorts/v{i}", account=f"UC{i}")
    assert _schedule(sched, clip_id, "UC5", "youtube", title="Fifth").status_code == 201
    sixth = _schedule(sched, clip_id, "UC6", "youtube", title="Sixth")
    assert (sixth.status_code, sixth.json()["detail"]) == (
        400, "The clip is already on 5 accounts on YouTube. Content Rewards allows at most 5.")


def test_the_same_text_is_refused_but_a_new_text_is_fine(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    assert _schedule(sched, clip_id, "openA", title="Huge clutch #fyp").status_code == 201
    same = _schedule(sched, clip_id, "openB", title="#gaming huge  CLUTCH ", at=_stockholm(1, 16))
    assert (same.status_code, same.json()["detail"]) == (400, SAME)
    assert _schedule(sched, clip_id, "openB", title="A new angle on it", at=_stockholm(1, 16)).status_code == 201


def test_the_same_clip_on_another_account_close_in_time_gets_a_warning(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id, other = _clip(settings), _clip(settings, "b", title="Other clip")
    first = _schedule(sched, clip_id, "openA").json()
    assert first["warning"] is None
    near = _schedule(sched, clip_id, "openB", title="Second take", at=_stockholm(1, 13)).json()
    assert near["warning"].startswith("The same clip is on openA at 12:00. "), near["warning"]
    assert _schedule(sched, clip_id, "openB", title="x", at=_stockholm(1, 15)).status_code == 409  # samma konto
    listed = {post["id"]: post["warning"] for post in sched.get("/campaign/schedule").json()}
    assert listed[first["id"]].startswith("The same clip is on openB at 13:00. ")
    # Finns båda varningarna visas den om samma konto.
    _schedule(sched, other, "openA", title="Other clip", at=_stockholm(1, 12, 30))
    listed = {post["id"]: post["warning"] for post in sched.get("/campaign/schedule").json()}
    assert listed[first["id"]].startswith("The account has another post at 12:30.")


def test_the_youtube_button_follows_the_rules_per_channel(sched, settings) -> None:
    set_profile(settings, accounts={"youtube_shorts": ["UC1", "UC2"]})
    clip_id = _clip(settings)
    _youtube_upload(settings, clip_id, "UC1", "Big play")

    def send(channel, title):
        return sched.post(f"/campaign/clips/{clip_id}/youtube", json={**YT_FIELDS, "title": title,
                                                                      "channel_id": channel})

    assert (send("UC1", "Fresh").status_code, send("UC1", "Fresh").json()["detail"]) == (409, ONE)
    same = send("UC2", "big   play")
    assert (same.status_code, same.json()["detail"]) == (400, SAME)
    assert send("UC2", "Fresh").status_code == 202
    assert [call[1] for call in FakeYouTube.calls] == ["UC2"]


def test_the_tiktok_button_follows_the_rules_per_account(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account="openA")

    def send(account):
        return sched.post(f"/campaign/clips/{clip_id}/tiktok", json={
            "mode": "direct", "open_id": account, "title": "Clean shot", "privacy_level": "SELF_ONLY"})

    assert (send("openA").status_code, send("openA").json()["detail"]) == (409, ONE)
    assert send("openB").status_code == 202
    assert [call[1] for call in FakeTikTok.calls] == ["openB"]


def test_at_the_time_the_rules_are_checked_again(sched, settings, notes) -> None:  # noqa: F811
    set_profile(settings, accounts={"youtube_shorts": ["UC1", "UC2"]})
    clip_id = _clip(settings)
    post = _schedule(sched, clip_id, "UC2", "youtube", title="Big play").json()
    _youtube_upload(settings, clip_id, "UC1", "big PLAY", status="uploaded")  # med knappen under tiden
    schedule.Schedule(settings).run_due(now=datetime.fromisoformat(post["at"]) + timedelta(seconds=10))
    assert FakeYouTube.calls == []
    assert schedule.Schedule(settings).get(post["id"])["status"] == "failed"
    assert schedule.Schedule(settings).get(post["id"])["error"] == SAME
    assert notes and "was not uploaded" in notes[-1][0]


def test_a_direct_tiktok_upload_counts_as_a_post_on_its_account(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    with TikTok(settings)._db() as db:
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, created, open_id, mode, title) "
                   "VALUES (?, 'p1', 'processing', ?, 'openA', 'direct', 'Huge clutch')",
                   (clip_id, datetime.now().isoformat()))
    assert _schedule(sched, clip_id, "openB", title="huge clutch").json()["detail"] == SAME


# ---------- 3. GET /clips/{id}/accounts ----------


def _token(settings, open_id, **names) -> None:
    TikTok(settings).save_token({"access_token": "a", "refresh_token": "r", "open_id": open_id,
                                 "refresh_expires_at": "2099-01-01T00:00:00+00:00", **names})


def test_the_accounts_of_a_clip_show_where_it_is_posted_scheduled_or_free(sched, settings) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "@ClipVault", "openC"]})
    _token(settings, "openA", display_name="Alpha")
    _token(settings, "o2", username="clipvault", display_name="Clip Vault")
    clip_id = _clip(settings)
    ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, "tiktok", TT_A, account="openA")
    planned = _schedule(sched, clip_id, "o2", title="Fresh take").json()
    answer = sched.get(f"/campaign/clips/{clip_id}/accounts", params={"platform": "tiktok"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["multi"], body["max_accounts"]) == (True, 5)
    posted, scheduled, free = body["accounts"]
    assert posted["at"] and {**posted, "at": None} == {"account": "openA", "name": "Alpha", "state": "posted",
                                                       "at": None, "text": None}
    assert scheduled == {"account": "o2", "name": "clipvault", "state": "scheduled", "at": planned["at"],
                         "text": "Fresh take"}
    assert free == {"account": "openC", "name": None, "state": "free", "at": None, "text": None}


def test_a_campaign_without_accounts_is_not_multi(sched, settings) -> None:
    clip_id = _clip(settings)
    assert sched.get(f"/campaign/clips/{clip_id}/accounts", params={"platform": "youtube"}).json() == {
        "multi": False, "max_accounts": 5, "accounts": []}
    assert sched.get("/campaign/clips/999/accounts", params={"platform": "youtube"}).status_code == 404
    assert sched.get(f"/campaign/clips/{clip_id}/accounts", params={"platform": "myspace"}).status_code == 422


# ---------- 4. cover_s ----------


def test_a_scheduled_tiktok_post_keeps_its_cover_and_a_cover_outside_the_clip_is_400(sched, settings) -> None:
    clip_id = _clip(settings)  # 30 s

    def plan(cover_s):
        return sched.post(f"/campaign/clips/{clip_id}/schedule", json={
            "platform": "tiktok", "account": "openA", "at": _stockholm(1, 12),
            "fields": {"title": "x", "privacy_level": "SELF_ONLY", "cover_s": cover_s}})

    refused = plan(31.0)
    assert refused.status_code == 400 and "The cover" in refused.json()["detail"]
    assert plan(4.0).json()["fields"]["cover_s"] == 4.0


@needs_ffmpeg
def test_the_cover_is_checked_against_the_finished_file_not_the_logged_times(sched, settings) -> None:
    # Loggen säger 30 s (10-40), men tidskartan gjorde den färdiga filen 5 s lång.
    clip_id = _clip(settings)
    path = settings.out_dir / "v" / "klipp.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x112:d=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    answer = sched.post(f"/campaign/clips/{clip_id}/schedule", json={
        "platform": "tiktok", "account": "openA", "at": _stockholm(1, 12),
        "fields": {"title": "x", "privacy_level": "SELF_ONLY", "cover_s": 20.0}})
    assert answer.status_code == 400 and "The cover" in answer.json()["detail"]


# ---------- 5. POST /clips/{id}/variant ----------

from campaign.brain import Brain  # noqa: E402
from campaign.budget import Budget, BudgetExceeded  # noqa: E402
from campaign.caption import check_caption  # noqa: E402
from campaign.profile import load_profile  # noqa: E402

from .conftest import FakeClient  # noqa: E402

CAPTION_RULES = {"tag_line": "#ad #partner", "must_include": ["@warcraft"], "hashtags": ["#wow"],
                 "max_extra_hashtags": 1}


def _claude(monkeypatch, *titles) -> FakeClient:
    from campaign import variant

    fake = FakeClient({"title": titles[0]})
    answers = iter(titles)
    real = fake.messages.create

    def create(**kwargs):
        fake.messages.answer = {"title": next(answers, titles[-1])}
        return real(**kwargs)

    fake.messages.create = create
    monkeypatch.setattr(variant, "Brain", lambda settings, budget: Brain(settings, budget, client=fake))
    return fake


def _variant(client, clip_id, platform="tiktok", account="openB"):
    return client.post(f"/campaign/clips/{clip_id}/variant", json={"platform": platform, "account": account})


def test_a_variant_differs_from_the_used_texts_and_follows_the_tag_line(sched, settings, monkeypatch) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]}, caption=CAPTION_RULES)
    clip_id = _clip(settings)
    assert _schedule(sched, clip_id, "openA", title="Huge clutch").status_code == 201
    fake = _claude(monkeypatch, "Huge  CLUTCH", "Nobody saw that last shot coming #fyp")
    answer = _variant(sched, clip_id)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["title"] == "Nobody saw that last shot coming"  # hashtaggen tas bort; kampanjen sätter dem
    first, second = body["caption"].split("\n")
    assert first.startswith(body["title"]) and "@warcraft" in first
    assert second.startswith("#ad #partner") and "#wow" in second
    assert check_caption(body["caption"], load_profile("skiimasktimmy", settings.profiles_dir)) == []
    assert body["description"] is None
    assert len(fake.messages.calls) == 2  # den första var samma text som på openA
    assert "Huge clutch" in str(fake.messages.calls[0]["messages"])
    budget = Budget(settings.data_dir / "budget.db", settings.prices_path, 200.0, 10.0)
    assert budget.summary()["month_usd"] > 0  # budgeten gäller


def test_a_youtube_variant_has_a_description(sched, settings, monkeypatch) -> None:
    set_profile(settings, accounts={"youtube_shorts": ["UC1", "UC2"]})
    _claude(monkeypatch, "Timmy reads the whole lobby")
    body = _variant(sched, _clip(settings), "youtube", "UC2").json()
    assert body["title"] == "Timmy reads the whole lobby"
    assert body["description"] == body["caption"] and body["caption"].startswith(body["title"])


def test_a_variant_that_is_still_taken_is_400_after_two_tries(sched, settings, monkeypatch) -> None:
    set_profile(settings, accounts={"tiktok": ["openA", "openB"]})
    clip_id = _clip(settings)
    _schedule(sched, clip_id, "openA", title="Huge clutch")
    fake = _claude(monkeypatch, "huge clutch")
    answer = _variant(sched, clip_id)
    assert answer.status_code == 400 and "Try again" in answer.json()["detail"]
    assert len(fake.messages.calls) == 2


def test_a_variant_without_a_key_or_budget_is_400_with_the_reason(sched, settings, monkeypatch) -> None:
    clip_id = _clip(settings)
    settings.anthropic_api_key = ""
    answer = _variant(sched, clip_id)
    assert answer.status_code == 400 and "key" in answer.json()["detail"].lower()
    settings.anthropic_api_key = "nyckel"
    _claude(monkeypatch, "Anything")
    monkeypatch.setattr(Budget, "check", lambda self: (_ for _ in ()).throw(BudgetExceeded("The monthly cap is reached: 200.00 of 200.00 dollars.")))
    answer = _variant(sched, clip_id)
    assert answer.status_code == 400 and "The monthly cap is reached" in answer.json()["detail"]
    assert sched.post("/campaign/clips/999/variant", json={"platform": "tiktok", "account": "a"}).status_code == 404
