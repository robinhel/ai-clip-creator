"""Ett projekt per kampanj för egna filer: filerna från python -m campaign local och uppladdningarna på
Egna videor. Varje fil behåller sin körning. Inga riktiga anrop till Claude eller Twitch."""

from __future__ import annotations

import sqlite3

from campaign.cliplog import ClipLog

from .test_api import client  # noqa: F401  (fixture)
from .test_natt_lage import _run

CAMPAIGN = "skiimasktimmy"
TWITCH = "2925991945"
FILE_A, FILE_B = "Skyborne_Test_A", "Skyborne_Test_B"


def _log(settings) -> ClipLog:
    return ClipLog(settings.data_dir / "klipp.db")


def _file(settings, vod_id: str, status: str = "done", **extra) -> int:
    """En körning som python -m campaign local skapar den."""
    run_id = _run(status, vod_id)
    _log(settings).add_run(run_id, vod_id, CAMPAIGN, "Bodycam", title=vod_id.replace("_", " "),
                           channel_name="SkiiMaskTimmy", thumbnail_url=None, duration_s=20, **extra)
    return run_id


def _clip(settings, vod_id: str, profile: str = CAMPAIGN) -> int:
    return _log(settings).add(vod_id=vod_id, profile=profile, file=str(settings.out_dir / vod_id / "a.mp4"),
                              title=f"Klipp {vod_id}", caption="c", start_s=1.0, end_s=15.0)


def _upload(settings, profile: str = CAMPAIGN) -> int:
    """En uppladdning, som drafts.store sparar den: projektet först, sedan klippet."""
    _log(settings).project(profile, "SkiiMaskTimmy")
    return _clip(settings, "upload", profile)


def _project(client, profile: str = CAMPAIGN) -> dict:  # noqa: F811
    [project] = [p for p in client.get("/campaign/projects").json() if p["profile"] == profile]
    return project


def test_two_own_files_and_an_upload_are_one_project(client, settings) -> None:  # noqa: F811
    a, b = _file(settings, FILE_A), _file(settings, FILE_B)
    mine = [_clip(settings, FILE_A), _clip(settings, FILE_B), _clip(settings, FILE_B), _clip(settings, "upload")]
    _clip(settings, "upload", profile="annan")  # en annan kampanjs uppladdning
    _clip(settings, FILE_A, profile="annan")  # samma filnamn i en annan kampanj
    _clip(settings, TWITCH)  # ett Twitch-klipp i samma kampanj hör inte till projektet

    project = _project(client)
    assert (project["title"], project["streamer"]) == ("Egna filer för SkiiMaskTimmy", "SkiiMaskTimmy")
    assert [(f["run_id"], f["vod_id"], f["status"]) for f in project["files"]] == [(a, FILE_A, "done"),
                                                                                    (b, FILE_B, "done")]
    assert project["files"][0]["title"] == "Skyborne Test A" and project["files"][0]["duration_s"] == 20
    assert project["clip_counts"] == {"renderad": 4}
    assert (project["active"], project["pct"]) == (False, None)
    assert project["poster_url"].startswith(f"/campaign/clips/{mine[0]}/poster")
    assert client.get(f"/campaign/projects/{project['id']}").json() == project

    clips = client.get("/campaign/clips", params={"project_id": project["id"]}).json()
    assert sorted(c["id"] for c in clips) == mine
    assert client.get("/campaign/clips", params={"project_id": 999}).json() == []


def test_a_campaign_with_only_uploads_gets_a_project_without_files(client, settings) -> None:  # noqa: F811
    upload = _upload(settings)
    project = _project(client)
    assert (project["files"], project["clip_counts"], project["title"]) == ([], {"renderad": 1},
                                                                            "Egna filer för SkiiMaskTimmy")
    assert _project(client)["id"] == project["id"]  # samma projekt nästa gång
    assert [c["id"] for c in client.get("/campaign/clips", params={"project_id": project["id"]}).json()] == [upload]


def test_reading_the_projects_writes_nothing(client, settings) -> None:  # noqa: F811
    _clip(settings, "upload")  # en uppladdning utan projekt
    assert client.get("/campaign/projects").json() == []
    assert _log(settings).projects() == []


def test_an_unknown_project_is_404_in_swedish(client) -> None:  # noqa: F811
    response = client.get("/campaign/projects/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Projektet finns inte."


def test_runs_still_list_each_own_file_so_the_links_from_a_clip_work(client, settings) -> None:  # noqa: F811
    a, b = _file(settings, FILE_A), _file(settings, FILE_B)
    twitch = _file(settings, TWITCH)
    clips = [_clip(settings, FILE_A), _clip(settings, FILE_B), _clip(settings, TWITCH)]
    project_id = _project(client)["id"]

    runs = client.get("/campaign/runs").json()
    expected = {a: project_id, b: project_id, twitch: None}
    assert {r["run_id"]: r["project_id"] for r in runs} == expected
    assert {r["origin"] for r in runs} == {None}
    # Samma uppslag som ClipLogClient (useRunLinks) och QueueClient: vod_id och kampanj, största run_id.
    for clip_id, run_id in zip(clips, (a, b, twitch), strict=True):
        clip = next(c for c in client.get("/campaign/clips").json() if c["id"] == clip_id)
        found = max(r["run_id"] for r in runs if (r["vod_id"], r["profile"]) == (clip["vod_id"], clip["profile"]))
        assert found == run_id
        assert client.get(f"/campaign/runs/{found}").json()["project_id"] == expected[run_id]


def test_twitch_runs_get_no_project(client, settings) -> None:  # noqa: F811
    run_id = _file(settings, TWITCH)
    assert _log(settings).run(run_id)["project_id"] is None
    assert client.get(f"/campaign/runs/{run_id}").json()["project_id"] is None
    assert client.get("/campaign/projects").json() == []


def test_a_project_shows_the_file_that_runs_now(client, settings) -> None:  # noqa: F811
    _file(settings, FILE_A)
    running = _file(settings, FILE_B, status="running")
    project = _project(client)
    assert project["active"] is True
    assert [f["status"] for f in project["files"]] == ["done", "running"]
    assert project["pct"] == project["files"][1]["pct"]
    assert project["files"][1]["run_id"] == running


def test_a_new_run_of_the_same_file_replaces_the_file_but_a_chat_run_does_not(client, settings) -> None:  # noqa: F811
    first = _file(settings, FILE_A, status="failed")
    again = _file(settings, FILE_A)
    chat = _file(settings, FILE_A, origin="chat")
    assert [f["run_id"] for f in _project(client)["files"]] == [again]
    [card] = client.get("/campaign/runs").json()
    assert card["run_id"] == again
    assert {first, again, chat} == {r["run_id"] for r in _log(settings).runs()}


def test_a_file_with_only_chat_runs_shows_the_latest_one(client, settings) -> None:  # noqa: F811
    _file(settings, FILE_A, origin="chat")
    last = _file(settings, FILE_A, origin="chat")
    assert [r["run_id"] for r in client.get("/campaign/runs").json()] == [last]
    assert [f["run_id"] for f in _project(client)["files"]] == [last]


# ---------- migreringen ----------


def _old_cliplog(path) -> None:
    """En klipplogg som den såg ut med user_version 2: inga projekt."""
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id TEXT, profile TEXT,
        start_s REAL, end_s REAL, title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT,
        status TEXT, problems TEXT, warnings TEXT, review TEXT, platform TEXT, url TEXT,
        views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, note TEXT, created TEXT, updated TEXT,
        score INTEGER, reason TEXT, subscores TEXT, version_of INTEGER)""")
    db.execute("""CREATE TABLE runs (run_id INTEGER PRIMARY KEY, vod_id TEXT, profile TEXT, game TEXT,
        focus TEXT, min_s INTEGER, max_s INTEGER, title TEXT, channel_name TEXT, thumbnail_url TEXT,
        duration_s INTEGER, created TEXT, start_s REAL, end_s REAL)""")
    db.execute("""CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT,
        account TEXT, url TEXT, status TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
        paid_usd REAL, created TEXT, updated TEXT, prev_status TEXT)""")
    db.executemany("INSERT INTO runs (run_id, vod_id, profile, channel_name) VALUES (?,?,?,?)", [
        (11, TWITCH, CAMPAIGN, "SkiiMaskTimmy"), (12, FILE_A, "blizzard_nordics", "Blizzard Entertainment"),
        (13, FILE_B, "blizzard_nordics", "Blizzard Entertainment"), (14, "WOW_x", CAMPAIGN, "SkiiMaskTimmy"),
        (22, FILE_A, "blizzard_nordics", "Blizzard Entertainment")])
    db.executemany("INSERT INTO clips (vod_id, profile, status, url) VALUES (?,?,?,?)", [
        (TWITCH, CAMPAIGN, "postad", "https://www.tiktok.com/@a/video/1"), (FILE_A, "blizzard_nordics", "godkand", None),
        (FILE_B, "blizzard_nordics", "renderad", None), ("upload", "annan", "betald", None)])
    db.execute("INSERT INTO posts (clip_id, platform, url, status) VALUES (1, 'tiktok', "
               "'https://www.tiktok.com/@a/video/1', 'postad')")
    db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()


def _rows(path) -> dict:
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
                for table in ("clips", "posts")} | {
            "runs": db.execute("SELECT run_id, vod_id, profile FROM runs ORDER BY 1").fetchall(),
            "project_ids": db.execute("SELECT run_id, project_id FROM runs ORDER BY 1").fetchall(),
            "projects": db.execute("SELECT id, profile, kind, title FROM projects ORDER BY 1").fetchall(),
            "version": db.execute("PRAGMA user_version").fetchone()[0]}


def _cut(rows: list, old: list) -> list:
    """Raderna med bara de gamla kolumnerna: nya kolumner (NEW_COLUMNS) läggs sist."""
    return [row[:len(old[0])] for row in rows]


def test_the_migration_saves_a_copy_first_and_puts_own_files_in_one_project_per_campaign(tmp_path) -> None:
    path = tmp_path / "klipp.db"
    _old_cliplog(path)
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
                  for table in ("clips", "posts", "runs")}

    ClipLog(path)

    [copy] = tmp_path.glob("klipp.db.fore-projekt-*")
    with sqlite3.connect(copy) as db:  # kopian är klipploggen före migreringen
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'projects'").fetchone()
        assert {t: _cut(db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall(), before[t])  # noqa: S608
                for t in before} == before
    after = _rows(path)
    assert after["version"] == 3
    assert _cut(after["clips"], before["clips"]) == before["clips"]
    assert _cut(after["posts"], before["posts"]) == before["posts"]
    assert after["runs"] == [row[:3] for row in before["runs"]]
    projects = {(profile, kind): (pid, title) for pid, profile, kind, title in after["projects"]}
    assert projects.keys() == {("blizzard_nordics", "egna"), (CAMPAIGN, "egna"), ("annan", "egna")}
    blizzard, timmy = projects["blizzard_nordics", "egna"][0], projects[CAMPAIGN, "egna"][0]
    assert projects["blizzard_nordics", "egna"][1] == "Egna filer för Blizzard Entertainment"
    assert projects[CAMPAIGN, "egna"][1] == "Egna filer för SkiiMaskTimmy"
    assert dict(after["project_ids"]) == {11: None, 12: blizzard, 13: blizzard, 14: timmy, 22: blizzard}


def test_the_migration_gives_the_same_result_when_it_runs_twice(tmp_path) -> None:
    path = tmp_path / "klipp.db"
    _old_cliplog(path)
    ClipLog(path)
    once = _rows(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 2")
    ClipLog(path)
    assert _rows(path) == once
    ClipLog(path)  # user_version 3: inget görs, ingen ny kopia
    assert _rows(path) == once
    assert len(list(tmp_path.glob("klipp.db.fore-projekt-*"))) == 2


def test_a_new_cliplog_needs_no_copy(tmp_path) -> None:
    ClipLog(tmp_path / "klipp.db")
    assert _rows(tmp_path / "klipp.db")["version"] == 3
    assert list(tmp_path.glob("klipp.db.fore-projekt-*")) == []


def test_undo_takes_the_files_out_of_the_project(client, settings) -> None:  # noqa: F811
    run_id = _file(settings, FILE_A)
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE runs SET project_id = NULL")  # ångra, som det står i cliplog.py
    assert client.get(f"/campaign/runs/{run_id}").json()["project_id"] is None
    assert _project(client)["files"] == []


# ---------- live-klipparens delar ----------

LIVE_PART = f"{TWITCH}_live_2"  # live.part_id: en del av en Twitch-stream, ingen egen fil


def test_a_live_part_gets_no_project_and_shows_as_its_own_card(client, settings) -> None:  # noqa: F811
    run_id = _file(settings, LIVE_PART, origin=None)  # som live.py anropar add_run
    assert _log(settings).run(run_id)["project_id"] is None
    assert client.get(f"/campaign/runs/{run_id}").json()["project_id"] is None
    assert [r["project_id"] for r in client.get("/campaign/runs").json() if r["run_id"] == run_id] == [None]
    assert client.get("/campaign/projects").json() == []


def test_the_migration_leaves_live_parts_outside_the_projects(tmp_path) -> None:
    path = tmp_path / "klipp.db"
    _old_cliplog(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO runs (run_id, vod_id, profile, channel_name) VALUES (?,?,?,?)",
                   (15, LIVE_PART, CAMPAIGN, "SkiiMaskTimmy"))
    ClipLog(path)
    assert dict(_rows(path)["project_ids"])[15] is None
