"""Koppling till Robins YouTube-kanaler: inloggning (Google OAuth för webbklient med PKCE), token,
uppladdning av godkända klipp som Shorts (YouTube Data API v3, resumable) och status för uppladdningen.

Bara httpx, som TikTok-koden, inga google-bibliotek. Flera kanaler kan vara kopplade. Token sparas per
kanal-id i klippdata/youtube_accounts.json med rättigheter 600. Token loggas aldrig och skickas aldrig
till webben. Uppladdningarna sparas i tabellen youtube_uploads i klippdata/klipp.db.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .preferences import add_post

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"  # behövs för channels.list mine=true
SCOPES = f"{UPLOAD_SCOPE} {READ_SCOPE}"

SHORT_MAX_S = 180  # vertikal video upp till 3 minuter blir en Short
TITLE_MAX = 100  # tecken
DESCRIPTION_MAX = 5000  # byte
PRIVACY = ("public", "unlisted", "private")
CATEGORY = "20"  # Gaming
STATUS_CACHE_S = 60
RECENT_S = 600  # samma klipp till samma kanal inom 10 minuter är troligen ett dubbelklick
DONE = ("processed", "failed")
QUOTA_DAY = ZoneInfo("America/Los_Angeles")  # YouTubes kvot börjar om vid midnatt Pacific Time
MESSAGE = "YouTube behandlar videon. Det kan ta några minuter innan den syns."
RELOGIN = "Logga in på YouTube igen under Inställningar."
UNREACHABLE = "YouTube svarade inte. Försök igen om en stund."
ABORTED = "Uppladdningen avbröts. Försök igen."
BUSY = "En annan uppladdning till YouTube pågår. Vänta tills den är klar."
NOT_CONFIGURED = "YOUTUBE_CLIENT_ID och YOUTUBE_CLIENT_SECRET saknas. Lägg in dem under Inställningar."
ERRORS = {
    "quotaExceeded": (("YouTubes kvot för i dag är slut (100 uppladdningar per projekt och dag). "
                       "Försök igen i morgon."), 429),
    "uploadLimitExceeded": ("Kanalen har nått YouTubes gräns för uppladdningar i dag. Försök igen i morgon.", 429),
}

# Läsa, förnya och spara token sker ett anrop i taget, så att två anrop inte förnyar samtidigt.
# RLock: accounts() tar låset själv och anropas också inifrån låset.
_token_lock = threading.RLock()
# En uppladdning i taget, så att ett dubbelklick inte hinner skicka samma klipp två gånger.
# Den andra väntar inte på låset utan får 409 direkt.
_upload_lock = threading.Lock()


class YouTubeError(Exception):
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


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _reason(response: httpx.Response) -> str:
    """Googles felorsak: errors[0].reason i API:t, error som text från OAuth."""
    error = _json(response).get("error")
    if isinstance(error, dict):
        errors = error.get("errors") or [{}]
        first = errors[0] if isinstance(errors[0], dict) else {}
        return first.get("reason") or error.get("status") or f"http_{response.status_code}"
    return str(error or f"http_{response.status_code}")


class YouTube:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.accounts_path = settings.data_dir / "youtube_accounts.json"
        self.db_path = settings.data_dir / "klipp.db"

    @property
    def configured(self) -> bool:
        return bool(self.settings.youtube_client_id and self.settings.youtube_client_secret)

    # ---------- kanaler och token ----------

    def accounts(self) -> dict[str, dict]:
        """Kopplade kanaler, kanal-id -> token och kanalinfo, i den ordning de lades till."""
        with _token_lock:
            try:
                accounts = json.loads(self.accounts_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return accounts if isinstance(accounts, dict) else {}

    def _save(self, accounts: dict[str, dict]) -> None:
        """Skriv en ny tmp-fil (rättigheter 600 från början) och byt sedan ut den gamla. Anropas under låset."""
        if not accounts:
            self.accounts_path.unlink(missing_ok=True)
            return
        self.accounts_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.accounts_path.parent, prefix=".youtube_accounts.",
                                         suffix=".tmp", delete=False, encoding="utf-8") as file:
            json.dump(accounts, file)
        os.chmod(file.name, 0o600)
        os.replace(file.name, self.accounts_path)

    def save_account(self, account: dict) -> None:
        """Lägg till kanalen, eller byt dess token om den redan finns. Platsen i listan består."""
        with _token_lock:
            accounts = self.accounts()
            accounts[account["channel_id"]] = account
            self._save(accounts)

    def disconnect(self, channel_id: str) -> None:
        """Återkalla tillgången hos Google och ta bort kanalen. Svarar inte Google tas den ändå bort."""
        with _token_lock:
            accounts = self.accounts()
            if channel_id not in accounts:  # bara en nyckel i filen, aldrig en sökväg
                raise YouTubeError("Kanalen är inte kopplad.", 404)
            account = accounts.pop(channel_id)
            with contextlib.suppress(httpx.HTTPError), http_client() as http:
                http.post(REVOKE_URL, data={"token": account.get("refresh_token") or account.get("access_token", "")})
            self._save(accounts)

    @staticmethod
    def connected(account: dict) -> bool:
        return bool(account.get("refresh_token")) and not account.get("needs_login")

    def choose(self, channel_id: str | None) -> str:
        """Kanalen som klippet ska till. Utan val: den enda kopplade kanalen."""
        connected = [key for key, account in self.accounts().items() if self.connected(account)]
        if channel_id is None:
            if not connected:
                raise YouTubeError("Ingen YouTube-kanal är kopplad. Koppla en under Inställningar.", 400)
            if len(connected) > 1:
                raise YouTubeError("Välj vilken YouTube-kanal klippet ska till.", 400)
            return connected[0]
        if channel_id not in connected:
            raise YouTubeError(f"Den YouTube-kanalen är inte kopplad. {RELOGIN}", 400)
        return channel_id

    def _token_request(self, http: httpx.Client, **form: str) -> dict:
        data = {"client_id": self.settings.youtube_client_id,
                "client_secret": self.settings.youtube_client_secret, **form}
        try:
            response = http.post(TOKEN_URL, data=data)
        except httpx.HTTPError as error:
            raise YouTubeError(UNREACHABLE) from error
        body = _json(response)
        if response.status_code == 200 and body.get("access_token"):
            return body
        if response.status_code == 200 or response.status_code >= 500:
            raise YouTubeError(UNREACHABLE)
        if body.get("error") == "invalid_grant":  # refresh_token har gått ut (7 dagar i läget Testing) eller dragits in
            raise YouTubeError(RELOGIN, 400)
        raise YouTubeError(f"Google godkände inte nycklarna ({_reason(response)}). Kontrollera YOUTUBE_CLIENT_ID "
                           "och YOUTUBE_CLIENT_SECRET under Inställningar.", 400)

    def _channel(self, http: httpx.Client, access: str) -> dict:
        try:
            response = http.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                                headers={"Authorization": f"Bearer {access}"})
        except httpx.HTTPError as error:
            raise YouTubeError(UNREACHABLE) from error
        if response.status_code >= 500:
            raise YouTubeError(UNREACHABLE)
        if response.status_code != 200:
            raise YouTubeError(f"YouTube visade inte kanalen ({_reason(response)}).", 400)
        items = _json(response).get("items") or []
        if not items:
            raise YouTubeError("Google-kontot har ingen YouTube-kanal. Skapa en kanal på YouTube först, "
                               "eller välj ett annat konto.", 400)
        return items[0]

    def connect(self, code: str, verifier: str) -> None:
        """Byt koden från inloggningen mot token, hämta kanalen och spara."""
        with http_client() as http:
            body = self._token_request(http, code=code, grant_type="authorization_code",
                                       redirect_uri=self.settings.youtube_redirect_uri, code_verifier=verifier)
            if not {UPLOAD_SCOPE, READ_SCOPE} <= set(body.get("scope", "").split()):
                raise YouTubeError("Appen fick inte båda rättigheterna. Logga in igen och kryssa i att appen får "
                                   "ladda upp videor och se ditt YouTube-konto.", 400)
            if not body.get("refresh_token"):
                raise YouTubeError("Google gav ingen refresh token. Försök igen.", 400)
            channel = self._channel(http, body["access_token"])
        snippet = channel.get("snippet") or {}
        self.save_account({
            "channel_id": channel["id"], "title": snippet.get("title"), "handle": snippet.get("customUrl"),
            "avatar_url": ((snippet.get("thumbnails") or {}).get("default") or {}).get("url"),
            "access_token": body["access_token"], "refresh_token": body["refresh_token"],
            "expires_at": _iso(_now() + timedelta(seconds=int(body.get("expires_in", 0)))),
        })

    def _access_token(self, http: httpx.Client, channel_id: str | None) -> str:
        """Giltig access token för kanalen. En som gått ut (eller går ut inom en minut) förnyas först."""
        if not self.configured:
            raise YouTubeError(NOT_CONFIGURED, 400)
        with _token_lock:
            accounts = self.accounts()  # läs inuti låset: ett annat anrop kan just ha förnyat token
            account = accounts.get(channel_id or "")
            if not account or not self.connected(account):
                raise YouTubeError(RELOGIN, 400)
            if datetime.fromisoformat(account["expires_at"]) - timedelta(seconds=60) <= _now():
                try:
                    fresh = self._token_request(http, grant_type="refresh_token",
                                                refresh_token=account["refresh_token"])
                except YouTubeError as error:
                    if str(error) == RELOGIN:  # webben visar "Logga in igen" för kanalen
                        accounts[channel_id] = {**account, "needs_login": True}
                        self._save(accounts)
                    raise
                account = {**account, "access_token": fresh["access_token"],  # refresh_token består
                           "expires_at": _iso(_now() + timedelta(seconds=int(fresh.get("expires_in", 0))))}
                accounts[channel_id] = account
                self._save(accounts)
            return account["access_token"]

    # ---------- uppladdningar ----------

    def _db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS youtube_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, channel_id TEXT, video_id TEXT,
            status TEXT, error TEXT, privacy TEXT, created TEXT, checked TEXT)""")
        return db

    def _set(self, upload_id: int, **fields) -> None:
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            db.execute(f"UPDATE youtube_uploads SET {sets} WHERE id = ?", (*fields.values(), upload_id))

    def _latest_row(self, clip_id: int, channel_id: str | None = None) -> dict | None:
        where, args = (("clip_id = ? AND channel_id = ?", (clip_id, channel_id)) if channel_id
                       else ("clip_id = ?", (clip_id,)))
        with self._db() as db:
            row = db.execute(f"SELECT * FROM youtube_uploads WHERE {where} ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def uploads_today(self) -> int:
        """Uppladdningar sedan kvoten började om. Varje påbörjad uppladdning kostar en av 100."""
        midnight = _now().astimezone(QUOTA_DAY).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM youtube_uploads WHERE created >= ?",
                              (_iso(midnight.astimezone(UTC)),)).fetchone()[0]

    @staticmethod
    def _check(video: dict, duration_s: float) -> dict:
        """Robins val, kontrollerade mot YouTubes regler. Svaret är metadata till videos.insert."""
        title = (video.get("title") or "").strip()
        if not title or len(title) > TITLE_MAX or "<" in title or ">" in title:
            raise YouTubeError(f"Titeln måste vara 1–{TITLE_MAX} tecken, utan < och >.", 400)
        description = video.get("description") or ""
        if len(description.encode("utf-8")) > DESCRIPTION_MAX or "<" in description or ">" in description:
            raise YouTubeError(f"Beskrivningen får vara högst {DESCRIPTION_MAX} byte, utan < och >.", 400)
        if video.get("privacy") not in PRIVACY:
            raise YouTubeError("Välj vem som får se videon.", 400)
        if not isinstance(video.get("made_for_kids"), bool):
            raise YouTubeError("Välj om videon är gjord för barn.", 400)
        if duration_s > SHORT_MAX_S:
            raise YouTubeError(f"Klippet är {math.ceil(duration_s)} sekunder. En YouTube Short får vara högst "
                               f"{SHORT_MAX_S} sekunder.", 400)
        return {"snippet": {"title": title, "description": description, "categoryId": CATEGORY},
                "status": {"privacyStatus": video["privacy"], "selfDeclaredMadeForKids": video["made_for_kids"],
                           "containsSyntheticMedia": False}}

    def _start(self, http: httpx.Client, access: str, size: int, metadata: dict, notify: bool) -> str | None:
        """Starta en resumable uppladdning. Svaret är adressen som filen ska skickas till,
        eller None när Google inte godkänner token (401)."""
        try:
            response = http.post(
                UPLOAD_URL, json=metadata,
                params={"uploadType": "resumable", "part": "snippet,status",
                        "notifySubscribers": "true" if notify else "false"},
                headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
        except httpx.HTTPError as error:
            raise YouTubeError(UNREACHABLE) from error
        if response.status_code == 200 and response.headers.get("location"):
            return response.headers["location"]
        reason = _reason(response)
        if reason in ERRORS:
            raise YouTubeError(*ERRORS[reason])
        if response.status_code == 401:
            return None
        if response.status_code == 200 or response.status_code >= 500:
            raise YouTubeError(UNREACHABLE)
        raise YouTubeError(f"YouTube tog inte emot videon ({reason}).")

    def _expire(self, channel_id: str, access: str) -> None:
        """Google godkände inte token fast den inte har gått ut: låt nästa anrop förnya den."""
        with _token_lock:
            accounts = self.accounts()
            if accounts.get(channel_id, {}).get("access_token") == access:  # inte redan förnyad av ett annat anrop
                accounts[channel_id]["expires_at"] = _iso(_now())
                self._save(accounts)

    def _put_file(self, http: httpx.Client, session: str, path: Path, size: int) -> httpx.Response:
        """Skicka filen. Tappas svaret (nätverksfel eller 5xx) frågas sessionen en gång om filen kom fram,
        så att ett nytt försök inte ger en dubblett på kanalen."""
        try:
            put = http.put(session, content=path.read_bytes(), headers={"Content-Type": "video/mp4"})
        except httpx.HTTPError:
            put = None
        if put is None or put.status_code >= 500:
            with contextlib.suppress(httpx.HTTPError):
                asked = http.put(session, content=b"", headers={"Content-Range": f"bytes */{size}"})
                if asked.status_code in (200, 201):
                    return asked
            if put is None:
                raise YouTubeError(UNREACHABLE)
        return put

    def upload(self, clip_id: int, path: Path, channel_id: str | None, video: dict, duration_s: float) -> dict:
        """Ladda upp filen som en Short till kanalen. Klippets status i klipploggen ändras inte."""
        metadata = self._check(video, duration_s)
        size = path.stat().st_size
        if size == 0:
            raise YouTubeError("Filen är tom.", 400)
        if not _upload_lock.acquire(blocking=False):
            raise YouTubeError(BUSY, 409)
        try:
            result = self._upload(clip_id, path, channel_id, video, metadata, size)
        finally:
            _upload_lock.release()
        add_post(self.settings, clip_id, "youtube_shorts", result["url"])  # bara när hjälpen för inskick är på
        return result

    def _upload(self, clip_id: int, path: Path, channel_id: str | None, video: dict, metadata: dict,
                size: int) -> dict:
        with http_client() as http:
            if not self.configured:
                raise YouTubeError(NOT_CONFIGURED, 400)
            channel_id = self.choose(channel_id)
            last = self._latest_row(clip_id, channel_id)  # samma klipp till en annan kanal är inget dubbelklick
            if last and last["status"] != "failed" \
                    and _now() - datetime.fromisoformat(last["created"]) < timedelta(seconds=RECENT_S):
                raise YouTubeError("Klippet skickades nyss till kanalen. Titta på YouTube.", 409)
            notify = video.get("notify_subscribers", True)
            access = self._access_token(http, channel_id)
            session = self._start(http, access, size, metadata, notify)
            if session is None:  # förnya och försök en gång till; invalid_grant ger RELOGIN och needs_login
                self._expire(channel_id, access)
                session = self._start(http, self._access_token(http, channel_id), size, metadata, notify)
            if session is None:
                raise YouTubeError(RELOGIN, 400)

            created = _iso(_now())
            with self._db() as db:
                upload_id = db.execute(
                    "INSERT INTO youtube_uploads (clip_id, channel_id, status, privacy, created) VALUES (?,?,?,?,?)",
                    (clip_id, channel_id, "uploading", video["privacy"], created)).lastrowid
            try:  # en Short är högst 3 minuter, så filen ryms i minnet
                put = self._put_file(http, session, path, size)
                if put.status_code not in (200, 201):
                    raise YouTubeError(f"YouTube tog inte emot filen (svar {put.status_code}).")
                video_id = _json(put).get("id")
                if not video_id:
                    raise YouTubeError(UNREACHABLE)
            except (YouTubeError, httpx.HTTPError, OSError) as error:
                if isinstance(error, YouTubeError):
                    message = str(error)
                elif isinstance(error, httpx.HTTPError):
                    message = UNREACHABLE
                else:
                    message = "Klippets fil gick inte att läsa."
                self._set(upload_id, status="failed", error=message)
                raise YouTubeError(message) from error
            self._set(upload_id, video_id=video_id, status="uploaded", checked=_iso(_now()))
        return {"clip_id": clip_id, "video_id": video_id, "url": f"https://www.youtube.com/shorts/{video_id}",
                "status": "uploaded", "channel_id": channel_id, "message": MESSAGE}

    def latest(self, clip_id: int) -> dict | None:
        """Senaste uppladdningen för klippet, med status från YouTube (sparas en minut)."""
        upload = self._latest_row(clip_id)
        if upload is None:
            return None
        checked = upload["checked"] and datetime.fromisoformat(upload["checked"])
        if upload["video_id"] and upload["status"] not in DONE and (
                not checked or (_now() - checked).total_seconds() >= STATUS_CACHE_S):
            try:
                with http_client() as http:
                    access = self._access_token(http, upload["channel_id"])
                    upload.update(self._fetch_status(http, access, upload["video_id"]))
                self._set(upload["id"], status=upload["status"], error=upload["error"], checked=_iso(_now()))
            except YouTubeError:  # YouTube svarar inte eller kanalen är inte kopplad: visa det som är sparat
                self._set(upload["id"], checked=_iso(_now()))  # och fråga inte igen förrän om en minut
        if upload["status"] == "uploading" \
                and _now() - datetime.fromisoformat(upload["created"]) >= timedelta(seconds=RECENT_S):
            upload.update(status="failed", error=ABORTED)  # servern dog mitt i uppladdningen
            self._set(upload["id"], status="failed", error=ABORTED)
        account = self.accounts().get(upload["channel_id"] or "") or {}
        video_id = upload["video_id"]
        return {"video_id": video_id, "url": f"https://www.youtube.com/shorts/{video_id}" if video_id else None,
                "status": upload["status"], "error": upload["error"], "privacy": upload["privacy"],
                "channel_id": upload["channel_id"], "title": account.get("title")}

    def _fetch_status(self, http: httpx.Client, access: str, video_id: str) -> dict:
        try:
            response = http.get(VIDEOS_URL, params={"part": "status,processingDetails", "id": video_id},
                                headers={"Authorization": f"Bearer {access}"})
        except httpx.HTTPError as error:
            raise YouTubeError(UNREACHABLE) from error
        if response.status_code != 200:
            raise YouTubeError(UNREACHABLE)
        items = _json(response).get("items") or []
        if not items:
            return {"status": "failed", "error": "Videon finns inte längre på YouTube."}
        status = items[0].get("status") or {}
        upload_status = status.get("uploadStatus")
        if upload_status == "processed":
            return {"status": "processed", "error": None}
        if upload_status == "rejected":
            return {"status": "failed",
                    "error": f"YouTube avvisade videon ({status.get('rejectionReason') or 'okänd orsak'})."}
        if upload_status == "failed":
            return {"status": "failed",
                    "error": f"YouTube kunde inte behandla videon ({status.get('failureReason') or 'okänt fel'})."}
        if upload_status == "deleted":
            return {"status": "failed", "error": "Videon är borttagen på YouTube."}
        return {"status": "uploaded", "error": None}

    def status(self) -> dict:
        """Läget för webben. Aldrig någon token eller nyckel."""
        accounts = [{"channel_id": key, "title": account.get("title"), "handle": account.get("handle"),
                     "avatar_url": account.get("avatar_url"), "connected": self.connected(account),
                     "needs_login": not self.connected(account)}
                    for key, account in self.accounts().items()]
        return {"configured": self.configured, "accounts": accounts, "uploads_today": self.uploads_today()}
