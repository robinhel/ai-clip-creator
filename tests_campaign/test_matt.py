"""Fler mått i Statistik: Robin skriver in tittartid, andel som ser klart, andel från För dig, sparningar och
andel från kampanjens länder per inlägg, från TikTok Studio. Appen räknar själv ut klippets längd och om
inlägget nådde kampanjens gräns inom 7 dagar."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from mcp import Client

from campaign import cli, mcp_server, schedule
from campaign.cliplog import ClipLog

from .conftest import make_clip, needs_ffmpeg
from .test_api import _clip, client  # noqa: F401  (fixture)

TIKTOK = "https://www.tiktok.com/@me/video/1"
TIKTOK_2 = "https://www.tiktok.com/@me/video/2"
SHORTS = "https://youtube.com/shorts/xyz"
METRICS = {
    "avg_watch_s": 6.4,
    "full_pct": 31.5,
    "fyp_pct": 88.0,
    "saves": 12,
    "countries_pct": 74.2,
}
CREATED = "2026-11-01T12:00:00+00:00"


def _day(day: int, hour: int = 12) -> str:
    """Tiden `day` dagar efter CREATED, som _now() skriver den."""
    return f"2026-11-{1 + day:02d}T{hour:02d}:00:00+00:00"


def _posted(client, settings) -> tuple[int, int]:  # noqa: F811
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    clip = client.post(
        f"/campaign/clips/{clip_id}/posts", json={"platform": "tiktok", "url": TIKTOK}
    ).json()
    return clip_id, clip["posts"][0]["id"]


# ---------- en gammal klipplogg ----------


OLD_TABLES = [
    """CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id TEXT, profile TEXT,
    start_s REAL, end_s REAL, title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT,
    status TEXT, problems TEXT, warnings TEXT, review TEXT, platform TEXT, url TEXT,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, note TEXT, created TEXT, updated TEXT,
    score INTEGER, reason TEXT, subscores TEXT, version_of INTEGER)""",
    """CREATE TABLE runs (run_id INTEGER PRIMARY KEY, vod_id TEXT, profile TEXT, game TEXT,
    focus TEXT, min_s INTEGER, max_s INTEGER, title TEXT, channel_name TEXT, thumbnail_url TEXT,
    duration_s INTEGER, created TEXT, start_s REAL, end_s REAL)""",
    """CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT,
    account TEXT, url TEXT, status TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    paid_usd REAL, created TEXT, updated TEXT, prev_status TEXT)""",
    "CREATE UNIQUE INDEX posts_account ON posts (clip_id, platform, COALESCE(account, ''))",
]
# Egna videor (user_version 3): projekten och körningarnas kolumner.
PROJECT_TABLES = [
    "ALTER TABLE runs ADD COLUMN project_id INTEGER",
    "ALTER TABLE runs ADD COLUMN origin TEXT",
    """CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL,
    kind TEXT NOT NULL, title TEXT, created TEXT)""",
]
OLD_CLIPS = [  # vod_id, profile, start_s, end_s, title, status, views, likes
    ("2925991945", "skiimasktimmy", 10.0, 40.0, "No way", "inskickad", 3100, 45),
    ("2925991945", "skiimasktimmy", 50.0, 70.0, "Clutch", "postad", 900, 8),
]
OLD_POSTS = [  # clip_id, platform, account, url, status, views, likes, comments, shares, paid_usd, prev_status
    (1, "tiktok", None, TIKTOK, "inskickad", 2500, 40, 3, 1, None, "postad"),
    (1, "youtube_shorts", None, SHORTS, "postad", 600, 5, 0, 0, None, None),
    (2, "tiktok", "open-1", TIKTOK_2, "postad", 900, 8, 1, 0, 1.5, None),
]


def _old_cliplog(path, version: int) -> None:
    """En klipplogg som den såg ut med user_version 2 (före projekten) eller 3 (med projekten), med siffror."""
    db = sqlite3.connect(path)
    for sql in OLD_TABLES + (PROJECT_TABLES if version >= 3 else []):
        db.execute(sql)
    db.execute(
        "INSERT INTO runs (run_id, vod_id, profile) VALUES (11, '2925991945', 'skiimasktimmy')"
    )
    db.executemany(
        "INSERT INTO clips (vod_id, profile, start_s, end_s, title, status, views, likes, created, updated) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(*row, CREATED, CREATED) for row in OLD_CLIPS],
    )
    db.executemany(
        "INSERT INTO posts (clip_id, platform, account, url, status, views, likes, comments, shares, paid_usd, "
        "prev_status, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(*row, CREATED, CREATED) for row in OLD_POSTS],
    )
    db.execute(f"PRAGMA user_version = {version}")
    db.commit()
    db.close()


def _columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def test_an_old_cliplog_gets_the_new_columns_and_keeps_every_row(tmp_path) -> None:
    for version in (2, 3):
        path = tmp_path / f"klipp{version}.db"
        _old_cliplog(path, version)
        with sqlite3.connect(path) as db:
            old = {
                table: (
                    _columns(db, table),
                    db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall(),
                )  # noqa: S608
                for table in ("clips", "posts")
            }

        ClipLog(path)

        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 3
            for table, (columns, rows) in old.items():
                assert (
                    db.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1").fetchall()
                    == rows
                )  # noqa: S608
            assert (
                db.execute(
                    "SELECT avg_watch_s, full_pct, fyp_pct, saves, countries_pct FROM posts"
                ).fetchall()
                == [(None,) * 5] * 3
            )
            assert db.execute("SELECT length_s FROM clips").fetchall() == [(None,), (None,)]
            assert db.execute("SELECT * FROM post_views").fetchall() == []
        log = ClipLog(path)  # en gång till: inget ändras
        assert [p["views"] for p in log.posts()] == [2500, 600, 900]
        assert [p["account"] for p in log.posts()] == [None, None, "open-1"]
        assert (
            log.get(1)["views"] == 3100
        )  # klippets gamla siffror räknas inte om när loggen öppnas


# ---------- PATCH med måtten ----------


def test_the_metrics_are_saved_per_post_and_checked(client, settings) -> None:  # noqa: F811
    clip_id, post_id = _posted(client, settings)
    response = client.patch(f"/campaign/posts/{post_id}", json=METRICS)
    assert response.status_code == 200, response.text
    post = response.json()["posts"][0]
    assert {key: post[key] for key in METRICS} == METRICS
    assert (
        client.patch(f"/campaign/posts/{post_id}", json={"avg_watch_s": 12.5}).json()["posts"][0][
            "avg_watch_s"
        ]
        == 12.5
    )
    assert ClipLog(settings.data_dir / "klipp.db").get_post(post_id)["avg_watch_s"] == 12.5

    for wrong in (
        {"full_pct": 100.5},
        {"saves": -1},
        {"avg_watch_s": -0.1},
        {"fyp_pct": -1},
        {"countries_pct": 101},
        {"saves": 2.5},
    ):
        assert client.patch(f"/campaign/posts/{post_id}", json=wrong).status_code == 422, wrong
    post = client.get("/campaign/clips").json()[0]["posts"][0]
    assert {key: post[key] for key in METRICS} == {**METRICS, "avg_watch_s": 12.5}


def test_infinity_and_nan_are_refused(client, settings) -> None:  # noqa: F811
    clip_id, post_id = _posted(client, settings)
    for body in ('{"avg_watch_s": Infinity}', '{"avg_watch_s": NaN}', '{"full_pct": NaN}'):
        response = client.patch(
            f"/campaign/posts/{post_id}",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, body
    post = ClipLog(settings.data_dir / "klipp.db").get_post(post_id)
    assert post["avg_watch_s"] is None and post["full_pct"] is None


def test_nan_as_link_is_refused_as_a_wrong_type(client, settings) -> None:  # noqa: F811
    clip_id, post_id = _posted(client, settings)
    response = client.patch(
        f"/campaign/posts/{post_id}",
        content='{"url": NaN}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert ClipLog(settings.data_dir / "klipp.db").get_post(post_id)["url"] == TIKTOK


def test_the_metrics_are_not_added_up_on_the_clip(client, settings) -> None:  # noqa: F811
    clip_id, tiktok = _posted(client, settings)
    clip = client.post(
        f"/campaign/clips/{clip_id}/posts",
        json={"platform": "youtube_shorts", "url": SHORTS},
    ).json()
    shorts = next(p["id"] for p in clip["posts"] if p["platform"] == "youtube_shorts")
    client.patch(f"/campaign/posts/{tiktok}", json={"views": 1000, **METRICS})
    clip = client.patch(
        f"/campaign/posts/{shorts}", json={"views": 1500, "saves": 20, "full_pct": 50}
    ).json()
    assert clip["views"] == 2500
    assert not {"saves", "full_pct", "avg_watch_s", "fyp_pct", "countries_pct"} & clip.keys()
    assert [p["saves"] for p in clip["posts"]] == [12, 20]


# ---------- visningarnas historik ----------


def test_each_saved_view_count_is_kept_with_its_time(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    post_id = log.add_post(clip_id, "tiktok", TIKTOK)
    assert log.views_history(post_id) == []
    log.update_post(post_id, views=800)
    log.update_post(post_id, views=2100, likes=30)
    log.update_post(post_id, saves=5)  # bara sparningar: ingen ny rad
    log.update_post(post_id, views=None)  # tomt fält: ingen ny rad
    history = log.views_history(post_id)
    assert [views for views, _ in history] == [800, 2100]
    assert all(at.endswith("+00:00") for _, at in history)


def test_deleting_a_post_removes_only_its_history(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    gone = log.add_post(clip_id, "tiktok", TIKTOK)
    kept = log.add_post(clip_id, "youtube_shorts", SHORTS)
    log.update_post(gone, views=100)
    log.update_post(kept, views=200)
    log.update_post(kept, views=300)
    log.delete_post(gone)
    assert log.views_history(gone) == []
    assert [views for views, _ in log.views_history(kept)] == [200, 300]


# ---------- nådde gränsen inom 7 dagar ----------


def test_reached_min_within_7_days() -> None:
    from campaign.metrics import reached_min_7d

    assert reached_min_7d(CREATED, [(800, _day(2)), (2100, _day(6))], 2000) is True
    assert reached_min_7d(CREATED, [(1900, _day(8))], 2000) is False
    assert reached_min_7d(CREATED, [(2500, _day(9))], 2000) is None  # första värdet kom för sent
    assert reached_min_7d(CREATED, [], 2000) is None
    assert reached_min_7d(CREATED, [(1200, _day(3))], 2000) is None  # inte än
    # Exakt 7 dagar räknas på båda sidor.
    assert reached_min_7d(CREATED, [(2000, _day(7))], 2000) is True
    assert reached_min_7d(CREATED, [(1999, _day(7))], 2000) is False
    assert reached_min_7d(CREATED, [(2000, _day(7, 13))], 2000) is None
    assert reached_min_7d(CREATED, [(1999, _day(7, 11))], 2000) is None
    # Ja vinner när båda gäller.
    assert reached_min_7d(CREATED, [(2100, _day(5)), (1900, _day(9))], 2000) is True
    # Kampanjens gräns avgör.
    assert reached_min_7d(CREATED, [(1000, _day(8))], 2000) is False
    assert reached_min_7d(CREATED, [(1000, _day(8))], 250) is None
    assert reached_min_7d(CREATED, [(1000, _day(6))], 2000) is None
    assert reached_min_7d(CREATED, [(1000, _day(6))], 250) is True
    assert reached_min_7d(CREATED, [(1000, _day(6))], 0) is None


def test_the_post_says_if_it_reached_the_campaign_min_in_7_days(client, settings) -> None:  # noqa: F811
    clip_id, post_id = _posted(client, settings)
    clip = client.get("/campaign/clips").json()[0]
    assert clip["posts"][0]["reached_min_7d"] is None
    assert clip["audience_countries"] == "Tier 1-länder"
    clip = client.patch(f"/campaign/posts/{post_id}", json={"views": 2500}).json()
    assert clip["posts"][0]["reached_min_7d"] is True


def test_without_a_campaign_profile_there_is_no_answer(client, settings) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(
        vod_id="123",
        profile="borttagen",
        file="",
        title="x",
        caption="x",
        status="godkand",
        start_s=0.0,
        end_s=20.0,
    )
    post_id = log.add_post(clip_id, "tiktok", TIKTOK)
    log.update_post(post_id, views=5000)
    clip = next(c for c in client.get("/campaign/clips").json() if c["id"] == clip_id)
    assert clip["audience_countries"] is None
    assert clip["posts"][0]["reached_min_7d"] is None


# ---------- klippets längd ----------


@needs_ffmpeg
def test_the_clip_length_comes_from_the_file_and_is_kept(client, settings) -> None:  # noqa: F811
    path = make_clip(settings.out_dir / "123" / "klara" / "clip_01_a.mp4", seconds=3)
    clip_id = _clip(settings, status="godkand", file=str(path))  # loggens tider säger 30 s
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_post(clip_id, "tiktok", TIKTOK)
    clip = client.get("/campaign/clips").json()[0]
    assert abs(clip["length_s"] - 3.0) < 0.2
    path.unlink()  # sparad: filen läses inte igen
    assert client.get("/campaign/clips").json()[0]["length_s"] == clip["length_s"]

    log.update(
        clip_id, file=str(settings.out_dir / "123" / "klara" / "clip_01_b.mp4")
    )  # ny fil: räknas om
    assert client.get("/campaign/clips").json()[0]["length_s"] == 30.0


def test_the_clip_length_falls_back_to_the_log_times(client, settings) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    with_times = _clip(settings, status="godkand")  # ingen fil, 10-40 s i loggen
    without = log.add(vod_id="upload", profile="skiimasktimmy", file="", title="x", caption="x")
    log.add_post(with_times, "tiktok", TIKTOK)
    log.add_post(without, "tiktok", TIKTOK_2)
    clips = {c["id"]: c for c in client.get("/campaign/clips").json()}
    assert clips[with_times]["length_s"] == 30.0
    assert clips[without]["length_s"] is None
    assert log.get(with_times)["length_s"] == 30.0
    assert log.get(without)["length_s"] is None  # inget sparat: nästa gång prövas filen igen


def _fake_probe(monkeypatch, duration: float = 3.0) -> list[Path]:
    """ffprobe utan ffmpeg: varje fil som läses hamnar i listan."""
    calls: list[Path] = []

    def probe(path: Path) -> dict:
        calls.append(path)
        return {"duration": duration}

    monkeypatch.setattr(schedule, "probe", probe)
    return calls


def test_ffprobe_runs_only_for_posted_clips_with_a_file_and_once(
    client,  # noqa: F811
    settings,
    monkeypatch,
) -> None:
    calls = _fake_probe(monkeypatch)
    log = ClipLog(settings.data_dir / "klipp.db")
    unposted = _clip(settings)  # fil, men inget inlägg
    posted, _ = _posted(client, settings)
    no_file = log.add(
        vod_id="123",
        profile="skiimasktimmy",
        file="",
        title="x",
        caption="x",
        status="godkand",
        start_s=0.0,
        end_s=20.0,
    )
    log.add_post(no_file, "tiktok", TIKTOK_2)
    clips = {c["id"]: c for c in client.get("/campaign/clips").json()}
    assert calls == [Path(log.get(posted)["file"])]
    assert clips[posted]["length_s"] == 3.0
    assert clips[unposted]["length_s"] is None
    assert clips[no_file]["length_s"] == 20.0  # ingen fil: loggens tider utan ffprobe
    client.get("/campaign/clips")
    assert len(calls) == 1  # sparad: ffprobe körs inte vid nästa laddning


def test_without_ffprobe_the_length_comes_from_the_times_and_is_not_saved(
    client,  # noqa: F811
    settings,
    monkeypatch,
) -> None:
    def no_ffprobe(path: Path) -> dict:
        raise SystemExit("ffprobe hittades inte. Installera ffmpeg och försök igen.")

    monkeypatch.setattr(schedule, "probe", no_ffprobe)
    clip_id, _ = _posted(client, settings)
    assert client.get("/campaign/clips").json()[0]["length_s"] == 30.0
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["length_s"] is None


def test_new_times_reset_the_length(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings)
    log.set_length(clip_id, 30.0, log.get(clip_id)["updated"])
    assert log.get(clip_id)["length_s"] == 30.0
    log.update(clip_id, start_s=12.0)
    assert log.get(clip_id)["length_s"] is None


def test_a_length_is_saved_only_once_and_only_for_the_unchanged_clip(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings)
    log.set_length(clip_id, 30.0, "2026-01-01T00:00:00+00:00")  # klippet har ändrats sedan dess
    assert log.get(clip_id)["length_s"] is None
    updated = log.get(clip_id)["updated"]
    log.set_length(clip_id, 28.0, updated)
    log.set_length(clip_id, 99.0, updated)  # redan sparad: skrivs inte över
    assert log.get(clip_id)["length_s"] == 28.0
    assert log.get(clip_id)["updated"] == updated


# ---------- att göra ----------


def test_todo_at_analytics_asks_for_the_extra_metrics(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    post_id = log.add_post(clip_id, "tiktok", TIKTOK)
    log.update_post(post_id, status="inskickad", views=2500)
    [item] = log.todo(2000)
    assert "analytics" in item and "More metrics" in item and "Stats" in item


# ---------- terminalen och MCP ----------


def test_stats_in_the_terminal_saves_the_metrics(settings, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    post_id = log.add_post(clip_id, "tiktok", TIKTOK)
    assert (
        cli.main(
            [
                "stats",
                str(clip_id),
                "--views",
                "2500",
                "--watch-s",
                "6,4",
                "--full-pct",
                "31.5",
                "--fyp-pct",
                "88,0",
                "--countries-pct",
                "74,2",
                "--saves",
                "12",
            ]
        )
        == 0
    )
    post = log.get_post(post_id)
    assert {key: post[key] for key in METRICS} == METRICS
    assert post["views"] == 2500
    assert log.get(clip_id)["views"] == 2500


@pytest.mark.parametrize(
    "wrong", [["--saves", "-5"], ["--watch-s", "nan"], ["--watch-s", "inf"], ["--full-pct", "nan"]]
)
def test_stats_in_the_terminal_refuses_wrong_numbers(settings, monkeypatch, capsys, wrong) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    post_id = log.add_post(clip_id, "tiktok", TIKTOK)
    with pytest.raises(SystemExit) as refused:
        cli.main(["stats", str(clip_id), "--views", "2500", *wrong])
    assert refused.value.code == 2
    assert wrong[0] in capsys.readouterr().err
    post = log.get_post(post_id)
    assert post["views"] is None and post["saves"] is None
    assert post["avg_watch_s"] is None and post["full_pct"] is None


def test_save_stats_reaches_the_app(client, settings, monkeypatch) -> None:  # noqa: F811
    clip_id, post_id = _posted(client, settings)
    monkeypatch.setattr(mcp_server, "http_client", lambda: client)
    monkeypatch.setattr(mcp_server, "api_key", lambda: None)

    async def go():
        async with Client(mcp_server.server) as mcp:
            return await mcp.call_tool("save_stats", {"post_id": post_id, **METRICS})

    result = asyncio.run(go())
    assert not result.is_error, result.content
    post = ClipLog(settings.data_dir / "klipp.db").get_post(post_id)
    assert {key: post[key] for key in METRICS} == METRICS
