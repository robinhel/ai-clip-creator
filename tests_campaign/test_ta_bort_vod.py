"""Ta bort VOD-filen på ett projekt för att frigöra plats. Klippen, transkriptet, chatten och klipploggen
finns kvar. Påhittade Twitch och twitch-dl. Inga riktiga anrop."""

from __future__ import annotations

import os
import shutil

import pytest

from campaign import download, pipeline
from campaign.cliplog import ClipLog

from .conftest import PAKET
from .test_api import client  # noqa: F401  (fixture)
from .test_natt_lage import _run

VOD = "2925991945"
MB = 1024 * 1024


def _project(settings, vod_id: str = VOD, status: str = "done") -> int:
    run_id = _run(status, vod_id)
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, vod_id, "skiimasktimmy", "Bodycam", title="T",
                                                    duration_s=13950)
    return run_id


def _files(settings, vod_id: str = VOD) -> dict:
    """En VOD på disken som efter ett projekt: VOD-filen, delar från twitch-dl, det som gjordes av den och ett klipp."""
    folder = settings.work_dir / vod_id
    (folder / "delar").mkdir(parents=True)
    (folder / f"{vod_id}.mp4").write_bytes(b"v" * 3 * MB)
    (folder / f"{vod_id}.del.mp4").write_bytes(b"h" * MB)  # en halv hopslagning av delarna
    (folder / "delar" / "0001.ts").write_bytes(b"d" * 2 * MB)
    kept = {name: folder / name for name in ("transcript.json", "chat.json", "candidates.json", "picks.json")}
    for path in kept.values():
        path.write_text("{}", encoding="utf-8")
    clip = settings.out_dir / vod_id / "run_1" / "klara" / "clip_01.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"c" * MB)
    kept["clip"] = clip
    return kept


def test_delete_removes_only_the_vod_file_and_the_parts(client, settings) -> None:  # noqa: F811
    run_id = _project(settings)
    kept = _files(settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id=VOD, profile="skiimasktimmy", file=str(kept["clip"]), title="x", caption="c")
    assert client.get(f"/campaign/runs/{run_id}").json()["vod_bytes"] == 6 * MB
    assert [r["vod_bytes"] for r in client.get("/campaign/runs").json()] == [6 * MB]

    response = client.delete(f"/campaign/runs/{run_id}/vod")

    assert response.status_code == 200, response.text
    assert response.json() == {"freed_bytes": 6 * MB, "freed_gb": round(6 * MB / 1024**3, 2)}
    folder = settings.work_dir / VOD
    assert sorted(p.name for p in folder.iterdir()) == ["candidates.json", "chat.json", "picks.json", "transcript.json"]
    assert all(path.exists() for path in kept.values())
    assert log.get(clip_id)["file"] == str(kept["clip"])
    assert log.run(run_id) is not None
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert (run["vod_bytes"], run["clip_counts"]) == (None, {"renderad": 1})
    assert client.delete(f"/campaign/runs/{run_id}/vod").json()["freed_bytes"] == 0  # redan borttagen


@pytest.mark.parametrize("status", ["pending", "running"])
def test_delete_is_refused_while_the_project_runs(client, settings, status) -> None:  # noqa: F811
    run_id = _project(settings, status=status)
    _files(settings)
    response = client.delete(f"/campaign/runs/{run_id}/vod")
    assert response.status_code == 409
    assert (settings.work_dir / VOD / f"{VOD}.mp4").exists()


def test_delete_is_refused_while_another_run_uses_the_same_vod(client, settings) -> None:  # noqa: F811
    done = _project(settings)
    _files(settings)
    other = _run("running", VOD)  # en annan kampanj, eller chatten, klipper ur samma VOD
    client.app.state.active_run_id = other
    try:
        assert client.delete(f"/campaign/runs/{done}/vod").status_code == 409
    finally:
        client.app.state.active_run_id = None
    assert (settings.work_dir / VOD / f"{VOD}.mp4").exists()


def test_delete_of_an_unknown_project_is_404(client) -> None:  # noqa: F811
    assert client.delete("/campaign/runs/999/vod").status_code == 404
    assert client.delete("/campaign/runs/abc/vod").status_code == 422


@pytest.mark.parametrize("vod_id", ["..", "../utanfor", ".", "a/b"])
def test_delete_never_leaves_the_work_folder(client, settings, vod_id) -> None:  # noqa: F811
    # Filerna som en slarvig väg skulle ta bort: <work>/<id>/<id>.mp4 och delarna, utanför work/<id>/.
    folder = settings.work_dir / vod_id
    keep = [folder / f"{vod_id}.mp4", folder / "delar" / "0001.ts"]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    run_id = _project(settings, vod_id=vod_id)
    response = client.delete(f"/campaign/runs/{run_id}/vod")
    assert response.status_code == 400, response.text
    assert all(path.exists() for path in keep)
    assert client.get(f"/campaign/runs/{run_id}").json()["vod_bytes"] is None


def test_delete_does_not_follow_links_out_of_the_folder(client, settings) -> None:  # noqa: F811
    run_id = _project(settings)
    outside = settings.work_dir.parent / "ssd-delar"
    outside.mkdir()
    (outside / "0001.ts").write_bytes(b"x" * MB)
    folder = settings.work_dir / VOD
    folder.mkdir(parents=True)
    os.symlink(outside, folder / "delar")
    assert client.delete(f"/campaign/runs/{run_id}/vod").status_code == 200
    assert not (folder / "delar").exists()
    assert (outside / "0001.ts").exists()


def test_after_delete_an_own_file_cannot_run_again(client, settings) -> None:  # noqa: F811
    own = "timmy_bodycam_test"
    run_id = _project(settings, vod_id=own, status="failed")
    (settings.work_dir / own).mkdir(parents=True)
    (settings.work_dir / own / f"{own}.mp4").write_bytes(b"v" * MB)
    assert client.get(f"/campaign/runs/{run_id}").json()["retry_problem"] is None

    assert client.delete(f"/campaign/runs/{run_id}/vod").json()["freed_bytes"] == MB

    problem = client.get(f"/campaign/runs/{run_id}").json()["retry_problem"]
    assert "Run again does not work" in problem
    response = client.post(f"/campaign/runs/{run_id}/retry")
    assert (response.status_code, response.json()["detail"]) == (409, problem)


def test_run_again_on_twitch_downloads_the_vod_again_after_delete(client, settings, monkeypatch) -> None:  # noqa: F811
    """Ingen gammal kontrollpunkt hoppar över hämtningen: transkriptet och .checkpoints finns kvar."""
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.chdir(settings.root)
    run_id = _project(settings, status="failed")
    _files(settings)
    sentinels = settings.work_dir / ".checkpoints" / VOD
    sentinels.mkdir(parents=True)
    for stage in ("download", "transcribe", "chat", "candidates"):
        (sentinels / f"{stage}.done").touch()
    assert client.delete(f"/campaign/runs/{run_id}/vod").status_code == 200

    calls: list[list[str]] = []

    def twitch_dl(cmd, on_progress):
        calls.append(cmd)
        raise RuntimeError("påhittad twitch-dl stoppar här")

    monkeypatch.setattr(download, "_resolve_quality", lambda vod_id, quality: quality)
    monkeypatch.setattr(download, "_run_twitchdl", twitch_dl)
    again = _project(settings, status="pending")
    pipeline.run_campaign_vod(again, VOD, "skiimasktimmy", "Bodycam", settings)

    assert len(calls) == 1 and VOD in calls[0]
    run = client.get(f"/campaign/runs/{again}").json()
    assert run["status"] == "failed"
    assert [s["status"] for s in run["steps"]][0] == "failed"  # hämtningen, inte ett senare steg
