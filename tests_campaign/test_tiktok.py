"""Uppladdning till TikTok som utkast. Låtsas-TikTok via httpx.MockTransport, inga riktiga anrop."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import stat
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, tiktok, tiktok_api
from campaign.cliplog import ClipLog

KEYS = ("ck_test_key", "cs_test_secret")
REVOKE = "https://open.tiktokapis.com/v2/oauth/revoke/"
UNREACHABLE = "TikTok svarade inte. Försök igen om en stund."


class FakeTikTok:
    """Svarar som TikTok på token, användarinfo, init, PUT, status och revoke. Sparar alla anrop.

    down: steg som ger nätverksfel. empty: steg som svarar 200 utan innehåll.
    """

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.init_error: tuple[str, int] | None = None  # (TikToks felkod, HTTP-status)
        self.refresh_error = False
        self.status = "SEND_TO_USER_INBOX"
        self.statuses: dict[str, str] = {}  # publish_id -> status, annars self.status
        self.put_status: int | None = None
        self.down: set[str] = set()
        self.empty: set[str] = set()
        self.token_delay = 0.0

    @staticmethod
    def step(request: httpx.Request) -> str:
        if request.method == "PUT":
            return "put"
        urls = {"token": tiktok.TOKEN_URL, "user": tiktok.USER_URL, "init": tiktok.INIT_URL,
                "status": tiktok.STATUS_URL, "revoke": REVOKE}
        return next((name for name, url in urls.items() if str(request.url).startswith(url)), "")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.step(request)
        if step in self.down:
            raise httpx.ConnectError("nere", request=request)
        if step in self.empty:
            return httpx.Response(200, json={})
        if step == "token":
            time.sleep(self.token_delay)
            if self.refresh_error and b"refresh_token" in request.content:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "expired"})
            self.tokens += 1
            return httpx.Response(200, json={
                "access_token": f"act.secret{self.tokens}", "refresh_token": f"rft.secret{self.tokens}",
                "expires_in": 7200, "refresh_expires_in": 15552000, "open_id": "o1",
                "scope": "user.info.basic,video.upload", "token_type": "Bearer"})
        if step == "user":
            return httpx.Response(200, json={"data": {"user": {
                "open_id": "o1", "display_name": "clipvault.hq26", "avatar_url": "https://p16/a.jpg"}},
                "error": {"code": "ok"}})
        if step == "init":
            if self.init_error:
                code, http = self.init_error
                return httpx.Response(http, json={"error": {"code": code, "message": "nej"}})
            return httpx.Response(200, json={"data": {
                "publish_id": f"v_inbox_file~v2.{len(self.requests)}",
                "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1"}, "error": {"code": "ok"}})
        if step == "put":
            if self.put_status:
                return httpx.Response(self.put_status)
            rng = request.headers["content-range"].removeprefix("bytes ")
            last, total = rng.split("-")[1].split("/")
            return httpx.Response(201 if int(last) + 1 == int(total) else 206)
        if step == "status":
            status = self.statuses.get(json.loads(request.content)["publish_id"], self.status)
            return httpx.Response(200, json={"data": {"status": status, "fail_reason": "file_format_check_failed"},
                                             "error": {"code": "ok"}})
        if step == "revoke":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def calls(self, prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(prefix)]


@pytest.fixture
def fake(monkeypatch) -> FakeTikTok:
    fake = FakeTikTok()
    monkeypatch.setattr(tiktok, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(tiktok_api, "_pending", {})
    return fake


@pytest.fixture
def client(settings, fake) -> TestClient:
    settings.tiktok_client_key, settings.tiktok_client_secret = KEYS
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.include_router(tiktok_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app, follow_redirects=False, client=("127.0.0.1", 50000))


def _connect(settings, expires_in_s: int = 7200) -> None:
    """Spara en token som om Robin redan har kopplat TikTok."""
    now = datetime.now(UTC)
    tiktok.TikTok(settings).save_token({
        "access_token": "act.old", "refresh_token": "rft.old", "open_id": "o1",
        "scopes": ["user.info.basic", "video.upload"], "display_name": "clipvault.hq26",
        "avatar_url": "https://p16/a.jpg",
        "expires_at": (now + timedelta(seconds=expires_in_s)).isoformat(),
        "refresh_expires_at": (now + timedelta(days=180)).isoformat(),
    })


def _clip(settings, status="godkand", problems=None, size=25) -> int:
    path = settings.out_dir / "123" / "klara" / f"clip_{status}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(size)))
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="123", profile="skiimasktimmy", file=str(path), title="No way", caption="No way #fps",
        status=status, clip_format="funny", problems=problems or [], start_s=1.0, end_s=20.0)


# ---------- 1. bara godkända klipp ----------


def test_only_approved_clips_without_problems_can_be_uploaded(client, settings, fake) -> None:
    _connect(settings)
    for clip_id in (_clip(settings, status="renderad"), _clip(settings, problems=["Overlayen saknas."])):
        response = client.post(f"/campaign/clips/{clip_id}/tiktok")
        assert response.status_code == 400
        assert response.json()["detail"]
    assert client.post("/campaign/clips/999/tiktok").status_code == 404
    assert fake.calls(tiktok.INIT_URL) == []


# ---------- 2. kopplat och högst 5 väntande ----------


def test_upload_needs_a_connection(client, settings, fake) -> None:
    clip_id = _clip(settings)
    response = client.post(f"/campaign/clips/{clip_id}/tiktok")
    assert response.status_code == 400
    assert "Koppla" in response.json()["detail"]
    assert fake.requests == []


def test_five_pending_drafts_in_24_hours_gives_429(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clips = [_clip(settings) for _ in range(6)]
    for clip_id in clips[:5]:
        assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202
    response = client.post(f"/campaign/clips/{clips[5]}/tiktok")
    assert response.status_code == 429
    assert len(fake.calls(tiktok.INIT_URL)) == 5
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 5

    later = datetime.now(UTC) + timedelta(hours=25)
    monkeypatch.setattr(tiktok, "_now", lambda: later)
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 0


def test_tiktoks_own_spam_limit_gives_429(client, settings, fake) -> None:
    _connect(settings)
    fake.init_error = ("spam_risk_too_many_pending_share", 403)
    response = client.post(f"/campaign/clips/{_clip(settings)}/tiktok")
    assert response.status_code == 429
    assert response.json()["detail"]


# ---------- 3. delarna ----------


def test_chunks_follow_tiktoks_rules() -> None:
    assert tiktok.chunks(3_000_000) == (3_000_000, [(0, 2_999_999)])
    assert tiktok.chunks(30_000_000) == (30_000_000, [(0, 29_999_999)])
    size, parts = tiktok.chunks(150_000_123)
    assert size == 10_000_000
    assert len(parts) == 15 == 150_000_123 // size
    assert parts[0] == (0, 9_999_999)
    assert parts[-1] == (140_000_000, 150_000_122)  # sista delen tar resten
    assert all(b + 1 == c for (_, b), (c, _) in itertools.pairwise(parts))


def test_upload_sends_the_file_in_parts_with_content_range(client, settings, fake, monkeypatch) -> None:
    monkeypatch.setattr(tiktok, "CHUNK", 10)
    monkeypatch.setattr(tiktok, "WHOLE_MAX", 10)
    _connect(settings)
    clip_id = _clip(settings, size=25)
    response = client.post(f"/campaign/clips/{clip_id}/tiktok")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["publish_id"].startswith("v_inbox_file")
    assert "inkorgen" in body["message"]

    (init,) = fake.calls(tiktok.INIT_URL)
    assert init.headers["authorization"] == "Bearer act.old"
    assert json.loads(init.content) == {"source_info": {
        "source": "FILE_UPLOAD", "video_size": 25, "chunk_size": 10, "total_chunk_count": 2}}
    puts = [r for r in fake.requests if r.method == "PUT"]
    assert [p.headers["content-range"] for p in puts] == ["bytes 0-9/25", "bytes 10-24/25"]
    assert [p.headers["content-length"] for p in puts] == ["10", "15"]
    assert {p.headers["content-type"] for p in puts} == {"video/mp4"}
    assert b"".join(p.content for p in puts) == bytes(range(25))


# ---------- 4. token förnyas ----------


def test_expired_token_is_refreshed_before_the_call_and_saved(client, settings, fake) -> None:
    _connect(settings, expires_in_s=-10)
    assert client.post(f"/campaign/clips/{_clip(settings)}/tiktok").status_code == 202
    first, second = fake.requests[:2]
    assert str(first.url).startswith(tiktok.TOKEN_URL)
    form = parse_qs(first.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["rft.old"]
    assert form["client_key"] == [KEYS[0]]
    assert str(second.url).startswith(tiktok.INIT_URL)
    assert second.headers["authorization"] == "Bearer act.secret1"
    saved = json.loads((settings.data_dir / "tiktok_token.json").read_text())
    assert (saved["access_token"], saved["refresh_token"]) == ("act.secret1", "rft.secret1")
    assert saved["display_name"] == "clipvault.hq26"  # namnet finns kvar efter förnyelsen


def test_refresh_that_fails_asks_to_connect_again(client, settings, fake) -> None:
    _connect(settings, expires_in_s=-10)
    fake.refresh_error = True
    response = client.post(f"/campaign/clips/{_clip(settings)}/tiktok")
    assert response.status_code == 400
    assert "Koppla" in response.json()["detail"]
    assert fake.calls(tiktok.INIT_URL) == []


# ---------- 5. inloggningen ----------


def test_connect_without_keys_gives_400(client, settings) -> None:
    settings.tiktok_client_key = ""
    response = client.get("/campaign/tiktok/connect")
    assert response.status_code == 400
    assert "TIKTOK_CLIENT_KEY" in response.json()["detail"]


def test_connect_redirects_to_tiktok_with_pkce(client, settings) -> None:
    response = client.get("/campaign/tiktok/connect")
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == tiktok.AUTH_URL
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["client_key"] == KEYS[0]
    assert query["response_type"] == "code"
    assert query["scope"] == "user.info.basic,video.upload"
    assert query["redirect_uri"] == "http://127.0.0.1:8000/campaign/tiktok/callback/"
    assert query["code_challenge_method"] == "S256"
    _, verifier = tiktok_api._pending[query["state"]]
    assert 43 <= len(verifier) <= 128
    assert query["code_challenge"] == hashlib.sha256(verifier.encode()).hexdigest()  # hex, som TikTok vill


def test_callback_with_wrong_state_is_refused(client, settings, fake) -> None:
    client.get("/campaign/tiktok/connect")
    response = client.get("/campaign/tiktok/callback/", params={"code": "c1", "state": "fel"})
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["tiktok"] == ["fel"]
    assert query["orsak"][0]
    assert fake.requests == []
    assert not (settings.data_dir / "tiktok_token.json").exists()


def test_callback_saves_the_token_privately_and_goes_back_to_the_web(client, settings, fake) -> None:
    location = client.get("/campaign/tiktok/connect").headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    _, verifier = tiktok_api._pending[state]

    response = client.get("/campaign/tiktok/callback/", params={"code": "c1", "state": state,
                                                               "scopes": "user.info.basic,video.upload"})
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3000/campaigns?tiktok=ok"
    form = parse_qs(fake.calls(tiktok.TOKEN_URL)[0].content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["c1"]
    assert form["code_verifier"] == [verifier]
    assert form["client_secret"] == [KEYS[1]]
    assert form["redirect_uri"] == ["http://127.0.0.1:8000/campaign/tiktok/callback/"]

    path = settings.data_dir / "tiktok_token.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "act.secret1"
    assert saved["display_name"] == "clipvault.hq26"
    assert saved["scopes"] == ["user.info.basic", "video.upload"]

    again = client.get("/campaign/tiktok/callback/", params={"code": "c1", "state": state})
    assert "tiktok=fel" in again.headers["location"]  # state gäller en gång


def test_callback_when_robin_says_no(client) -> None:
    location = client.get("/campaign/tiktok/connect").headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    response = client.get("/campaign/tiktok/callback/", params={
        "state": state, "error": "access_denied", "error_description": "User cancelled"})
    assert "tiktok=fel" in response.headers["location"]


# ---------- 6. status utan hemligheter ----------


def test_status_never_shows_tokens_or_keys(client, settings, fake) -> None:
    status = client.get("/campaign/tiktok/status").json()
    assert (status["configured"], status["connected"], status["pending_limit"]) == (True, False, 5)

    location = client.get("/campaign/tiktok/connect").headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    client.get("/campaign/tiktok/callback/", params={"code": "c1", "state": state})
    status = client.get("/campaign/tiktok/status").json()
    assert status["connected"] is True
    assert status["display_name"] == "clipvault.hq26"
    assert status["scopes"] == ["user.info.basic", "video.upload"]
    assert status["refresh_expires_at"]
    text = json.dumps(status)
    for secret in ("act.secret", "rft.secret", *KEYS, "o1"):
        assert secret not in text

    settings.tiktok_client_key = ""
    assert client.get("/campaign/tiktok/status").json()["configured"] is False


def test_disconnect_revokes_at_tiktok_and_removes_the_token_even_if_tiktok_is_down(client, settings,
                                                                                    fake) -> None:
    _connect(settings)
    assert client.post("/campaign/tiktok/disconnect").json() == {"connected": False}
    (revoke,) = fake.calls(REVOKE)
    assert parse_qs(revoke.content.decode()) == {
        "client_key": [KEYS[0]], "client_secret": [KEYS[1]], "token": ["act.old"]}
    assert not (settings.data_dir / "tiktok_token.json").exists()
    assert client.get("/campaign/tiktok/status").json()["connected"] is False

    _connect(settings)
    fake.down = {"revoke"}
    assert client.post("/campaign/tiktok/disconnect").json() == {"connected": False}
    assert not (settings.data_dir / "tiktok_token.json").exists()


# ---------- 7. klippets status ändras inte ----------


def test_upload_does_not_change_the_clips_status_and_its_status_can_be_read(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    assert client.get(f"/campaign/clips/{clip_id}/tiktok").json() is None

    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["status"] == "godkand"

    latest = client.get(f"/campaign/clips/{clip_id}/tiktok").json()
    assert (latest["clip_id"], latest["status"], latest["error"]) == (clip_id, "uploaded", None)
    assert len(fake.calls(tiktok.STATUS_URL)) == 1
    client.get(f"/campaign/clips/{clip_id}/tiktok")
    assert len(fake.calls(tiktok.STATUS_URL)) == 1  # TikTok tillåter 6 anrop per minut, så status sparas en stund


def test_failed_upload_at_tiktok_is_shown_with_a_reason(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    client.post(f"/campaign/clips/{clip_id}/tiktok")
    fake.status = "FAILED"
    later = datetime.now(UTC) + timedelta(minutes=5)
    monkeypatch.setattr(tiktok, "_now", lambda: later)
    latest = client.get(f"/campaign/clips/{clip_id}/tiktok").json()
    assert latest["status"] == "failed"
    assert "file_format_check_failed" in latest["error"]
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 0


def test_missing_file_is_refused(client, settings) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    path = ClipLog(settings.data_dir / "klipp.db").get(clip_id)["file"]
    os.remove(path)
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 400


# ---------- granskningens fynd ----------


def test_connect_and_callback_only_from_this_computer(client, settings, fake) -> None:
    for host in ("127.0.0.1", "::1"):
        local = TestClient(client.app, follow_redirects=False, client=(host, 50000))
        assert local.get("/campaign/tiktok/connect").status_code == 302
    state = parse_qs(urlparse(client.get("/campaign/tiktok/connect").headers["location"]).query)["state"][0]

    other = TestClient(client.app, follow_redirects=False, client=("192.168.1.20", 50000))
    assert other.get("/campaign/tiktok/connect").status_code == 403
    response = other.get("/campaign/tiktok/callback/", params={"code": "c1", "state": state})
    assert response.status_code == 403
    assert fake.requests == []
    assert not (settings.data_dir / "tiktok_token.json").exists()


def test_limit_counts_fresh_status_so_a_posted_draft_frees_a_place(client, settings, fake) -> None:
    _connect(settings)
    clips = [_clip(settings) for _ in range(6)]
    published = [client.post(f"/campaign/clips/{c}/tiktok").json()["publish_id"] for c in clips[:5]]
    fake.statuses[published[0]] = "PUBLISH_COMPLETE"
    assert client.post(f"/campaign/clips/{clips[5]}/tiktok").status_code == 202
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 5


def test_status_check_before_the_limit_that_fails_gives_502(client, settings, fake) -> None:
    _connect(settings)
    clips = [_clip(settings) for _ in range(6)]
    for clip_id in clips[:5]:
        client.post(f"/campaign/clips/{clip_id}/tiktok")
    fake.down = {"status"}
    response = client.post(f"/campaign/clips/{clips[5]}/tiktok")
    assert (response.status_code, response.json()["detail"]) == (502, UNREACHABLE)


def test_two_calls_at_once_refresh_the_token_only_once(settings, fake) -> None:
    settings.tiktok_client_key, settings.tiktok_client_secret = KEYS
    _connect(settings, expires_in_s=-10)
    fake.token_delay = 0.3
    tokens: list[str] = []

    def call() -> None:
        with tiktok.http_client() as http:
            tokens.append(tiktok.TikTok(settings)._access_token(http))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert fake.tokens == 1
    assert tokens == ["act.secret1", "act.secret1"]
    assert [p.name for p in settings.data_dir.iterdir() if "tiktok" in p.name] == ["tiktok_token.json"]


@pytest.mark.parametrize(("down", "empty"), [({"init"}, set()), (set(), {"init"}), ({"put"}, set())])
def test_tiktok_not_answering_gives_502_and_the_draft_does_not_wait(client, settings, fake, down,
                                                                    empty) -> None:
    _connect(settings)
    fake.down, fake.empty = down, empty
    clip_id = _clip(settings)
    response = client.post(f"/campaign/clips/{clip_id}/tiktok")
    assert (response.status_code, response.json()["detail"]) == (502, UNREACHABLE)
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 0
    latest = client.get(f"/campaign/clips/{clip_id}/tiktok").json()
    assert latest is None or latest["status"] == "failed"

    fake.down, fake.empty = set(), set()
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202  # går att skicka igen


def test_put_that_tiktok_refuses_fails_the_draft(client, settings, fake) -> None:
    _connect(settings)
    fake.put_status = 500
    clip_id = _clip(settings)
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 502
    latest = client.get(f"/campaign/clips/{clip_id}/tiktok").json()
    assert latest["status"] == "failed"
    assert latest["error"]
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 0


@pytest.mark.parametrize(("down", "empty"), [({"token"}, set()), (set(), {"token"})])
def test_refresh_that_tiktok_does_not_answer_gives_502(client, settings, fake, down, empty) -> None:
    _connect(settings, expires_in_s=-10)
    fake.down, fake.empty = down, empty
    response = client.post(f"/campaign/clips/{_clip(settings)}/tiktok")
    assert (response.status_code, response.json()["detail"]) == (502, UNREACHABLE)
    assert fake.calls(tiktok.INIT_URL) == []


def test_status_that_tiktok_does_not_answer_shows_the_saved_one(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    client.post(f"/campaign/clips/{clip_id}/tiktok")
    fake.empty = {"status"}
    assert client.get(f"/campaign/clips/{clip_id}/tiktok").json()["status"] == "uploaded"


def test_same_clip_again_right_away_gives_409_but_later_is_fine(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202
    again = client.post(f"/campaign/clips/{clip_id}/tiktok")
    assert again.status_code == 409
    assert again.json()["detail"] == "Klippet skickades nyss. Titta i TikTok-inkorgen."

    busy = _clip(settings)
    with tiktok.TikTok(settings)._db() as db:
        db.execute("INSERT INTO tiktok_uploads (clip_id, publish_id, status, created) VALUES (?,?,?,?)",
                   (busy, "p1", "uploading", "2026-01-01T00:00:00+00:00"))
    assert client.post(f"/campaign/clips/{busy}/tiktok").status_code == 409

    later = datetime.now(UTC) + timedelta(minutes=11)
    monkeypatch.setattr(tiktok, "_now", lambda: later)
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202
    assert len(fake.calls(tiktok.INIT_URL)) == 2


def test_post_routes_need_the_api_key(client, settings, fake, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    _connect(settings)
    clip_id = _clip(settings)
    assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 401
    assert client.post("/campaign/tiktok/disconnect").status_code == 401
    assert (settings.data_dir / "tiktok_token.json").exists()
    assert fake.requests == []
    assert client.post(f"/campaign/clips/{clip_id}/tiktok", headers={"X-Api-Key": "nyckel"}).status_code == 202


@pytest.mark.parametrize(("code", "http", "expected"),
                         [("access_token_invalid", 401, 400), ("rate_limit_exceeded", 429, 429)])
def test_tiktok_errors_at_init_give_clear_answers(client, settings, fake, code, http, expected) -> None:
    _connect(settings)
    fake.init_error = (code, http)
    response = client.post(f"/campaign/clips/{_clip(settings)}/tiktok")
    assert response.status_code == expected
    assert response.json()["detail"]


def test_token_that_expires_within_a_minute_is_refreshed_first(client, settings, fake) -> None:
    _connect(settings, expires_in_s=30)
    assert client.post(f"/campaign/clips/{_clip(settings)}/tiktok").status_code == 202
    assert fake.step(fake.requests[0]) == "token"
    assert fake.calls(tiktok.INIT_URL)[0].headers["authorization"] == "Bearer act.secret1"


def test_chunks_at_the_64_mb_limit() -> None:
    assert tiktok.chunks(64_000_000) == (64_000_000, [(0, 63_999_999)])
    size, parts = tiktok.chunks(64_000_001)
    assert (size, len(parts)) == (10_000_000, 6)
    assert parts[-1] == (50_000_000, 64_000_000)
