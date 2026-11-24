"""Uppladdning till YouTube som Shorts. Låtsas-Google via httpx.MockTransport, inga riktiga anrop."""

from __future__ import annotations

import base64
import hashlib
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

from campaign import api, youtube, youtube_api
from campaign.cliplog import ClipLog

from .conftest import set_helper, set_profile

KEYS = ("cid_test.apps.googleusercontent.com", "GOCSPX-test_secret")
ACCOUNTS = "youtube_accounts.json"
RELOGIN = "Sign in to YouTube again under Settings."
UNREACHABLE = "YouTube did not answer. Try again in a while."
MESSAGE = "YouTube is processing the video. It can take a few minutes before it shows."
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=sess1"


class FakeGoogle:
    """Svarar som Google på token, revoke, channels, uppladdning (POST och PUT) och videos. Sparar alla anrop.

    down: steg som ger nätverksfel.
    """

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.scope = SCOPES
        self.channel = {"id": "UC1", "snippet": {"title": "Clip Vault", "customUrl": "@clipvault",
                                                 "thumbnails": {"default": {"url": "https://yt3/a.jpg"}}}}
        self.refresh_error = False
        self.upload_error: tuple[int, str] | None = None  # (HTTP-status, Googles reason)
        self.put_status: int | None = None
        self.put_lost = False  # PUT av filen kastar nätverksfel, fast Google kan ha tagit emot den
        self.resume_status = 308  # svaret när sessionen tillfrågas (Content-Range: bytes */N)
        self.upload_errors: list[tuple[int, str]] = []  # fel för de första startanropen, ett i taget
        self.video: dict | None = {"uploadStatus": "uploaded"}  # None: videon finns inte (borttagen)
        self.down: set[str] = set()
        self.token_delay = 0.0

    @staticmethod
    def step(request: httpx.Request) -> str:
        if request.method == "PUT":
            return "put"
        urls = {"token": youtube.TOKEN_URL, "revoke": youtube.REVOKE_URL, "channels": youtube.CHANNELS_URL,
                "upload": youtube.UPLOAD_URL, "videos": youtube.VIDEOS_URL}
        return next((name for name, url in urls.items() if str(request.url).startswith(url)), "")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.step(request)
        if step in self.down:
            raise httpx.ConnectError("nere", request=request)
        if step == "token":
            time.sleep(self.token_delay)
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                if self.refresh_error:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "expired"})
                self.tokens += 1
                return httpx.Response(200, json={"access_token": f"ya29.secret{self.tokens}", "expires_in": 3599,
                                                 "scope": self.scope, "token_type": "Bearer"})
            self.tokens += 1
            return httpx.Response(200, json={
                "access_token": f"ya29.secret{self.tokens}", "refresh_token": f"1//rsecret{self.tokens}",
                "expires_in": 3599, "scope": self.scope, "token_type": "Bearer"})
        if step == "channels":
            return httpx.Response(200, json={"items": [self.channel] if self.channel else []})
        if step == "upload":
            if self.upload_errors or self.upload_error:
                http, reason = self.upload_errors.pop(0) if self.upload_errors else self.upload_error
                return httpx.Response(http, json={"error": {"code": http, "message": "nej",
                                                            "errors": [{"reason": reason}]}})
            return httpx.Response(200, headers={"Location": SESSION})
        if step == "put":
            if request.headers.get("content-range", "").startswith("bytes */"):
                if self.resume_status in (200, 201):
                    return httpx.Response(self.resume_status, json={"id": "vid1"})
                return httpx.Response(self.resume_status)
            if self.put_lost:
                raise httpx.ReadTimeout("svaret kom aldrig", request=request)
            if self.put_status:
                return httpx.Response(self.put_status, json={"error": {"code": self.put_status}})
            return httpx.Response(200, json={"id": "vid1", "status": {"uploadStatus": "uploaded"}})
        if step == "videos":
            video_id = parse_qs(urlparse(str(request.url)).query)["id"][0]
            items = [{"id": video_id, "status": self.video}] if self.video else []
            return httpx.Response(200, json={"items": items})
        if step == "revoke":
            return httpx.Response(200)
        return httpx.Response(404)

    def calls(self, step: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.step(r) == step]


@pytest.fixture
def fake(monkeypatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(youtube, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(youtube_api, "_pending", {})
    return fake


@pytest.fixture
def client(settings, fake) -> TestClient:
    settings.youtube_client_id, settings.youtube_client_secret = KEYS
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.include_router(youtube_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False, client=("127.0.0.1", 50000))


def _connect(settings, channel_id: str = "UC1", title: str = "Clip Vault", access: str = "ya29.old",
             expires_in_s: int = 3600) -> None:
    """Spara en token som om Robin redan har kopplat kanalen."""
    youtube.YouTube(settings).save_account({
        "access_token": access, "refresh_token": access.replace("ya29", "1//r"), "channel_id": channel_id,
        "title": title, "handle": "@clipvault", "avatar_url": "https://yt3/a.jpg",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in_s)).isoformat()})


def _saved(settings) -> dict:
    return json.loads((settings.data_dir / ACCOUNTS).read_text())


def _clip(settings, status="godkand", problems=None, size=25, seconds=20.0) -> int:
    path = settings.out_dir / "123" / "klara" / f"clip_{status}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(size)))
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="123", profile="skiimasktimmy", file=str(path), title="No way", caption="No way #fps",
        status=status, clip_format="funny", problems=problems or [], start_s=1.0, end_s=1.0 + seconds)


BODY = {"title": "No way", "description": "No way #fps #shorts", "privacy": "public", "made_for_kids": False}


def _send(client, clip_id: int, **fields):
    return client.post(f"/campaign/clips/{clip_id}/youtube", json={**BODY, **fields})


def _later(monkeypatch, minutes: float) -> None:
    moment = datetime.now(UTC) + timedelta(minutes=minutes)
    monkeypatch.setattr(youtube, "_now", lambda: moment)


def _login(client, fake, channel_id: str = "UC1", title: str = "Clip Vault") -> str:
    fake.channel = {"id": channel_id, "snippet": {"title": title, "customUrl": "@" + title.lower().replace(" ", ""),
                                                  "thumbnails": {"default": {"url": "https://yt3/a.jpg"}}}}
    state = parse_qs(urlparse(client.get("/campaign/youtube/connect").headers["location"]).query)["state"][0]
    return client.get("/campaign/youtube/callback/", params={"code": "c", "state": state}).headers["location"]


# ---------- inställningar och nycklar ----------


def test_settings_read_the_youtube_keys(monkeypatch, tmp_path) -> None:
    from campaign.config import load_settings

    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id1")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "s1")
    settings = load_settings(tmp_path)
    assert (settings.youtube_client_id, settings.youtube_client_secret) == ("id1", "s1")
    assert settings.youtube_redirect_uri == "http://127.0.0.1:8000/campaign/youtube/callback/"


def test_the_router_is_registered_in_the_app() -> None:
    from clipsmith.api.app import app

    paths = set(app.openapi()["paths"])
    assert {"/campaign/youtube/status", "/campaign/clips/{clip_id}/youtube"} <= paths


# ---------- inloggning ----------


def test_connect_without_keys_gives_400(client, settings) -> None:
    settings.youtube_client_secret = ""
    response = client.get("/campaign/youtube/connect")
    assert response.status_code == 400
    assert "YOUTUBE_CLIENT_ID" in response.json()["detail"]


def test_connect_redirects_to_google_with_pkce(client, settings) -> None:
    response = client.get("/campaign/youtube/connect")
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == youtube.AUTH_URL == "https://accounts.google.com/o/oauth2/v2/auth"
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["client_id"] == KEYS[0]
    assert query["redirect_uri"] == "http://127.0.0.1:8000/campaign/youtube/callback/"
    assert (query["response_type"], query["access_type"], query["prompt"]) == ("code", "offline",
                                                                            "consent select_account")
    assert query["include_granted_scopes"] == "true"
    assert query["scope"] == SCOPES
    assert query["code_challenge_method"] == "S256"
    _, verifier = youtube_api._pending[query["state"]]
    assert 43 <= len(verifier) <= 128
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == digest  # base64url utan =, som i RFC 7636


def test_connect_and_callback_only_from_this_computer(settings, fake) -> None:
    settings.youtube_client_id, settings.youtube_client_secret = KEYS
    app = FastAPI()
    app.include_router(youtube_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    other = TestClient(app, follow_redirects=False, client=("192.168.1.20", 50000))
    response = other.get("/campaign/youtube/connect")
    assert (response.status_code, response.json()["detail"]) == (
        403, "The account can only be connected from the computer where the server runs.")
    assert other.get("/campaign/youtube/callback/", params={"code": "c", "state": "s"}).status_code == 403
    assert fake.requests == []


def test_callback_saves_the_channel_privately_and_goes_back_to_the_web(client, settings, fake) -> None:
    location = _login(client, fake)
    assert location == "http://localhost:3000/settings?youtube=ok"
    (token,) = fake.calls("token")
    form = parse_qs(token.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["c"]
    assert form["code_verifier"][0] and form["redirect_uri"] == [settings.youtube_redirect_uri]
    (channels,) = fake.calls("channels")
    assert parse_qs(urlparse(str(channels.url)).query) == {"part": ["snippet"], "mine": ["true"]}
    assert channels.headers["authorization"] == "Bearer ya29.secret1"

    path = settings.data_dir / ACCOUNTS
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    saved = _saved(settings)["UC1"]
    assert (saved["refresh_token"], saved["title"], saved["handle"], saved["avatar_url"]) == (
        "1//rsecret1", "Clip Vault", "@clipvault", "https://yt3/a.jpg")


def test_callback_with_wrong_state_is_refused(client, settings, fake) -> None:
    client.get("/campaign/youtube/connect")
    response = client.get("/campaign/youtube/callback/", params={"code": "c", "state": "fel"})
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert (response.status_code, query["youtube"], bool(query["orsak"][0])) == (302, ["fel"], True)
    assert urlparse(response.headers["location"]).path == "/settings"
    assert fake.requests == []
    assert not (settings.data_dir / ACCOUNTS).exists()


def test_callback_when_robin_says_no(client, settings, fake) -> None:
    state = parse_qs(urlparse(client.get("/campaign/youtube/connect").headers["location"]).query)["state"][0]
    response = client.get("/campaign/youtube/callback/", params={"error": "access_denied", "state": state})
    assert parse_qs(urlparse(response.headers["location"]).query)["youtube"] == ["fel"]
    assert fake.requests == []


@pytest.mark.parametrize("scope", ["https://www.googleapis.com/auth/youtube.readonly",
                                   "https://www.googleapis.com/auth/youtube.upload"])
def test_callback_without_both_rights_is_refused(client, settings, fake, scope) -> None:
    fake.scope = scope  # Google låter Robin kryssa ur rättigheter
    query = parse_qs(urlparse(_login(client, fake)).query)
    assert query["youtube"] == ["fel"] and query["orsak"][0]
    assert not (settings.data_dir / ACCOUNTS).exists()


def test_callback_for_an_account_without_a_channel_is_refused(client, settings, fake) -> None:
    state = parse_qs(urlparse(client.get("/campaign/youtube/connect").headers["location"]).query)["state"][0]
    fake.channel = None
    response = client.get("/campaign/youtube/callback/", params={"code": "c", "state": state})
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["youtube"] == ["fel"] and "kanal" in query["orsak"][0]
    assert not (settings.data_dir / ACCOUNTS).exists()


def test_two_channels_are_kept_in_order_and_the_same_one_is_replaced(client, settings, fake) -> None:
    _login(client, fake, "UC1", "Clip Vault")
    _login(client, fake, "UC2", "Andra Kanalen")
    _login(client, fake, "UC1", "Nytt Namn")
    saved = _saved(settings)
    assert list(saved) == ["UC1", "UC2"]
    assert (saved["UC1"]["title"], saved["UC1"]["refresh_token"]) == ("Nytt Namn", "1//rsecret3")


# ---------- status ----------


def test_status_lists_the_channels_without_tokens(client, settings, fake) -> None:
    uri = settings.youtube_redirect_uri
    assert client.get("/campaign/youtube/status").json() == {
        "configured": True, "accounts": [], "uploads_today": 0, "redirect_uri": uri}
    _connect(settings)
    status = client.get("/campaign/youtube/status")
    assert status.json() == {"configured": True, "uploads_today": 0, "redirect_uri": uri, "accounts": [{
        "channel_id": "UC1", "title": "Clip Vault", "handle": "@clipvault", "avatar_url": "https://yt3/a.jpg",
        "connected": True, "needs_login": False}]}
    for secret in ("ya29.", "1//r", *KEYS):
        assert secret not in status.text
    assert fake.requests == []


def test_uploads_today_counts_the_uploads(client, settings, fake) -> None:
    _connect(settings)
    assert _send(client, _clip(settings)).status_code == 202
    assert client.get("/campaign/youtube/status").json()["uploads_today"] == 1


# ---------- token ----------


def test_expired_token_is_refreshed_before_the_call_and_saved(client, settings, fake) -> None:
    _connect(settings, expires_in_s=30)  # går ut inom en minut
    assert _send(client, _clip(settings)).status_code == 202
    assert fake.step(fake.requests[0]) == "token"
    assert parse_qs(fake.requests[0].content.decode())["refresh_token"] == ["1//r.old"]
    assert fake.calls("upload")[0].headers["authorization"] == "Bearer ya29.secret1"
    saved = _saved(settings)["UC1"]
    assert (saved["access_token"], saved["refresh_token"]) == ("ya29.secret1", "1//r.old")  # refresh_token består


def test_refresh_that_google_refuses_asks_to_log_in_again(client, settings, fake) -> None:
    _connect(settings, expires_in_s=-10)
    fake.refresh_error = True
    response = _send(client, _clip(settings))
    assert (response.status_code, response.json()["detail"]) == (400, RELOGIN)
    assert fake.calls("upload") == []
    (account,) = client.get("/campaign/youtube/status").json()["accounts"]
    assert (account["connected"], account["needs_login"]) == (False, True)


def test_two_calls_at_once_refresh_the_token_only_once(settings, fake) -> None:
    settings.youtube_client_id, settings.youtube_client_secret = KEYS
    _connect(settings, expires_in_s=-10)
    fake.token_delay = 0.2

    def call() -> None:
        with youtube.http_client() as http:
            youtube.YouTube(settings)._access_token(http, "UC1")

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(fake.calls("token")) == 1


# ---------- uppladdning ----------


def test_only_approved_clips_without_problems_can_be_uploaded(client, settings, fake) -> None:
    _connect(settings)
    for clip_id in (_clip(settings, status="renderad"), _clip(settings, problems=["Overlayen saknas."])):
        response = _send(client, clip_id)
        assert response.status_code == 400 and response.json()["detail"]
    assert _send(client, 999).status_code == 404
    assert fake.requests == []


def test_missing_file_is_refused(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    os.remove(ClipLog(settings.data_dir / "klipp.db").get(clip_id)["file"])
    assert _send(client, clip_id).status_code == 400
    assert fake.requests == []


def test_upload_needs_a_connection(client, settings, fake) -> None:
    response = _send(client, _clip(settings))
    assert response.status_code == 400 and "Settings" in response.json()["detail"]
    assert fake.requests == []


def test_upload_needs_the_keys(client, settings, fake) -> None:
    _connect(settings)
    settings.youtube_client_id = ""
    assert _send(client, _clip(settings)).status_code == 400
    assert fake.requests == []


def test_upload_sends_the_short_with_the_choices(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    response = _send(client, clip_id, notify_subscribers=False)
    assert response.status_code == 202, response.text
    assert response.json() == {"clip_id": clip_id, "video_id": "vid1", "url": "https://www.youtube.com/shorts/vid1",
                               "status": "uploaded", "channel_id": "UC1", "message": MESSAGE}
    (init,) = fake.calls("upload")
    query = parse_qs(urlparse(str(init.url)).query)
    assert (query["uploadType"], query["part"], query["notifySubscribers"]) == (
        ["resumable"], ["snippet,status"], ["false"])
    assert init.headers["authorization"] == "Bearer ya29.old"
    assert (init.headers["x-upload-content-length"], init.headers["x-upload-content-type"]) == ("25", "video/mp4")
    assert json.loads(init.content) == {
        "snippet": {"title": "No way", "description": "No way #fps #shorts", "categoryId": "20"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}}
    (put,) = fake.calls("put")
    assert (str(put.url), put.content, put.headers["content-type"]) == (SESSION, bytes(range(25)), "video/mp4")
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["status"] == "postad"  # inlägget skapas av hjälpen
    with youtube.YouTube(settings)._db() as db:
        row = dict(db.execute("SELECT clip_id, channel_id, video_id, status, privacy FROM youtube_uploads").fetchone())
    assert row == {"clip_id": clip_id, "channel_id": "UC1", "video_id": "vid1", "status": "uploaded",
                   "privacy": "public"}


def test_subscribers_are_notified_by_default(client, settings, fake) -> None:
    _connect(settings)
    assert _send(client, _clip(settings), made_for_kids=True, privacy="unlisted").status_code == 202
    (init,) = fake.calls("upload")
    assert parse_qs(urlparse(str(init.url)).query).get("notifySubscribers", ["true"]) == ["true"]
    assert json.loads(init.content)["status"]["selfDeclaredMadeForKids"] is True


@pytest.mark.parametrize("fields", [
    {"privacy": None}, {"privacy": "friends"}, {"made_for_kids": None},
    {"title": ""}, {"title": "   "}, {"title": "x" * 101}, {"title": "No <way>"}, {"title": None},
    {"description": "å" * 2501}])  # 5002 byte
def test_wrong_choices_are_refused(client, settings, fake, fields) -> None:
    _connect(settings)
    response = _send(client, _clip(settings), **fields)
    assert response.status_code == 400 and response.json()["detail"]
    assert fake.requests == []


def test_limits_that_are_just_fine(client, settings, fake) -> None:
    _connect(settings)
    assert _send(client, _clip(settings), title="x" * 100, description="å" * 2500).status_code == 202


def test_a_clip_longer_than_a_short_is_refused(client, settings, fake) -> None:
    _connect(settings)
    response = _send(client, _clip(settings, seconds=180.5))
    assert response.status_code == 400 and "180" in response.json()["detail"]
    assert _send(client, _clip(settings, seconds=180)).status_code == 202


def test_with_two_channels_the_upload_must_say_which(client, settings, fake) -> None:
    _connect(settings)
    _connect(settings, channel_id="UC2", title="Andra", access="ya29.other")
    clip_id = _clip(settings)
    response = _send(client, clip_id)
    assert (response.status_code, response.json()["detail"]) == (400, "Choose which YouTube channel the clip goes to.")
    assert _send(client, clip_id, channel_id="okänd").status_code == 400
    response = _send(client, clip_id, channel_id="UC2")
    assert (response.status_code, response.json()["channel_id"]) == (202, "UC2")
    assert fake.calls("upload")[0].headers["authorization"] == "Bearer ya29.other"


def test_same_clip_to_the_same_channel_right_away_gives_409(client, settings, fake, monkeypatch) -> None:
    set_helper(settings, False)  # med hjälpen på stoppar inlägget på youtube_shorts redan andra försöket (400)
    _connect(settings)
    _connect(settings, channel_id="UC2", title="Andra", access="ya29.other")
    clip_id = _clip(settings)
    assert _send(client, clip_id, channel_id="UC1").status_code == 202
    assert _send(client, clip_id, channel_id="UC1").status_code == 409
    assert _send(client, clip_id, channel_id="UC2").status_code == 202  # en annan kanal är inget dubbelklick
    _later(monkeypatch, 11)
    assert _send(client, clip_id, channel_id="UC1").status_code == 202


@pytest.mark.parametrize(("http", "reason", "expected"), [
    (403, "quotaExceeded", 429), (400, "uploadLimitExceeded", 429), (401, "authError", 400),
    (500, "backendError", 502)])
def test_google_errors_at_the_start_give_clear_answers(client, settings, fake, http, reason, expected) -> None:
    _connect(settings)
    fake.upload_error = (http, reason)
    response = _send(client, _clip(settings))
    assert response.status_code == expected and response.json()["detail"]
    if http == 401:
        assert response.json()["detail"] == RELOGIN
    assert fake.calls("put") == []
    with youtube.YouTube(settings)._db() as db:
        assert db.execute("SELECT COUNT(*) FROM youtube_uploads").fetchone()[0] == 0


@pytest.mark.parametrize("step", ["upload", "put"])
def test_google_not_answering_gives_502_and_a_failed_row(client, settings, fake, step) -> None:
    _connect(settings)
    fake.down.add(step)
    clip_id = _clip(settings)
    response = _send(client, clip_id)
    assert (response.status_code, response.json()["detail"]) == (502, UNREACHABLE)
    if step == "put":
        latest = client.get(f"/campaign/clips/{clip_id}/youtube").json()
        assert (latest["status"], latest["error"]) == ("failed", UNREACHABLE)
        assert _send(client, clip_id).status_code == 502  # ett misslyckat försök spärrar inte


def test_put_that_google_refuses_fails_the_upload(client, settings, fake) -> None:
    _connect(settings)
    fake.put_status = 400
    clip_id = _clip(settings)
    assert _send(client, clip_id).status_code == 502
    assert client.get(f"/campaign/clips/{clip_id}/youtube").json()["status"] == "failed"


def test_post_routes_need_the_api_key(client, settings, fake, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    _connect(settings)
    clip_id = _clip(settings)
    assert _send(client, clip_id).status_code == 401
    assert client.post("/campaign/youtube/accounts/UC1/disconnect").status_code == 401
    assert client.get("/campaign/youtube/status").status_code == 200  # läser: ingen nyckel
    assert fake.requests == []
    response = client.post(f"/campaign/clips/{clip_id}/youtube", json=BODY, headers={"X-Api-Key": "nyckel"})
    assert response.status_code == 202


# ---------- status för uppladdningen ----------


def test_upload_status_is_read_fresh_but_not_too_often(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    assert client.get(f"/campaign/clips/{clip_id}/youtube").json() is None
    _send(client, clip_id, privacy="unlisted")
    fake.video = {"uploadStatus": "processed", "privacyStatus": "unlisted"}
    _later(monkeypatch, 0.5)
    latest = client.get(f"/campaign/clips/{clip_id}/youtube").json()
    assert latest == {"video_id": "vid1", "url": "https://www.youtube.com/shorts/vid1", "status": "uploaded",
                      "error": None, "privacy": "unlisted", "channel_id": "UC1", "title": "Clip Vault"}
    assert fake.calls("videos") == []  # sparat i en minut

    _later(monkeypatch, 2)
    assert client.get(f"/campaign/clips/{clip_id}/youtube").json()["status"] == "processed"
    (videos,) = fake.calls("videos")
    assert parse_qs(urlparse(str(videos.url)).query) == {"part": ["status,processingDetails"], "id": ["vid1"]}
    _later(monkeypatch, 10)
    client.get(f"/campaign/clips/{clip_id}/youtube")
    assert len(fake.calls("videos")) == 1  # klar: ingen mer hämtning


@pytest.mark.parametrize("video", [{"uploadStatus": "rejected", "rejectionReason": "duplicate"},
                                   {"uploadStatus": "failed", "failureReason": "codec"},
                                   {"uploadStatus": "deleted"}, None])
def test_a_rejected_failed_or_deleted_video_is_failed_with_a_reason(client, settings, fake, monkeypatch,
                                                                     video) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    _send(client, clip_id)
    fake.video = video
    _later(monkeypatch, 2)
    latest = client.get(f"/campaign/clips/{clip_id}/youtube").json()
    assert latest["status"] == "failed" and latest["error"]


def test_status_that_google_does_not_answer_shows_the_saved_one(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    _send(client, clip_id)
    fake.down.add("videos")
    _later(monkeypatch, 2)
    assert client.get(f"/campaign/clips/{clip_id}/youtube").json()["status"] == "uploaded"


# ---------- koppla från ----------


def test_disconnect_revokes_at_google_and_removes_the_channel_even_if_google_is_down(client, settings,
                                                                                    fake) -> None:
    _connect(settings)
    _connect(settings, channel_id="UC2", title="Andra", access="ya29.other")
    assert client.post("/campaign/youtube/accounts/okänd/disconnect").status_code == 404
    assert client.post("/campaign/youtube/accounts/youtube_accounts.json/disconnect").status_code == 404
    assert fake.calls("revoke") == []

    response = client.post("/campaign/youtube/accounts/UC2/disconnect")
    assert response.status_code == 200
    assert [a["channel_id"] for a in response.json()["accounts"]] == ["UC1"]
    (revoke,) = fake.calls("revoke")
    assert parse_qs(revoke.content.decode())["token"] == ["1//r.other"]
    assert list(_saved(settings)) == ["UC1"]

    fake.down.add("revoke")
    assert client.post("/campaign/youtube/accounts/UC1/disconnect").status_code == 200
    assert not (settings.data_dir / ACCOUNTS).exists()


def test_no_token_is_shown_in_answers_or_the_log(client, settings, fake, caplog) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    answers = [_login(client, fake, "UC1", "A"), _login(client, fake, "UC2", "B")]
    clip_id = _clip(settings)
    answers += [_send(client, clip_id, channel_id="UC2").text, client.get(f"/campaign/clips/{clip_id}/youtube").text,
                client.get("/campaign/youtube/status").text,
                client.post("/campaign/youtube/accounts/UC1/disconnect").text]
    for text in (*answers, caplog.text):
        for secret in ("ya29.", "1//r", *KEYS):
            assert secret not in text


# ---------- granskningen: tappade svar, avbrutna uppladdningar, 401 och låset ----------


@pytest.mark.parametrize("lost", ["network", "5xx"])
def test_a_lost_answer_to_the_put_is_checked_once_and_the_video_kept(client, settings, fake, lost) -> None:
    _connect(settings)
    if lost == "network":
        fake.put_lost = True
    else:
        fake.put_status = 503
    fake.resume_status = 200
    clip_id = _clip(settings)
    response = _send(client, clip_id)
    assert response.status_code == 202, response.text
    assert (response.json()["video_id"], response.json()["status"]) == ("vid1", "uploaded")
    upload, asked = fake.calls("put")
    assert upload.content == bytes(range(25))
    assert (str(asked.url), asked.headers["content-range"], asked.content) == (SESSION, "bytes */25", b"")
    with youtube.YouTube(settings)._db() as db:
        assert tuple(db.execute("SELECT video_id, status FROM youtube_uploads").fetchone()) == ("vid1", "uploaded")


def test_a_lost_answer_where_google_has_not_got_the_file_fails(client, settings, fake) -> None:
    _connect(settings)
    fake.put_lost = True  # sessionen svarar 308: filen kom inte fram
    clip_id = _clip(settings)
    response = _send(client, clip_id)
    assert (response.status_code, response.json()["detail"]) == (502, UNREACHABLE)
    assert len(fake.calls("put")) == 2
    assert client.get(f"/campaign/clips/{clip_id}/youtube").json()["status"] == "failed"


def _uploading_row(settings, clip_id: int, minutes_ago: float) -> None:
    created = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    with youtube.YouTube(settings)._db() as db:
        db.execute("INSERT INTO youtube_uploads (clip_id, channel_id, status, privacy, created) "
                   "VALUES (?, 'UC1', 'uploading', 'public', ?)", (clip_id, created))


def test_an_upload_that_stopped_halfway_is_failed_after_ten_minutes(client, settings, fake) -> None:
    _connect(settings)
    fresh, old = _clip(settings), _clip(settings)
    _uploading_row(settings, fresh, 1)
    _uploading_row(settings, old, 11)  # servern dog mitt i
    assert client.get(f"/campaign/clips/{fresh}/youtube").json()["status"] == "uploading"
    latest = client.get(f"/campaign/clips/{old}/youtube").json()
    assert (latest["status"], latest["error"]) == ("failed", "The upload was aborted. Try again.")
    with youtube.YouTube(settings)._db() as db:
        assert db.execute("SELECT status FROM youtube_uploads WHERE clip_id = ?", (old,)).fetchone()[0] == "failed"
    assert fake.requests == []


def test_a_token_that_google_refuses_is_refreshed_and_the_start_tried_again(client, settings, fake) -> None:
    _connect(settings)  # token ser giltig ut, men Google har dragit in den
    fake.upload_errors = [(401, "authError")]
    response = _send(client, _clip(settings))
    assert response.status_code == 202, response.text
    assert len(fake.calls("token")) == 1
    first, second = fake.calls("upload")
    assert (first.headers["authorization"], second.headers["authorization"]) == ("Bearer ya29.old",
                                                                                 "Bearer ya29.secret1")
    assert _saved(settings)["UC1"]["access_token"] == "ya29.secret1"


def test_a_refused_token_that_cannot_be_refreshed_asks_to_log_in_again(client, settings, fake) -> None:
    _connect(settings)
    fake.upload_errors = [(401, "authError")]
    fake.refresh_error = True
    response = _send(client, _clip(settings))
    assert (response.status_code, response.json()["detail"]) == (400, RELOGIN)
    assert len(fake.calls("upload")) == 1
    (account,) = client.get("/campaign/youtube/status").json()["accounts"]
    assert account["needs_login"] is True


def test_a_failed_status_call_is_not_repeated_within_a_minute(client, settings, fake, monkeypatch) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    _send(client, clip_id)
    fake.down.add("videos")
    _later(monkeypatch, 2)
    client.get(f"/campaign/clips/{clip_id}/youtube")
    client.get(f"/campaign/clips/{clip_id}/youtube")
    assert len(fake.calls("videos")) == 1
    _later(monkeypatch, 4)
    client.get(f"/campaign/clips/{clip_id}/youtube")
    assert len(fake.calls("videos")) == 2


def test_another_upload_in_progress_gives_409_at_once(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    assert youtube._upload_lock.acquire(timeout=1)
    timer = threading.Timer(1, youtube._upload_lock.release)  # så att testet inte hänger om anropet väntar
    timer.start()
    response = _send(client, clip_id)
    timer.join()
    assert (response.status_code, response.json()["detail"]) == (
        409, "Another upload to YouTube is running. Wait until it is done.")
    assert fake.requests == []
    assert _send(client, clip_id).status_code == 202


# ---------- hjälpen för inskick och kampanjens plattformar ----------


def test_a_successful_upload_creates_the_post_when_the_helper_is_on(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    before = datetime.now(UTC).replace(microsecond=0)
    assert _send(client, clip_id).status_code == 202
    log = ClipLog(settings.data_dir / "klipp.db")
    (post,) = log.posts(clip_id)
    assert (post["platform"], post["url"], post["status"]) == (
        "youtube_shorts", "https://www.youtube.com/shorts/vid1", "postad")
    assert datetime.fromisoformat(post["created"]) >= before  # tiden när appen fick adressen
    assert log.get(clip_id)["status"] == "postad"


def test_no_post_is_created_when_the_helper_is_off(client, settings, fake) -> None:
    set_helper(settings, False)
    _connect(settings)
    clip_id = _clip(settings)
    assert _send(client, clip_id).status_code == 202
    log = ClipLog(settings.data_dir / "klipp.db")
    assert log.posts(clip_id) == [] and log.get(clip_id)["status"] == "godkand"


def test_a_failed_upload_creates_no_post(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    fake.put_status = 400
    assert _send(client, clip_id).status_code == 502
    assert ClipLog(settings.data_dir / "klipp.db").posts(clip_id) == []


def test_a_campaign_without_youtube_refuses_the_upload(client, settings, fake) -> None:
    set_profile(settings, platforms=["tiktok"])
    _connect(settings)
    response = _send(client, _clip(settings))
    assert (response.status_code, response.json()["detail"]) == (400, "The campaign does not post on YouTube.")
    assert fake.calls("upload") == []


def test_a_clip_posted_on_tiktok_can_go_to_youtube_but_not_twice(client, settings, fake) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@a/video/1")
    assert log.get(clip_id)["status"] == "postad"
    assert _send(client, clip_id).status_code == 202
    again = _send(client, clip_id)
    assert (again.status_code, again.json()["detail"]) == (400, "The clip already has a post on YouTube.")
    assert len(fake.calls("upload")) == 1


@pytest.mark.parametrize("privacy", ["unlisted", "private"])
def test_a_video_that_is_not_public_creates_no_post(client, settings, fake, privacy) -> None:
    _connect(settings)
    clip_id = _clip(settings)
    assert _send(client, clip_id, privacy=privacy).status_code == 202
    assert ClipLog(settings.data_dir / "klipp.db").posts(clip_id) == []


def test_the_upload_keeps_its_title_and_description_for_the_text_rule(client, settings, fake) -> None:
    """Samma klipp på flera kanaler: texten får inte vara samma, så den sparas med uppladdningen."""
    _connect(settings)
    assert _send(client, _clip(settings)).status_code == 202
    with youtube.YouTube(settings)._db() as db:
        row = db.execute("SELECT title, description FROM youtube_uploads").fetchone()
    assert tuple(row) == ("No way", "No way #fps #shorts")


def test_status_has_the_redirect_address_from_the_server(settings) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from campaign import api, tiktok_api, youtube_api

    settings.youtube_redirect_uri = "http://127.0.0.1:9000/campaign/youtube/callback/"
    settings.tiktok_redirect_uri = "http://127.0.0.1:9000/campaign/tiktok/callback/"
    app = FastAPI()
    app.include_router(youtube_api.router)
    app.include_router(tiktok_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    client = TestClient(app)
    assert client.get("/campaign/youtube/status").json()["redirect_uri"] == settings.youtube_redirect_uri
    assert client.get("/campaign/tiktok/status").json()["redirect_uri"] == settings.tiktok_redirect_uri


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "localhost.evil.example"])
def test_connect_and_callback_refuse_another_host_name(client, fake, host) -> None:
    """DNS rebinding: a web page whose name resolves to 127.0.0.1 reaches the server from this computer."""
    assert client.get("/campaign/youtube/connect", headers={"host": host}).status_code == 403
    response = client.get("/campaign/youtube/callback/", params={"code": "c", "state": "s"}, headers={"host": host})
    assert response.status_code == 403
    assert fake.requests == []
