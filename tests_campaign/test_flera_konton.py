"""Samma klipp på flera konton på samma plattform (funktion 7). Låtsas-YouTube, låtsas-TikTok och låtsas-Claude:
inga riktiga anrop."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, preferences
from campaign.cli import main
from campaign.cliplog import ClipLog

from .conftest import set_helper, set_profile

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
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    ClipLog(path)  # en gång till ändrar inget
    assert _summary(path) == before
    assert {(p["clip_id"], p["platform"]): p["account"] for p in ClipLog(path).posts()} == accounts


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
