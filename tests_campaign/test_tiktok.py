"""Uppladdning till TikTok som utkast. Låtsas-TikTok via httpx.MockTransport, inga riktiga anrop."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, tiktok, tiktok_api
from campaign.cliplog import ClipLog

KEYS = ("ck_test_key", "cs_test_secret")


class FakeTikTok:
    """Svarar som TikTok på token, användarinfo, init, PUT och status. Sparar alla anrop."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.init_error: tuple[int, str] | None = None
        self.refresh_error = False
        self.status = "SEND_TO_USER_INBOX"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(tiktok.TOKEN_URL):
            if self.refresh_error and b"refresh_token" in request.content:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "expired"})
            self.tokens += 1
            return httpx.Response(200, json={
                "access_token": f"act.secret{self.tokens}", "refresh_token": f"rft.secret{self.tokens}",
                "expires_in": 7200, "refresh_expires_in": 15552000, "open_id": "o1",
                "scope": "user.info.basic,video.upload", "token_type": "Bearer"})
        if url.startswith(tiktok.USER_URL):
            return httpx.Response(200, json={"data": {"user": {
                "open_id": "o1", "display_name": "clipvault.hq26", "avatar_url": "https://p16/a.jpg"}},
                "error": {"code": "ok"}})
        if url.startswith(tiktok.INIT_URL):
            if self.init_error:
                code, http = self.init_error
                return httpx.Response(http, json={"error": {"code": code, "message": "nej"}})
            return httpx.Response(200, json={"data": {
                "publish_id": f"v_inbox_file~v2.{len(self.requests)}",
                "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1"}, "error": {"code": "ok"}})
        if request.method == "PUT":
            rng = request.headers["content-range"].removeprefix("bytes ")
            last, total = rng.split("-")[1].split("/")
            return httpx.Response(201 if int(last) + 1 == int(total) else 206)
        if url.startswith(tiktok.STATUS_URL):
            return httpx.Response(200, json={"data": {"status": self.status, "fail_reason": "file_format_check_failed"},
                                             "error": {"code": "ok"}})
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
    return TestClient(app, follow_redirects=False)


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
    clip_id = _clip(settings)
    for _ in range(5):
        assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 202
    response = client.post(f"/campaign/clips/{clip_id}/tiktok")
    assert response.status_code == 429
    assert len(fake.calls(tiktok.INIT_URL)) == 5
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 5

    later = datetime.now(UTC) + timedelta(hours=25)
    monkeypatch.setattr(tiktok, "_now", lambda: later)
    assert client.get("/campaign/tiktok/status").json()["pending_24h"] == 0


def test_tiktoks_own_spam_limit_gives_429(client, settings, fake) -> None:
    _connect(settings)
    fake.init_error = (403, "spam_risk_too_many_pending_share")
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
    assert all(b + 1 == c for (_, b), (c, _) in zip(parts, parts[1:], strict=False))


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


def test_disconnect_removes_the_token(client, settings) -> None:
    _connect(settings)
    assert client.post("/campaign/tiktok/disconnect").json() == {"connected": False}
    assert not (settings.data_dir / "tiktok_token.json").exists()
    assert client.get("/campaign/tiktok/status").json()["connected"] is False


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
