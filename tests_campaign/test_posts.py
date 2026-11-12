"""Inlägg per plattform: samma klipp postas på TikTok, Reels och Shorts, och varje inlägg skickas in,
följs och betalas för sig. Klippets länk, visningar och status räknas fram ur inläggen."""

from __future__ import annotations

import sqlite3

from campaign.cliplog import ClipLog

from .test_api import _clip, client  # noqa: F401  (fixture)

TIKTOK = "https://www.tiktok.com/@me/video/1"
REELS = "https://www.instagram.com/reel/abc/"
SHORTS = "https://youtube.com/shorts/xyz"


def _post(client, clip_id: int, platform: str, url: str):  # noqa: F811
    return client.post(f"/campaign/clips/{clip_id}/posts", json={"platform": platform, "url": url})


def test_one_clip_gets_one_post_per_platform(client, settings) -> None:  # noqa: F811
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    first = _post(client, clip_id, "tiktok", TIKTOK)
    assert first.status_code == 201, first.text
    clip = first.json()
    assert clip["status"] == "postad"
    assert [(p["platform"], p["url"], p["status"]) for p in clip["posts"]] == [("tiktok", TIKTOK, "postad")]
    assert _post(client, clip_id, "instagram_reels", REELS).status_code == 201
    assert _post(client, clip_id, "tiktok", "https://www.tiktok.com/@me/video/2").status_code == 409
    wrong = _post(client, clip_id, "youtube_shorts", REELS)  # fel domän för plattformen
    assert wrong.status_code == 400
    assert "YouTube Shorts" in wrong.json()["detail"]  # plattformens namn, inte dess id
    assert _post(client, clip_id, "myspace", REELS).status_code == 422
    clip = client.get("/campaign/clips").json()[0]
    assert [p["platform"] for p in clip["posts"]] == ["tiktok", "instagram_reels"]

    # Ett klipp som inte är godkänt, eller har problem, får inga inlägg.
    waiting = _clip(settings, clip_format="funny")
    assert _post(client, waiting, "tiktok", TIKTOK).status_code == 400
    assert client.post("/campaign/clips/999/posts", json={"platform": "tiktok", "url": TIKTOK}).status_code == 404


def test_each_post_is_sent_in_and_followed_on_its_own(client, settings) -> None:  # noqa: F811
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    tiktok = _post(client, clip_id, "tiktok", TIKTOK).json()["posts"][0]["id"]
    reels = next(p for p in _post(client, clip_id, "instagram_reels", REELS).json()["posts"]
                 if p["platform"] == "instagram_reels")["id"]

    assert client.patch(f"/campaign/posts/{tiktok}", json={"status": "inskickad"}).status_code == 200
    clip = client.patch(f"/campaign/posts/{tiktok}", json={"views": 2500, "likes": 40}).json()
    # Klippet är bara så långt som det inlägg som har kommit kortast.
    assert clip["status"] == "postad"
    assert (clip["views"], clip["likes"]) == (2500, 40)

    # Statistik inskickad går först när inlägget har nått kampanjens min_views (2 000).
    # Webben får gränsen med klippet, så att knappen kan låsas innan Robin klickar.
    assert clip["min_views"] == 2000
    assert client.patch(f"/campaign/posts/{reels}", json={"status": "inskickad"}).status_code == 200
    client.patch(f"/campaign/posts/{reels}", json={"views": 1500})
    refused = client.patch(f"/campaign/posts/{reels}", json={"status": "analytics_inskickad"})
    assert refused.status_code == 400
    assert "2000" in refused.json()["detail"].replace(" ", "")
    clip = client.patch(f"/campaign/posts/{tiktok}", json={"status": "analytics_inskickad"}).json()
    assert clip["status"] == "inskickad"
    assert clip["views"] == 4000

    # Vad Whop faktiskt betalade, per inlägg. Ett steg bakåt går att ångra.
    clip = client.patch(f"/campaign/posts/{tiktok}", json={"status": "betald", "paid_usd": 2.4}).json()
    post = next(p for p in clip["posts"] if p["id"] == tiktok)
    assert (post["status"], post["paid_usd"]) == ("betald", 2.4)
    assert client.patch(f"/campaign/posts/{tiktok}", json={"status": "analytics_inskickad"}).status_code == 200
    assert client.patch(f"/campaign/posts/{tiktok}", json={"status": "postad"}).status_code == 400  # två steg
    assert client.patch(f"/campaign/posts/{tiktok}", json={"paid_usd": -1}).status_code == 422
    assert client.patch(f"/campaign/posts/{tiktok}", json={"url": "https://"}).status_code == 400
    assert client.patch("/campaign/posts/999", json={"views": 1}).status_code == 404


def test_removing_the_last_post_makes_the_clip_approved_again(client, settings) -> None:  # noqa: F811
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    post_id = _post(client, clip_id, "tiktok", TIKTOK).json()["posts"][0]["id"]
    clip = client.delete(f"/campaign/posts/{post_id}").json()
    assert (clip["status"], clip["posts"], clip["url"]) == ("godkand", [], None)
    assert client.delete(f"/campaign/posts/{post_id}").status_code == 404


def test_the_old_way_of_posting_a_clip_makes_a_post(client, settings) -> None:  # noqa: F811
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    clip = client.patch(f"/campaign/clips/{clip_id}", json={"status": "postad", "url": TIKTOK,
                                                           "platform": "tiktok"}).json()
    assert [(p["platform"], p["status"]) for p in clip["posts"]] == [("tiktok", "postad")]
    # Ett steg för klippet gäller alla dess inlägg.
    clip = client.patch(f"/campaign/clips/{clip_id}", json={"status": "inskickad"}).json()
    assert [p["status"] for p in clip["posts"]] == ["inskickad"]
    clip = client.patch(f"/campaign/clips/{clip_id}", json={"status": "postad"}).json()
    clip = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"}).json()
    assert (clip["status"], clip["posts"]) == ("godkand", [])


def test_old_clips_with_a_link_become_one_post(settings) -> None:
    path = settings.data_dir / "klipp.db"
    log = ClipLog(path)
    clip_id = log.add(vod_id="1", profile="skiimasktimmy", file="a.mp4", title="a", caption="c", status="godkand")
    log.update(clip_id, status="inskickad", platform="tiktok", url=TIKTOK, views=3000, likes=30)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE IF EXISTS posts")  # som en logg från före inläggen
        db.execute("PRAGMA user_version = 0")
    log = ClipLog(path)
    posts = log.posts(clip_id)
    assert [(p["platform"], p["url"], p["status"], p["views"], p["likes"]) for p in posts] == [
        ("tiktok", TIKTOK, "inskickad", 3000, 30)]
    ClipLog(path)  # att öppna loggen igen ger inga dubbletter
    assert len(log.posts(clip_id)) == 1


def test_todo_reminds_about_each_post(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="1", profile="skiimasktimmy", file="a.mp4", title="Kul", caption="c", status="godkand")
    a = log.add_post(clip_id, "tiktok", TIKTOK)
    b = log.add_post(clip_id, "instagram_reels", REELS)
    log.update_post(a, status="inskickad")
    log.update_post(b, status="inskickad", views=2500)
    todo = log.todo()
    assert any("TikTok" in item and "visningar" in item for item in todo)
    assert any("Reels" in item and "skärminspelningen" in item for item in todo)
    assert not any("posta klippet" in item for item in todo)
