"""MCP-server så att Claude Code kan styra klippappen: samma saker som Robin gör i webben.

Ett tunt lager över appens API på :8000, samma vägar som webben använder. Ingen egen logik och ingen
databas här, så att appens regler, lås och kontroller gäller precis som i webben.

Start (stdio): .venv/bin/python -m campaign.mcp_server. Registreras för Claude Code i .mcp.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from dotenv import dotenv_values
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
OVERLAY_MAX = 10 * 1024 * 1024
logging.getLogger("httpx").setLevel(logging.WARNING)  # en loggrad per anrop stör bara

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
REPLACE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
PAID = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
OUT = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

server = MCPServer(
    name="klipp",
    instructions=(
        "Styr Robins klippapp (kampanjklara klipp av Twitch-streams). Appen måste köra på :8000. "
        "Verktyg vars beskrivning börjar med 'Publicerar utåt' eller 'Kostar pengar' får bara användas när "
        "Robin har sagt ja. Ett klipp får bara postas när regelkontrollen är grön och Robin har godkänt det. "
        "Fel kommer tillbaka med appens svenska text; visa den för Robin."
    ),
)


def http_client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=600.0)  # en uppladdning till YouTube kan ta minuter


def api_key() -> str | None:
    """Appens nyckel (CLIPSMITH_API_KEY), färskt varje gång. Inget annat ur .env läses. Skrivs aldrig ut."""
    return os.environ.get("CLIPSMITH_API_KEY") or dotenv_values(ROOT / ".env").get("CLIPSMITH_API_KEY") or None


def _seg(value: str) -> str:
    """En text som en del av adressen. Kodas helt, så att / ? # och .. aldrig når en annan väg."""
    if value in ("", ".", ".."):
        raise ToolError(f"Ogiltigt id: {value!r}")
    return quote(value, safe="")


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return response.text[:500] or f"svar {response.status_code}"
    if isinstance(detail, list):  # FastAPI:s valideringsfel
        return "; ".join(f"{(item.get('loc') or ['?'])[-1]}: {item.get('msg')}" for item in detail
                         if isinstance(item, dict))
    return str(detail)


def _send(method: str, path: str, body: dict | None = None, params: dict | None = None,
          content: bytes | None = None, content_type: str | None = None) -> str:
    """Ett anrop till appen. Svaret som JSON-text; ett fel blir ToolError med appens svenska text."""
    headers = {}
    key = api_key()
    if key:
        headers["X-Api-Key"] = key
    if content_type:
        headers["Content-Type"] = content_type
    if body is not None:
        body = {name: value for name, value in body.items() if value is not None}  # tomt = appens förval
    if params is not None:
        params = {name: value for name, value in params.items() if value is not None}
    try:
        with http_client() as http:
            response = http.request(method, path, json=body, params=params or None, content=content,
                                    headers=headers)
    except httpx.TimeoutException as error:
        raise ToolError("Appen svarade inte inom 10 minuter. Anropet kan ha gått igenom. Kolla upload_status eller "
                        "list_schedule innan du försöker igen.") from error
    except httpx.HTTPError as error:
        raise ToolError(f"Appen svarar inte på {BASE_URL}. Starta den med clipsmith serve och försök igen.") \
            from error
    if response.status_code >= 400:
        raise ToolError(f"Appen svarade {response.status_code}: {_detail(response)}")
    if not response.content:
        return json.dumps({"ok": True})
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except ValueError:
        return response.text


# ---------- översikt ----------


@server.tool(annotations=READ)
def summary() -> str:
    """Översikten: budget för Claude, antal klipp per status, att göra, disken och intjänat. Börja här."""
    return _send("GET", "/campaign/summary")


@server.tool(annotations=READ)
def check_setup() -> str:
    """Kontrollen från Inställningar: ffmpeg, nycklar (bara om de finns), kampanjer, budget, SSD och utrymme."""
    return _send("GET", "/campaign/setup")


@server.tool(annotations=READ)
def account_status() -> str:
    """Kopplade YouTube-kanaler och TikTok-konton, utan hemligheter: id, namn, om de kan publicera,
    dagens uppladdningar. Använd före uppladdning för att välja channel_id eller open_id."""
    return json.dumps({"youtube": json.loads(_send("GET", "/campaign/youtube/status")),
                       "tiktok": json.loads(_send("GET", "/campaign/tiktok/status"))}, ensure_ascii=False)


@server.tool(annotations=READ)
def tiktok_creator_info(open_id: str) -> str:
    """Ett TikTok-kontos val inför direktpostning: privacy_level_options, längsta klipp, avstängda kommentarer."""
    return _send("GET", f"/campaign/tiktok/accounts/{_seg(open_id)}/creator_info")


# ---------- streams och projekt ----------


@server.tool(annotations=READ)
def list_streams() -> str:
    """De senaste streamsen för de aktiva kampanjernas kanaler, med tips om vilka som är bra att klippa."""
    return _send("GET", "/campaign/streams")


@server.tool(annotations=READ)
def vod_info(vod_id: str) -> str:
    """Fakta om en Twitch-VOD innan en körning: kanal, titel, längd, om den är live och om disken räcker."""
    return _send("GET", f"/campaign/vod/{_seg(vod_id)}")


@server.tool(annotations=READ)
def list_projects() -> str:
    """Projekten (ett per stream och kampanj), nyast först, med status och antal klipp."""
    return _send("GET", "/campaign/runs")


@server.tool(annotations=READ)
def get_project(run_id: int) -> str:
    """En körning med förloppet per steg (hämta, transkribera, välja, klippa, kampanjklart) och kostnaden."""
    return _send("GET", f"/campaign/runs/{run_id}")


@server.tool(annotations=PAID)
def start_run(link: str, profile: str, game: str | None = None, start_s: float | None = None,
              end_s: float | None = None, focus: str | None = None, min_s: int | None = None,
              max_s: int | None = None) -> str:
    """Kostar pengar (Twitch-hämtning, transkribering och Claude). Fråga Robin först.
    Starta en körning för en kampanj från en Twitch-länk (twitch.tv/videos/<id>) eller ett VOD-id.
    profile är kampanjens id från list_campaigns. start_s/end_s begränsar delen av streamen, focus är en kort
    instruktion om vad som ska letas efter, min_s/max_s klippens längd. Bara en körning åt gången."""
    found = re.fullmatch(r"\s*(\d+)\s*", link) or re.search(r"twitch\.tv/videos/(\d+)", link)
    if not found:
        raise ToolError("Länken är inte en Twitch-VOD. Ge en länk som twitch.tv/videos/123456789 eller bara id:t.")
    return _send("POST", "/campaign/runs", {"profile": profile, "vod_id": found.group(1), "game": game,
                                            "start_s": start_s, "end_s": end_s, "focus": focus,
                                            "min_s": min_s, "max_s": max_s})


@server.tool(annotations=PAID)
def retry_run(run_id: int) -> str:
    """Kostar pengar (Twitch-hämtning, transkribering och Claude). Fråga Robin först.
    Kör en misslyckad körning igen med samma val (knappen Kör igen). Klara steg hoppas över."""
    return _send("POST", f"/campaign/runs/{run_id}/retry")


@server.tool(annotations=PAID)
def finish_run(run_id: int) -> str:
    """Kostar pengar (bildgranskning med Claude). Fråga Robin först.
    Gör projektets klipp kampanjklara igen: overlay, text, caption och regelkontroll. Använd efter att
    kampanjens profil har ändrats."""
    return _send("POST", f"/campaign/runs/{run_id}/finish")


# ---------- klipp ----------


@server.tool(annotations=READ)
def list_clips(status: str | None = None, vod_id: str | None = None, run_id: int | None = None) -> str:
    """Klipp i klipploggen, med caption, problem, betyg och inlägg. Filtrera på status (renderad, underkand,
    godkand, nekad, postad, inskickad, analytics_inskickad, betald), VOD eller projekt (run_id)."""
    return _send("GET", "/campaign/clips", params={"status": status, "vod_id": vod_id, "run_id": run_id})


@server.tool(annotations=READ)
def clip_transcript(clip_id: int) -> str:
    """Vad som sägs i klippet, med tider. Använd för att skriva en sann rubrik eller caption."""
    return _send("GET", f"/campaign/clips/{clip_id}/transcript")


@server.tool(annotations=READ)
def clip_edit_info(clip_id: int) -> str:
    """Kan klippet trimmas, och hur är det trimmat nu (gränserna för in_s och out_s)."""
    return _send("GET", f"/campaign/clips/{clip_id}/edit")


@server.tool(annotations=READ)
def upload_status(clip_id: int) -> str:
    """Senaste uppladdningen av klippet till YouTube och till TikTok, med status och länk."""
    return json.dumps({"youtube": json.loads(_send("GET", f"/campaign/clips/{clip_id}/youtube")),
                       "tiktok": json.loads(_send("GET", f"/campaign/clips/{clip_id}/tiktok"))}, ensure_ascii=False)


@server.tool(annotations=WRITE)
def update_clip(clip_id: int, status: Literal["renderad", "godkand", "nekad"] | None = None,
                caption: str | None = None, title: str | None = None, clip_format: str | None = None,
                note: str | None = None) -> str:
    """Att godkänna (godkand) kräver Robins ja.
    Godkänn (godkand) eller neka (nekad) ett klipp, eller ändra caption, rubrik, format eller anteckning. Captionen kontrolleras igen mot kampanjens regler; problemen syns i svaret.
    Rubriken på bilden ändras med edit_clip."""
    return _send("PATCH", f"/campaign/clips/{clip_id}", {"status": status, "caption": caption, "title": title,
                                                         "format": clip_format, "note": note})


@server.tool(annotations=REPLACE)
def edit_clip(clip_id: int, in_s: float | None = None, out_s: float | None = None, title: str | None = None) -> str:
    """Trimma klippet (in_s/out_s inom råklippet, se clip_edit_info) och byt rubriken på bilden. Klippet görs om
    med ffmpeg. Går inte medan en körning pågår."""
    return _send("POST", f"/campaign/clips/{clip_id}/edit", {"in_s": in_s, "out_s": out_s, "title": title})


# ---------- kampanjer ----------


@server.tool(annotations=READ)
def list_campaigns() -> str:
    """Kampanjerna (profilerna) med kanal, om de är aktiva och om de är redo för en körning."""
    return _send("GET", "/campaign/profiles")


@server.tool(annotations=READ)
def get_campaign(profile_id: str) -> str:
    """En kampanj i detalj: regler, format, caption, betalning och länkar."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}")


@server.tool(annotations=READ)
def get_campaign_yaml(profile_id: str) -> str:
    """Kampanjens profil som YAML-text, med kommentarer. Läs den innan save_campaign_yaml."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}/yaml")


@server.tool(annotations=READ)
def get_reframe(profile_id: str) -> str:
    """Kampanjens 9:16-beskärning: rutorna för kameran och spelet och delningen."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}/reframe")


@server.tool(annotations=WRITE)
def create_campaign(channel: str, streamer: str, fields: dict | None = None) -> str:
    """Skapa en kampanj som sidan Kampanjer gör. channel är Twitch-kanalen, streamer namnet. fields kan ha:
    campaign, language, clip_min_s, clip_max_s, on_screen_text, text_position (top|bottom), overlay_required,
    overlay_mode (full|top|bottom), facecam_required, must_include, hashtags, caption_template,
    formats [{name, description}], cpm_usd, min_views, max_payout_per_clip_usd, engagement_min_percent."""
    return _send("POST", "/campaign/profiles", {**(fields or {}), "channel": channel, "streamer": streamer})


@server.tool(annotations=REPLACE)
def save_campaign_yaml(profile_id: str, yaml: str) -> str:
    """Spara kampanjens hela profil som YAML-text (ersätter filen). Appen kontrollerar texten först."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/yaml", {"yaml": yaml})


@server.tool(annotations=WRITE)
def set_campaign_active(profile_id: str, active: bool) -> str:
    """Slå på eller stäng av en kampanj i appen. Avstängd döljer dess streams, projekt och klipp."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/active", {"active": active})


@server.tool(annotations=REPLACE)
def upload_overlay(profile_id: str, png_path: str) -> str:
    """Byt kampanjens overlay mot en PNG-fil på Macen (sökväg). Ersätter den gamla."""
    path = Path(png_path).expanduser()
    try:
        if path.stat().st_size > OVERLAY_MAX:
            raise ToolError(f"Filen är större än 10 MB: {png_path}")
        data = path.read_bytes()
    except OSError as error:
        raise ToolError(f"Filen går inte att läsa: {png_path}") from error
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/overlay", content=data, content_type="image/png")


@server.tool(annotations=REPLACE)
def save_reframe(profile_id: str, webcam_rects: list[dict], gameplay_rect: dict, split_ratio: float) -> str:
    """Spara kampanjens 9:16-beskärning. Rutor som {x, y, w, h}; läs get_reframe först."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/reframe",
                 {"webcam_rects": webcam_rects, "gameplay_rect": gameplay_rect, "split_ratio": split_ratio})


# ---------- bevakningen ----------


@server.tool(annotations=READ)
def list_scout() -> str:
    """Nya kampanjer från bevakningen (ClippingAlpha), med betyg 0–100 och varför, plus de bortsorterade."""
    return _send("GET", "/campaign/scout")


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
def refresh_scout() -> str:
    """Kontrollera nu: hämta nya kampanjer från ClippingAlpha direkt (räknas mot dygnsgränsen)."""
    return _send("POST", "/campaign/scout/refresh")


@server.tool(annotations=WRITE)
def dismiss_scout(ref: str, dismissed: bool = True) -> str:
    """Dölj en kampanj i bevakningen (ref som 'varumarke/serie'), eller visa den igen med dismissed=false."""
    return _send("POST", f"/campaign/scout/{_seg(ref)}/{'dismiss' if dismissed else 'undismiss'}")


# ---------- schemat ----------


@server.tool(annotations=READ)
def list_schedule() -> str:
    """Schemalagda uppladdningar: väntande först, sedan de 20 senast avslutade med status och fel."""
    return _send("GET", "/campaign/schedule")


@server.tool(annotations=READ)
def clip_accounts(clip_id: int, platform: Literal["tiktok", "youtube"]) -> str:
    """Kampanjens konton för ett klipp på plattformen: där klippet är postat, schemalagt eller ledigt (state).
    multi är sant när kampanjen postar samma klipp på flera konton: högst 5, med en egen text på varje."""
    return _send("GET", f"/campaign/clips/{clip_id}/accounts", params={"platform": platform})


@server.tool(annotations=READ)
def version_options(clip_id: int, platform: Literal["tiktok", "youtube"], account: str) -> str:
    """Valen för en ny version av klippet till ett annat konto: om det går (allowed, reason), fonter,
    klistermärken, overlays, hur mycket som får klippas i början och kontots fasta stil (defaults)."""
    return _send("GET", f"/campaign/clips/{clip_id}/versions/options", params={"platform": platform, "account": account})


@server.tool(annotations=WRITE)
def make_version(clip_id: int, platform: Literal["tiktok", "youtube"], account: str, font: str | None = None,
                 start_cut_s: float | None = None, overlay: str | None = None, sticker: dict | None = None,
                 mirror: bool | None = None, speed: float | None = None, filter: str | None = None,
                 brightness: float | None = None, saturation: float | None = None) -> str:
    """Gör en ny version av ett godkänt klipp för ett annat konto i kampanjen: annan font, klipp i början
    (start_cut_s, 0–5 s), egen overlay och klistermärke ({file, position, start_s, duration_s}), spegling,
    hastighet (speeds i version_options), färgfilter (filters), ljusstyrka (-0,3–0,3) och mättnad (-0,5–0,5).
    Versionen blir ett eget klipp som Robin godkänner. Inget publiceras. Läs version_options först."""
    return _send("POST", f"/campaign/clips/{clip_id}/versions",
                 {"platform": platform, "account": account, "font": font, "start_cut_s": start_cut_s,
                  "overlay": overlay, "sticker": sticker, "mirror": mirror, "speed": speed, "filter": filter,
                  "brightness": brightness, "saturation": saturation})


@server.tool(annotations=PAID)
def suggest_variant(clip_id: int, platform: Literal["tiktok", "youtube"], account: str) -> str:
    """Kostar pengar (Claude). Fråga Robin först.
    Föreslå en ny rubrik och caption för klippet på ett annat konto (open_id eller channel_id), olik texterna som
    klippet redan har på plattformen. Inget postas."""
    return _send("POST", f"/campaign/clips/{clip_id}/variant", {"platform": platform, "account": account})


@server.tool(annotations=OUT)
def schedule_upload(clip_id: int, platform: Literal["youtube", "tiktok"], at: str, fields: dict,
                    account: str | None = None) -> str:
    """Publicerar utåt. Fråga Robin först.
    Schemalägg uppladdningen av ett godkänt klipp. at är ISO-tid med tidszon (svensk tid: +01:00 eller +02:00).
    account är YouTubes channel_id eller TikToks open_id. fields som i upload_youtube (title, description, privacy,
    made_for_kids, notify_subscribers) eller send_tiktok (title, privacy_level, allow_*, brand_*). TikTok utan
    privacy_level blir en påminnelse i stället för en post."""
    return _send("POST", f"/campaign/clips/{clip_id}/schedule",
                 {"platform": platform, "at": at, "account": account, "fields": fields})


@server.tool(annotations=WRITE)
def cancel_schedule(post_id: int) -> str:
    """Avbryt en väntande schemalagd uppladdning."""
    return _send("DELETE", f"/campaign/schedule/{post_id}")


# ---------- uppladdning och inlägg ----------


@server.tool(annotations=OUT)
def upload_youtube(clip_id: int, title: str, privacy: Literal["public", "unlisted", "private"],
                   made_for_kids: bool, description: str | None = None, channel_id: str | None = None,
                   notify_subscribers: bool = True) -> str:
    """Publicerar utåt. Fråga Robin först.
    Ladda upp ett godkänt klipp som YouTube Short. description är captionen. channel_id behövs när flera kanaler
    är kopplade (account_status)."""
    return _send("POST", f"/campaign/clips/{clip_id}/youtube",
                 {"title": title, "privacy": privacy, "made_for_kids": made_for_kids, "description": description,
                  "channel_id": channel_id, "notify_subscribers": notify_subscribers})


@server.tool(annotations=OUT)
def send_tiktok(clip_id: int, mode: Literal["draft", "direct"] = "draft", open_id: str | None = None,
                title: str | None = None, privacy_level: str | None = None, allow_comment: bool | None = None,
                allow_duet: bool | None = None, allow_stitch: bool | None = None,
                brand_organic: bool | None = None, brand_content: bool | None = None,
                cover_s: float | None = None) -> str:
    """Publicerar utåt. Fråga Robin först.
    Skicka ett godkänt klipp till TikTok: som utkast i inkorgen (draft) eller direkt postat (direct, kräver
    privacy_level från tiktok_creator_info). title är captionen vid direktpostning. cover_s är omslaget,
    sekunder in i klippet (bara direct)."""
    return _send("POST", f"/campaign/clips/{clip_id}/tiktok",
                 {"mode": mode, "open_id": open_id, "title": title, "privacy_level": privacy_level,
                  "allow_comment": allow_comment, "allow_duet": allow_duet, "allow_stitch": allow_stitch,
                  "brand_organic": brand_organic, "brand_content": brand_content, "cover_s": cover_s})


@server.tool(annotations=WRITE)
def add_post(clip_id: int, platform: Literal["tiktok", "youtube_shorts", "instagram_reels"], url: str) -> str:
    """Markera ett klipp som postat med länken till inlägget (för hand, utan konto: ett per plattform)."""
    return _send("POST", f"/campaign/clips/{clip_id}/posts", {"platform": platform, "url": url})


@server.tool(annotations=OUT)
def mark_submitted(post_id: int, status: Literal["inskickad", "analytics_inskickad"] = "inskickad") -> str:
    """Publicerar utåt. Fråga Robin först.
    Markera att inlägget är inskickat till Content Rewards (inskickad), eller att statistiken är inskickad
    (analytics_inskickad). Bara när Robin själv har skickat in det."""
    return _send("PATCH", f"/campaign/posts/{post_id}", {"status": status})


@server.tool(annotations=WRITE)
def save_stats(post_id: int, views: int | None = None, likes: int | None = None, comments: int | None = None,
               shares: int | None = None, paid_usd: float | None = None,
               status: Literal["postad", "betald"] | None = None) -> str:
    """Spara statistik för ett inlägg (visningar, likes, kommentarer, delningar), eller utbetalningen (paid_usd
    och status betald)."""
    return _send("PATCH", f"/campaign/posts/{post_id}", {"views": views, "likes": likes, "comments": comments,
                                                          "shares": shares, "paid_usd": paid_usd, "status": status})


@server.tool(annotations=REPLACE)
def delete_post(post_id: int) -> str:
    """Tar bort för gott. Fråga Robin först.
    Ta bort ett inlägg som lades in fel."""
    return _send("DELETE", f"/campaign/posts/{post_id}")


if __name__ == "__main__":
    server.run()
