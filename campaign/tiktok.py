"""Koppling till Robins TikTok-konton: inloggning (Login Kit för desktop), token, uppladdning
som utkast till TikTok-inkorgen (scope video.upload) och direktpostning (scope video.publish).

Ett utkast har ingen caption: Robin trycker på notisen i TikTok-appen, klistrar in captionen och postar.
Direktpostning publicerar med beskrivning och de val Robin gjorde. Så länge appen inte är granskad
av TikTok går det bara som SELF_ONLY, och kontot måste vara privat.

Flera konton kan vara kopplade. Token sparas per konto (open_id) i klippdata/tiktok_accounts.json
med rättigheter 600. Token loggas aldrig och skickas aldrig till webben.
Uppladdningarna sparas i tabellen tiktok_uploads i klippdata/klipp.db, med kontots open_id.
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

import httpx

from .config import Settings
from .preferences import add_post

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_URL = "https://open.tiktokapis.com/v2/user/info/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
DIRECT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
CREATOR_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
# username i user/info kräver user.info.profile; vi tar det från creator_info i stället.
SCOPES = "user.info.basic,video.upload,video.publish"

# Delar: minst 5 MB och högst 64 MB per del, sista delen tar resten (högst 128 MB).
# 64_000_000 i stället för 64 MiB, så att gränsen håller oavsett hur TikTok räknar MB.
WHOLE_MAX = 64_000_000  # en fil upp till hit skickas i en del
CHUNK = 10_000_000  # delarnas storlek för större filer, som i TikToks exempel
PENDING_LIMIT = 5  # TikTok tar högst 5 väntande utkast per 24 timmar
STATUS_CACHE_S = 60  # status/fetch tillåter 30 anrop per minut; webben behöver inte färskare än en minut
RECENT_S = 600  # samma klipp igen inom 10 minuter är troligen ett dubbelklick
STUCK_S = 3600  # ett direktinlägg utan svar från TikTok på en timme räknas som misslyckat
STUCK = "TikTok har inte svarat om inlägget på en timme. Titta på profilen i TikTok."
TITLE_MAX = 2200  # UTF-16-enheter
DONE = ("failed", "publish_complete", "published")
MESSAGE = "Öppna TikTok-appen och tryck på notisen i inkorgen. Klistra in captionen och posta."
DIRECT_MESSAGE = "TikTok publicerar klippet. Det kan ta några minuter innan det syns på profilen."
NO_PUBLISH = "Kontot har inte rätt att publicera. Koppla kontot igen under Inställningar."
RECONNECT = "Koppla TikTok igen under Inställningar."
LEGACY_ID = "gammalt"  # nyckeln för en gammal token utan open_id
UNREACHABLE = "TikTok svarade inte. Försök igen om en stund."
# TikToks felkoder vid init och creator_info som Robin kan göra något åt.
ERRORS = {
    "spam_risk_too_many_posts": ("TikTok tar inte emot fler inlägg från kontot i dag. Försök igen i morgon.", 429),
    "spam_risk_user_banned_from_posting": ("TikTok har stängt av kontot från att posta.", 403),
    "reached_active_user_cap": (("Appen har nått TikToks gräns för hur många konton som postar per dygn. "
                                 "Försök igen i morgon."), 429),
    "unaudited_client_can_only_post_to_private_accounts": (
        # En ogranskad app får bara posta SELF_ONLY, och kontot måste vara privat.
        ("TikTok har inte granskat appen än. Då går det bara att publicera som Bara jag. Välj Bara jag och "
         "försök igen. Kontot måste också vara privat i TikTok."), 400),
    "privacy_level_option_mismatch": ("TikTok godtog inte valet av vem som får se klippet. Välj igen.", 400),
    "rate_limit_exceeded": ("TikTok tar bara emot några anrop i minuten. Vänta en minut.", 429),
}

# Läsa, förnya och spara token sker ett anrop i taget, så att samma refresh_token inte används två gånger.
# RLock: accounts() tar låset själv och anropas också inifrån låset.
_token_lock = threading.RLock()
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
        self.accounts_path = settings.data_dir / "tiktok_accounts.json"
        self.legacy_path = settings.data_dir / "tiktok_token.json"
        self.db_path = settings.data_dir / "klipp.db"

    @property
    def configured(self) -> bool:
        return bool(self.settings.tiktok_client_key and self.settings.tiktok_client_secret)

    # ---------- konton och token ----------

    def accounts(self) -> dict[str, dict]:
        """Kopplade konton, open_id -> token, i den ordning de lades till. Flyttar in en gammal token först."""
        with _token_lock:
            try:
                accounts = json.loads(self.accounts_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                accounts = {}
            if not isinstance(accounts, dict):
                accounts = {}
            return self._move_legacy(accounts)

    def _move_legacy(self, accounts: dict[str, dict]) -> dict[str, dict]:
        """Token från före flera konton (tiktok_token.json) flyttas in en gång. Uppladdningarna utan open_id
        kom från det kontot. En fil som inte går att läsa lämnas orörd."""
        if not self.legacy_path.exists():
            return accounts
        try:
            old = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return accounts
        if not isinstance(old, dict):
            return accounts
        key = old.get("open_id") or LEGACY_ID
        if key not in accounts:  # finns kontot redan är den gamla filen äldre
            accounts = {key: old, **accounts}
            self._save(accounts)
        with self._db() as db:
            db.execute("UPDATE tiktok_uploads SET open_id = ? WHERE open_id IS NULL", (key,))
        self.legacy_path.unlink()  # först när kontot är sparat
        return accounts

    def _save(self, accounts: dict[str, dict]) -> None:
        """Skriv en ny tmp-fil (rättigheter 600 från början) och byt sedan ut den gamla. Anropas under låset."""
        if not accounts:
            self.accounts_path.unlink(missing_ok=True)
            return
        self.accounts_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.accounts_path.parent, prefix=".tiktok_accounts.",
                                         suffix=".tmp", delete=False, encoding="utf-8") as file:
            json.dump(accounts, file)
        os.chmod(file.name, 0o600)
        os.replace(file.name, self.accounts_path)

    def save_token(self, token: dict) -> None:
        """Lägg till kontot, eller byt dess token om det redan finns (samma open_id). Platsen i listan består."""
        with _token_lock:
            accounts = self.accounts()
            accounts[token.get("open_id") or LEGACY_ID] = token
            self._save(accounts)

    def disconnect(self, open_id: str | None = None) -> None:
        """Återkalla tillgången hos TikTok och ta bort kontot, eller alla konton utan open_id.
        Svarar inte TikTok tas kontot ändå bort."""
        with _token_lock:
            accounts = self.accounts()
            if open_id is not None and open_id not in accounts:  # bara en nyckel i filen, aldrig en sökväg
                raise TikTokError("Kontot är inte kopplat.", 404)
            for key in [open_id] if open_id is not None else list(accounts):
                token = accounts.pop(key)
                if self.configured:
                    with contextlib.suppress(httpx.HTTPError), http_client() as http:
                        http.post(REVOKE_URL, data={"client_key": self.settings.tiktok_client_key,
                                                    "client_secret": self.settings.tiktok_client_secret,
                                                    "token": token.get("access_token", "")})
            self._save(accounts)

    @staticmethod
    def connected(token: dict) -> bool:
        try:
            return datetime.fromisoformat(token["refresh_expires_at"]) > _now()
        except (KeyError, TypeError, ValueError):
            return False

    def choose(self, open_id: str | None) -> str:
        """Kontot som klippet ska till. Utan val: det enda kopplade kontot."""
        connected = [key for key, token in self.accounts().items() if self.connected(token)]
        if open_id is None:
            if not connected:
                raise TikTokError(f"TikTok är inte kopplat. {RECONNECT}", 400)
            if len(connected) > 1:
                raise TikTokError("Välj vilket TikTok-konto klippet ska till.", 400)
            return connected[0]
        if open_id not in connected:
            raise TikTokError(f"Det TikTok-kontot är inte kopplat. {RECONNECT}", 400)
        return open_id

    def can_publish(self, open_id: str) -> bool:
        return "video.publish" in (self.accounts().get(open_id, {}).get("scopes") or [])

    def creator_info(self, open_id: str) -> dict:
        """Färsk creator_info för kontot, som postsidan visar. Användarnamnet sparas för länken till inlägget."""
        if open_id not in self.accounts():
            raise TikTokError("Kontot är inte kopplat.", 404)
        if not self.can_publish(open_id):
            raise TikTokError(NO_PUBLISH, 400)
        with http_client() as http:
            data = self._creator_info(http, self._access_token(http, open_id), open_id)
        return {"open_id": open_id, "nickname": data.get("creator_nickname"),
                "username": data.get("creator_username"), "avatar_url": data.get("creator_avatar_url"),
                "privacy_level_options": data.get("privacy_level_options") or [],
                **{key: bool(data.get(key)) for key in ("comment_disabled", "duet_disabled", "stitch_disabled")},
                "max_video_post_duration_sec": data.get("max_video_post_duration_sec")}

    def _creator_info(self, http: httpx.Client, access: str, open_id: str) -> dict:
        data = self._post(http, CREATOR_URL, access, {}, "läste inte kontot")
        username = data.get("creator_username")
        with _token_lock:
            accounts = self.accounts()
            if username and open_id in accounts and accounts[open_id].get("username") != username:
                accounts[open_id]["username"] = username
                self._save(accounts)
        return data

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
        self.save_token(token)

    def _access_token(self, http: httpx.Client, open_id: str | None) -> str:
        """Giltig access token för kontot. En som gått ut (eller går ut inom en minut) förnyas först."""
        if not self.configured:
            raise TikTokError("TIKTOK_CLIENT_KEY och TIKTOK_CLIENT_SECRET saknas i .env.", 400)
        with _token_lock:
            accounts = self.accounts()  # läs inuti låset: ett annat anrop kan just ha förnyat token
            token = accounts.get(open_id or "")
            if not token or not self.connected(token):
                raise TikTokError(f"TikTok är inte kopplat. {RECONNECT}", 400)
            if datetime.fromisoformat(token["expires_at"]) - timedelta(seconds=60) <= _now():
                fresh = self._token_request(http, grant_type="refresh_token",
                                            refresh_token=token["refresh_token"])
                accounts[open_id] = token = {**token, **fresh}  # namn och bild finns kvar
                self._save(accounts)
            return token["access_token"]

    # ---------- uppladdningar ----------

    def _db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS tiktok_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, publish_id TEXT,
            status TEXT, error TEXT, created TEXT, checked TEXT, open_id TEXT,
            mode TEXT DEFAULT 'draft', privacy_level TEXT, post_id TEXT)""")
        have = {row[1] for row in db.execute("PRAGMA table_info(tiktok_uploads)")}
        # Tabellen från före flera konton och direktpostning. Gamla rader får NULL, och mode blir draft.
        for column, kind in (("open_id", "TEXT"), ("mode", "TEXT DEFAULT 'draft'"), ("privacy_level", "TEXT"),
                             ("post_id", "TEXT"), ("title", "TEXT")):  # title: regeln om olika text per konto
            if column not in have:
                try:
                    db.execute(f"ALTER TABLE tiktok_uploads ADD COLUMN {column} {kind}")
                except sqlite3.OperationalError as error:
                    if "duplicate column" not in str(error):  # en annan tråd hann före
                        raise
        return db

    def _pending_rows(self, open_id: str) -> list[sqlite3.Row]:
        """Kontots utkast som väntar. TikTok räknar gränsen per användare. Direktpostningar räknas inte."""
        since = _iso(_now() - timedelta(hours=24))
        with self._db() as db:
            return db.execute(
                f"SELECT id, publish_id FROM tiktok_uploads WHERE open_id = ? AND mode = 'draft' AND created > ? "
                f"AND status NOT IN {DONE}", (open_id, since)).fetchall()

    def pending_24h(self, open_id: str) -> int:
        return len(self._pending_rows(open_id))

    def _set(self, upload_id: int, **fields) -> None:
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            db.execute(f"UPDATE tiktok_uploads SET {sets} WHERE id = ?", (*fields.values(), upload_id))

    def _latest_row(self, clip_id: int, open_id: str | None = None, mode: str | None = None) -> dict | None:
        """Senaste uppladdningen av klippet, till ett visst konto och i ett visst läge, eller vilken som helst."""
        where, args = ("clip_id = ? AND open_id = ? AND mode = ?", (clip_id, open_id, mode)) if open_id \
            else ("clip_id = ?", (clip_id,))
        with self._db() as db:
            row = db.execute(f"SELECT * FROM tiktok_uploads WHERE {where} ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def _refresh_pending(self, http: httpx.Client, access: str, open_id: str) -> None:
        """Färsk status för kontots väntande utkast. Robin kan ha postat eller tagit bort dem i appen."""
        for row in self._pending_rows(open_id):
            fresh = self._fetch_status(http, access, row["publish_id"])
            self._set(row["id"], **fresh, checked=_iso(_now()))

    def _post(self, http: httpx.Client, url: str, access: str, body: dict, refused: str) -> dict:
        """POST till Content Posting API. TikToks felkoder blir svensk text; svaret är data."""
        try:
            response = http.post(url, json=body, headers={"Authorization": f"Bearer {access}",
                                                          "Content-Type": "application/json; charset=UTF-8"})
        except httpx.HTTPError as error:
            raise TikTokError(UNREACHABLE) from error
        code = _error_code(response)  # creator_info svarar 200 med spam_risk-koderna
        if code in ERRORS:
            raise TikTokError(*ERRORS[code])
        if code.startswith("spam_risk"):
            raise TikTokError(f"TikTok tar inte emot fler utkast just nu ({code}). Posta eller ta bort "
                              "de som väntar i inkorgen, eller vänta ett dygn.", 429)
        if code in ("access_token_invalid", "scope_not_authorized"):
            raise TikTokError(f"TikTok godkände inte inloggningen ({code}). {RECONNECT}", 400)
        if response.status_code >= 500:
            raise TikTokError(UNREACHABLE)
        if response.status_code != 200 or code != "ok":
            raise TikTokError(f"TikTok {refused} ({code}).")
        data = _data(response)
        if not data:
            raise TikTokError(UNREACHABLE)
        return data

    def _init(self, http: httpx.Client, access: str, url: str, body: dict) -> dict:
        data = self._post(http, url, access, body, "tog inte emot uppladdningen")
        if not data.get("publish_id") or not data.get("upload_url"):
            raise TikTokError(UNREACHABLE)
        return data

    def _post_info(self, http: httpx.Client, access: str, open_id: str, post: dict, duration_s: float) -> dict:
        """Kontrollera Robins val mot färsk creator_info och bygg post_info till TikTok."""
        info = self._creator_info(http, access, open_id)
        privacy = post.get("privacy_level")
        if privacy not in (info.get("privacy_level_options") or []):
            raise TikTokError("Välj vem som får se klippet.", 400)
        if post.get("brand_content") and privacy == "SELF_ONLY":
            raise TikTokError("Branded content visibility cannot be set to private.", 400)
        longest = info.get("max_video_post_duration_sec")
        if longest and duration_s > longest:
            raise TikTokError(f"Klippet är {math.ceil(duration_s)} sekunder, men kontot får posta högst {longest} "
                              "sekunder.", 400)
        title = post.get("title") or ""
        return {**({"title": title} if title else {}), "privacy_level": privacy,
                **{f"disable_{what}": not post.get(f"allow_{what}") or bool(info.get(f"{what}_disabled"))
                   for what in ("comment", "duet", "stitch")},
                "brand_organic_toggle": bool(post.get("brand_organic")),
                "brand_content_toggle": bool(post.get("brand_content"))}

    def upload(self, clip_id: int, path: Path, open_id: str | None = None, post: dict | None = None,
               duration_s: float = 0) -> dict:
        """Ladda upp filen som utkast till kontot, eller publicera direkt när post (Robins val) finns.
        Klippets status i klipploggen ändras inte."""
        size = path.stat().st_size
        if size == 0:
            raise TikTokError("Filen är tom.", 400)
        mode = "draft" if post is None else "direct"
        with _upload_lock, http_client() as http:
            if not self.configured:
                raise TikTokError("TIKTOK_CLIENT_KEY och TIKTOK_CLIENT_SECRET saknas i .env.", 400)
            open_id = self.choose(open_id)
            # Samma klipp till ett annat konto, eller i det andra läget, är inget dubbelklick.
            last = self._latest_row(clip_id, open_id, mode)
            # En rad som står kvar som uploading (servern dog mitt i) spärrar bara i 10 minuter.
            if last and last["status"] in ("uploading", "uploaded", "processing", "published") \
                    and _now() - datetime.fromisoformat(last["created"]) < timedelta(seconds=RECENT_S):
                raise TikTokError("Klippet skickades nyss. Titta i TikTok-inkorgen." if last["mode"] == "draft"
                                  else "Klippet skickades nyss. Titta på profilen i TikTok.", 409)
            if post is not None:
                if not self.can_publish(open_id):
                    raise TikTokError(NO_PUBLISH, 400)
                if len((post.get("title") or "").encode("utf-16-le")) // 2 > TITLE_MAX:
                    raise TikTokError(f"Beskrivningen är för lång. TikTok tar högst {TITLE_MAX} tecken.", 400)
            access = self._access_token(http, open_id)
            if post is None and self.pending_24h(open_id) >= PENDING_LIMIT:
                self._refresh_pending(http, access, open_id)  # bara vid gränsen, så att status/fetch sparas
            if post is None and self.pending_24h(open_id) >= PENDING_LIMIT:
                raise TikTokError(f"{PENDING_LIMIT} utkast väntar redan i TikTok-inkorgen. Posta eller ta "
                                  "bort dem i TikTok-appen, eller vänta ett dygn.", 429)
            chunk_size, parts = chunks(size)
            source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
                      "total_chunk_count": len(parts)}
            if post is None:
                data = self._init(http, access, INIT_URL, {"source_info": source})
            else:
                post_info = self._post_info(http, access, open_id, post, duration_s)
                data = self._init(http, access, DIRECT_URL, {"post_info": post_info, "source_info": source})

            created = _iso(_now())
            with self._db() as db:
                upload_id = db.execute(
                    "INSERT INTO tiktok_uploads (clip_id, publish_id, status, created, open_id, mode, privacy_level, "
                    "title) VALUES (?,?,?,?,?,?,?,?)", (clip_id, data["publish_id"], "uploading", created, open_id,
                                                        mode, post and post.get("privacy_level"),
                                                        post and post.get("title"))).lastrowid
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
            status = "uploaded" if post is None else "processing"
            self._set(upload_id, status=status)
        if post is None:
            return {"clip_id": clip_id, "publish_id": data["publish_id"], "status": status,
                    "created": created, "message": MESSAGE, "open_id": open_id}
        return {"clip_id": clip_id, "publish_id": data["publish_id"], "status": status, "mode": mode,
                "created": created, "open_id": open_id, "message": DIRECT_MESSAGE}

    def latest(self, clip_id: int) -> dict | None:
        """Senaste uppladdningen för klippet, med status från TikTok (sparas en minut)."""
        upload = self._latest_row(clip_id)
        if upload is None:
            return None
        checked = upload["checked"] and datetime.fromisoformat(upload["checked"])
        seen = upload["checked"] or upload["created"]  # senast appen såg inlägget opublicerat
        fetched = False
        if upload["status"] not in DONE and (not checked or (_now() - checked).total_seconds() >= STATUS_CACHE_S):
            try:
                with http_client() as http:
                    access = self._access_token(http, upload["open_id"])
                    upload.update(self._fetch_status(http, access, upload["publish_id"], upload["mode"] == "direct",
                                                    upload["privacy_level"] == "PUBLIC_TO_EVERYONE"))
                self._set(upload["id"], status=upload["status"], error=upload["error"], post_id=upload["post_id"],
                          checked=_iso(_now()))
                fetched = True
            except TikTokError:
                pass  # TikTok svarar inte eller är inte kopplat: visa det som är sparat
            if upload["mode"] == "direct" and upload["status"] in ("uploading", "processing") \
                    and _now() - datetime.fromisoformat(upload["created"]) >= timedelta(seconds=STUCK_S):
                upload.update(status="failed", error=STUCK)  # annars frågar webben för alltid
                self._set(upload["id"], status="failed", error=STUCK, checked=_iso(_now()))
        account = self.accounts().get(upload["open_id"] or "") or {}
        username = account.get("username")
        post_url = f"https://www.tiktok.com/@{username}/video/{upload['post_id']}" \
            if upload["post_id"] and username else None
        if fetched and upload["status"] == "published" and post_url:  # just blev publicerat: en gång
            add_post(self.settings, clip_id, "tiktok", post_url, created=seen, account=upload["open_id"])  # tiden blir hellre för kort
        return {**{key: upload[key] for key in ("clip_id", "publish_id", "status", "error", "created", "open_id",
                                                "mode", "privacy_level", "post_id")},
                "display_name": account.get("display_name"),
                "post_url": post_url}

    def _fetch_status(self, http: httpx.Client, access: str, publish_id: str, direct: bool = False,
                      public: bool = False) -> dict:
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
        if direct and status != "failed":
            post_ids = data.get("publicaly_available_post_id") or []  # bara för offentliga inlägg
            # Ett offentligt inlägg får sitt id först efter TikToks granskning: tills dess frågar appen igen.
            done = status == "publish_complete" and (bool(post_ids) or not public)
            return {"status": "published" if done else "processing", "error": None,
                    "post_id": str(post_ids[0]) if post_ids else None}
        if status == "send_to_user_inbox":
            return {"status": "uploaded", "error": None}
        if status == "failed":
            return {"status": "failed",
                    "error": f"TikTok kunde inte ta emot klippet ({data.get('fail_reason') or 'okänt fel'})."}
        return {"status": status, "error": None}

    def status(self) -> dict:
        """Läget för webben. Aldrig någon token eller nyckel."""
        accounts = [{"open_id": key, "display_name": token.get("display_name"),
                     "avatar_url": token.get("avatar_url"), "scopes": token.get("scopes") or [],
                     "connected": self.connected(token), "refresh_expires_at": token.get("refresh_expires_at"),
                     "pending_24h": self.pending_24h(key),
                     "can_publish": "video.publish" in (token.get("scopes") or [])}
                    for key, token in self.accounts().items()]
        # De gamla fälten för en äldre webb: det första kontot som är kopplat.
        first = next((account for account in accounts if account["connected"]), None)
        return {
            "configured": self.configured,
            "pending_limit": PENDING_LIMIT,
            "accounts": accounts,
            "connected": first is not None,
            **{key: first[key] if first else None
               for key in ("display_name", "avatar_url", "refresh_expires_at")},
            "scopes": first["scopes"] if first else [],
            "pending_24h": first["pending_24h"] if first else 0,
        }
