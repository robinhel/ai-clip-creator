"""Koppling till Robins TikTok-konto: inloggning (Login Kit för desktop), token och uppladdning
som utkast till TikTok-inkorgen (Content Posting API, scope video.upload).

Ingen caption går med till TikTok. Robin öppnar TikTok-appen, trycker på notisen i inkorgen,
klistrar in captionen och postar. Direktpostning (video.publish) kräver granskning av TikTok
och byggs inte nu.

Token sparas i klippdata/tiktok_token.json med rättigheter 600. Token loggas aldrig.
Uppladdningarna sparas i tabellen tiktok_uploads i klippdata/klipp.db.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .config import Settings

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_URL = "https://open.tiktokapis.com/v2/user/info/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
SCOPES = "user.info.basic,video.upload"

# Delar: minst 5 MB och högst 64 MB per del, sista delen tar resten (högst 128 MB).
# 64_000_000 i stället för 64 MiB, så att gränsen håller oavsett hur TikTok räknar MB.
WHOLE_MAX = 64_000_000  # en fil upp till hit skickas i en del
CHUNK = 10_000_000  # delarnas storlek för större filer, som i TikToks exempel
PENDING_LIMIT = 5  # TikTok tar högst 5 väntande utkast per 24 timmar
STATUS_CACHE_S = 60  # status/fetch tillåter 30 anrop per minut; webben behöver inte färskare än en minut
RECENT_S = 600  # samma klipp igen inom 10 minuter är troligen ett dubbelklick
DONE = ("failed", "publish_complete")
MESSAGE = "Öppna TikTok-appen och tryck på notisen i inkorgen. Klistra in captionen och posta."
RECONNECT = "Koppla TikTok igen under Kampanjer."
UNREACHABLE = "TikTok svarade inte. Försök igen om en stund."

# Läsa, förnya och spara token sker ett anrop i taget, så att samma refresh_token inte används två gånger.
_token_lock = threading.Lock()
# En uppladdning i taget, så att ett dubbelklick inte hinner skicka samma klipp två gånger.
_upload_lock = threading.Lock()


class TikTokError(Exception):
    """Fel med svensk text och HTTP-status för API:t."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30.0, write=120.0))


def chunks(size: int) -> tuple[int, list[tuple[int, int]]]:
    """chunk_size och (första, sista byte) per del. Antalet delar är size // chunk_size."""
    if size <= WHOLE_MAX:
        return size, [(0, size - 1)]
    count = size // CHUNK
    parts = [(i * CHUNK, (i + 1) * CHUNK - 1) for i in range(count)]
    parts[-1] = (parts[-1][0], size - 1)  # sista delen tar resten
    return CHUNK, parts


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_code(response: httpx.Response) -> str:
    body = _json(response)
    if not body and response.status_code != 200:
        return f"http_{response.status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("code") or "ok"
    return str(error or "ok")


def _data(response: httpx.Response) -> dict:
    data = _json(response).get("data")
    return data if isinstance(data, dict) else {}


class TikTok:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_path = settings.data_dir / "tiktok_token.json"
        self.db_path = settings.data_dir / "klipp.db"

    @property
    def configured(self) -> bool:
        return bool(self.settings.tiktok_client_key and self.settings.tiktok_client_secret)

    # ---------- token ----------

    def token(self) -> dict | None:
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_token(self, token: dict) -> None:
        """Skriv en ny tmp-fil (rättigheter 600 från början) och byt sedan ut den gamla."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.token_path.parent, prefix=".tiktok_token.",
                                         suffix=".tmp", delete=False, encoding="utf-8") as file:
            json.dump(token, file)
        os.chmod(file.name, 0o600)
        os.replace(file.name, self.token_path)

    def disconnect(self) -> None:
        """Återkalla tillgången hos TikTok och ta bort token. Svarar inte TikTok tas filen ändå bort."""
        with _token_lock:
            token = self.token()
            if token and self.configured:
                with contextlib.suppress(httpx.HTTPError), http_client() as http:
                    http.post(REVOKE_URL, data={"client_key": self.settings.tiktok_client_key,
                                                "client_secret": self.settings.tiktok_client_secret,
                                                "token": token.get("access_token", "")})
            self.token_path.unlink(missing_ok=True)

    def connected(self) -> bool:
        token = self.token()
        return bool(token) and datetime.fromisoformat(token["refresh_expires_at"]) > _now()

    def _token_request(self, http: httpx.Client, **form: str) -> dict:
        data = {"client_key": self.settings.tiktok_client_key,
                "client_secret": self.settings.tiktok_client_secret, **form}
        try:
            response = http.post(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError as error:
            raise TikTokError(UNREACHABLE) from error
        body = _json(response)
        if "access_token" not in body or "refresh_token" not in body:
            if response.status_code == 200 or response.status_code >= 500:
                raise TikTokError(UNREACHABLE)
            raise TikTokError(f"TikTok gav ingen token ({body.get('error') or response.status_code}). "
                              f"{RECONNECT}", 400)
        now = _now()
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "open_id": body.get("open_id", ""),
            "scopes": [s for s in body.get("scope", "").split(",") if s],
            "expires_at": _iso(now + timedelta(seconds=int(body.get("expires_in", 0)))),
            "refresh_expires_at": _iso(now + timedelta(seconds=int(body.get("refresh_expires_in", 0)))),
        }

    def connect(self, code: str, verifier: str) -> None:
        """Byt koden från inloggningen mot token, hämta namn och bild, och spara."""
        with http_client() as http:
            token = self._token_request(http, code=code, grant_type="authorization_code",
                                        redirect_uri=self.settings.tiktok_redirect_uri,
                                        code_verifier=verifier)
            try:
                response = http.get(USER_URL, params={"fields": "open_id,display_name,avatar_url"},
                                    headers={"Authorization": f"Bearer {token['access_token']}"})
                user = _data(response).get("user") or {}
            except httpx.HTTPError:
                user = {}  # namn och bild behövs inte för att ladda upp
        token.update(display_name=user.get("display_name"), avatar_url=user.get("avatar_url"))
        with _token_lock:
            self.save_token(token)

    def _access_token(self, http: httpx.Client) -> str:
        """Giltig access token. En som gått ut (eller går ut inom en minut) förnyas först."""
        if not self.configured:
            raise TikTokError("TIKTOK_CLIENT_KEY och TIKTOK_CLIENT_SECRET saknas i .env.", 400)
        with _token_lock:
            token = self.token()  # läs inuti låset: ett annat anrop kan just ha förnyat den
            if not token or not self.connected():
                raise TikTokError(f"TikTok är inte kopplat. {RECONNECT}", 400)
            if datetime.fromisoformat(token["expires_at"]) - timedelta(seconds=60) <= _now():
                fresh = self._token_request(http, grant_type="refresh_token",
                                            refresh_token=token["refresh_token"])
                token = {**token, **fresh}  # namn och bild finns kvar
                self.save_token(token)
            return token["access_token"]

    # ---------- uppladdningar ----------

    def _db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS tiktok_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, publish_id TEXT,
            status TEXT, error TEXT, created TEXT, checked TEXT)""")
        return db

    def _pending_rows(self) -> list[sqlite3.Row]:
        since = _iso(_now() - timedelta(hours=24))
        with self._db() as db:
            return db.execute(
                f"SELECT id, publish_id FROM tiktok_uploads WHERE created > ? AND status NOT IN {DONE}",
                (since,)).fetchall()

    def pending_24h(self) -> int:
        return len(self._pending_rows())

    def _set(self, upload_id: int, **fields) -> None:
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            db.execute(f"UPDATE tiktok_uploads SET {sets} WHERE id = ?", (*fields.values(), upload_id))

    def _latest_row(self, clip_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM tiktok_uploads WHERE clip_id = ? ORDER BY id DESC LIMIT 1",
                             (clip_id,)).fetchone()
        return dict(row) if row else None

    def _refresh_pending(self, http: httpx.Client, access: str) -> None:
        """Färsk status för de väntande utkasten. Robin kan ha postat eller tagit bort dem i appen."""
        for row in self._pending_rows():
            fresh = self._fetch_status(http, access, row["publish_id"])
            self._set(row["id"], **fresh, checked=_iso(_now()))

    def _init(self, http: httpx.Client, access: str, size: int, chunk_size: int, count: int) -> dict:
        try:
            response = http.post(INIT_URL, headers={"Authorization": f"Bearer {access}",
                                                    "Content-Type": "application/json; charset=UTF-8"},
                                 json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                                       "chunk_size": chunk_size, "total_chunk_count": count}})
        except httpx.HTTPError as error:
            raise TikTokError(UNREACHABLE) from error
        code = _error_code(response)
        if code.startswith("spam_risk"):
            raise TikTokError(f"TikTok tar inte emot fler utkast just nu ({code}). Posta eller ta bort "
                              "de som väntar i inkorgen, eller vänta ett dygn.", 429)
        if code == "rate_limit_exceeded":
            raise TikTokError("TikTok tar bara emot 6 anrop i minuten. Vänta en minut.", 429)
        if code in ("access_token_invalid", "scope_not_authorized"):
            raise TikTokError(f"TikTok godkände inte inloggningen ({code}). {RECONNECT}", 400)
        if response.status_code >= 500:
            raise TikTokError(UNREACHABLE)
        if response.status_code != 200 or code != "ok":
            raise TikTokError(f"TikTok tog inte emot uppladdningen ({code}).")
        data = _data(response)
        if not data.get("publish_id") or not data.get("upload_url"):
            raise TikTokError(UNREACHABLE)
        return data

    def upload(self, clip_id: int, path: Path) -> dict:
        """Ladda upp filen som utkast. Klippets status i klipploggen ändras inte."""
        size = path.stat().st_size
        if size == 0:
            raise TikTokError("Filen är tom.", 400)
        with _upload_lock, http_client() as http:
            last = self._latest_row(clip_id)
            if last and (last["status"] == "uploading" or (
                    last["status"] == "uploaded"
                    and _now() - datetime.fromisoformat(last["created"]) < timedelta(seconds=RECENT_S))):
                raise TikTokError("Klippet skickades nyss. Titta i TikTok-inkorgen.", 409)
            access = self._access_token(http)
            if self.pending_24h() >= PENDING_LIMIT:
                self._refresh_pending(http, access)  # bara vid gränsen, så att status/fetch inte används i onödan
            if self.pending_24h() >= PENDING_LIMIT:
                raise TikTokError(f"{PENDING_LIMIT} utkast väntar redan i TikTok-inkorgen. Posta eller ta "
                                  "bort dem i TikTok-appen, eller vänta ett dygn.", 429)
            chunk_size, parts = chunks(size)
            data = self._init(http, access, size, chunk_size, len(parts))

            created = _iso(_now())
            with self._db() as db:
                upload_id = db.execute(
                    "INSERT INTO tiktok_uploads (clip_id, publish_id, status, created) VALUES (?,?,?,?)",
                    (clip_id, data["publish_id"], "uploading", created)).lastrowid
            try:
                with path.open("rb") as file:
                    for first, last_byte in parts:
                        file.seek(first)
                        put = http.put(data["upload_url"], content=file.read(last_byte - first + 1), headers={
                            "Content-Type": "video/mp4", "Content-Range": f"bytes {first}-{last_byte}/{size}"})
                        if put.status_code not in (201, 206):
                            raise TikTokError(f"TikTok tog inte emot filen (svar {put.status_code}).")
            except (TikTokError, httpx.HTTPError, OSError) as error:
                if isinstance(error, TikTokError):
                    message = str(error)
                elif isinstance(error, httpx.HTTPError):
                    message = UNREACHABLE
                else:
                    message = "Klippets fil gick inte att läsa."
                self._set(upload_id, status="failed", error=message)
                raise TikTokError(message) from error
            self._set(upload_id, status="uploaded")
        return {"clip_id": clip_id, "publish_id": data["publish_id"], "status": "uploaded",
                "created": created, "message": MESSAGE}

    def latest(self, clip_id: int) -> dict | None:
        """Senaste uppladdningen för klippet, med status från TikTok (sparas en minut)."""
        upload = self._latest_row(clip_id)
        if upload is None:
            return None
        checked = upload["checked"] and datetime.fromisoformat(upload["checked"])
        if upload["status"] not in DONE and (not checked or (_now() - checked).total_seconds() >= STATUS_CACHE_S):
            try:
                with http_client() as http:
                    upload.update(self._fetch_status(http, self._access_token(http), upload["publish_id"]))
                self._set(upload["id"], status=upload["status"], error=upload["error"], checked=_iso(_now()))
            except TikTokError:
                pass  # TikTok svarar inte eller är inte kopplat: visa det som är sparat
        return {key: upload[key] for key in ("clip_id", "publish_id", "status", "error", "created")}

    def _fetch_status(self, http: httpx.Client, access: str, publish_id: str) -> dict:
        try:
            response = http.post(STATUS_URL, json={"publish_id": publish_id}, headers={
                "Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8"})
        except httpx.HTTPError as error:
            raise TikTokError(UNREACHABLE) from error
        code, data = _error_code(response), _data(response)
        if response.status_code >= 500 or (code == "ok" and not data.get("status")):
            raise TikTokError(UNREACHABLE)
        if response.status_code != 200 or code != "ok":
            raise TikTokError(f"TikTok gav ingen status ({code}).")
        status = str(data["status"]).lower()
        if status == "send_to_user_inbox":
            return {"status": "uploaded", "error": None}
        if status == "failed":
            return {"status": "failed",
                    "error": f"TikTok kunde inte ta emot klippet ({data.get('fail_reason') or 'okänt fel'})."}
        return {"status": status, "error": None}

    def status(self) -> dict:
        """Läget för webben. Aldrig någon token eller nyckel."""
        token = self.token() if self.connected() else None
        return {
            "configured": self.configured,
            "connected": token is not None,
            "display_name": token and token.get("display_name"),
            "avatar_url": token and token.get("avatar_url"),
            "scopes": token["scopes"] if token else [],
            "refresh_expires_at": token and token["refresh_expires_at"],
            "pending_24h": self.pending_24h(),
            "pending_limit": PENDING_LIMIT,
        }
