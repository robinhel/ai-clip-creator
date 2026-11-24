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
        "Controls Robin's clip app (campaign-ready clips from Twitch streams). The app must run on :8000. "
        "Tools whose description starts with 'Publishes externally' or 'Costs money' may only be used when "
        "Robin has said yes. A clip may only be posted when the rule check is green and Robin has approved it. "
        "Errors come back with the app's own text; show it to Robin."
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
        raise ToolError(f"Invalid id: {value!r}")
    return quote(value, safe="")


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return response.text[:500] or f"response {response.status_code}"
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
        raise ToolError("The app did not answer within 10 minutes. The call may have gone through. Check upload_status or "
                        "list_schedule before you try again.") from error
    except httpx.HTTPError as error:
        raise ToolError(f"The app is not answering on {BASE_URL}. Start it with clipsmith serve and try again.") \
            from error
    if response.status_code >= 400:
        raise ToolError(f"The app answered {response.status_code}: {_detail(response)}")
    if not response.content:
        return json.dumps({"ok": True})
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except ValueError:
        return response.text


# ---------- översikt ----------


@server.tool(annotations=READ)
def summary() -> str:
    """The overview: Claude budget, number of clips per status, to-do, disk and earnings. Start here."""
    return _send("GET", "/campaign/summary")


@server.tool(annotations=READ)
def check_setup() -> str:
    """The check from Settings: ffmpeg, keys (only whether they exist), campaigns, budget, SSD and space."""
    return _send("GET", "/campaign/setup")


@server.tool(annotations=READ)
def account_status() -> str:
    """Connected YouTube channels and TikTok accounts, without secrets: id, name, whether they can publish,
    today's uploads. Use before uploading to choose channel_id or open_id."""
    return json.dumps({"youtube": json.loads(_send("GET", "/campaign/youtube/status")),
                       "tiktok": json.loads(_send("GET", "/campaign/tiktok/status"))}, ensure_ascii=False)


@server.tool(annotations=READ)
def tiktok_creator_info(open_id: str) -> str:
    """A TikTok account's options for direct posting: privacy_level_options, longest clip, disabled comments."""
    return _send("GET", f"/campaign/tiktok/accounts/{_seg(open_id)}/creator_info")


# ---------- streams och projekt ----------


@server.tool(annotations=READ)
def list_streams() -> str:
    """The latest streams for the active campaigns' channels, with tips on which are good to clip."""
    return _send("GET", "/campaign/streams")


@server.tool(annotations=READ)
def vod_info(vod_id: str) -> str:
    """Facts about a Twitch VOD before a project: channel, title, length, whether it is live and whether the disk has room."""
    return _send("GET", f"/campaign/vod/{_seg(vod_id)}")


@server.tool(annotations=READ)
def list_projects() -> str:
    """The projects (one per stream and campaign), newest first, with status and number of clips."""
    return _send("GET", "/campaign/runs")


@server.tool(annotations=READ)
def get_project(run_id: int) -> str:
    """A project with progress per step (download, transcribe, pick, cut, campaign-ready) and the cost."""
    return _send("GET", f"/campaign/runs/{run_id}")


@server.tool(annotations=PAID)
def start_run(link: str, profile: str, game: str | None = None, start_s: float | None = None,
              end_s: float | None = None, focus: str | None = None, min_s: int | None = None,
              max_s: int | None = None) -> str:
    """Costs money (Twitch download, transcription and Claude). Ask Robin first.
    Start a project for a campaign from a Twitch link (twitch.tv/videos/<id>) or a VOD ID.
    profile is the campaign's id from list_campaigns. start_s/end_s limit the part of the stream, focus is a short
    instruction on what to look for, min_s/max_s the clips' length. Only one project at a time."""
    found = re.fullmatch(r"\s*(\d+)\s*", link) or re.search(r"twitch\.tv/videos/(\d+)", link)
    if not found:
        raise ToolError("The link is not a Twitch VOD. Give a link like twitch.tv/videos/123456789 or just the id.")
    return _send("POST", "/campaign/runs", {"profile": profile, "vod_id": found.group(1), "game": game,
                                            "start_s": start_s, "end_s": end_s, "focus": focus,
                                            "min_s": min_s, "max_s": max_s})


@server.tool(annotations=PAID)
def retry_run(run_id: int) -> str:
    """Costs money (Twitch download, transcription and Claude). Ask Robin first.
    Run a failed project again with the same choices (the Run again button). Finished steps are skipped."""
    return _send("POST", f"/campaign/runs/{run_id}/retry")


@server.tool(annotations=PAID)
def finish_run(run_id: int) -> str:
    """Costs money (image review with Claude). Ask Robin first.
    Make the project's clips campaign-ready again: overlay, text, caption and rule check. Use after the
    campaign's profile has changed."""
    return _send("POST", f"/campaign/runs/{run_id}/finish")


# ---------- klipp ----------


@server.tool(annotations=READ)
def list_clips(status: str | None = None, vod_id: str | None = None, run_id: int | None = None) -> str:
    """Clips in the clip log, with caption, problems, score and posts. Filter by status (renderad, underkand,
    godkand, nekad, postad, inskickad, analytics_inskickad, betald), VOD or project (run_id)."""
    return _send("GET", "/campaign/clips", params={"status": status, "vod_id": vod_id, "run_id": run_id})


@server.tool(annotations=READ)
def clip_transcript(clip_id: int) -> str:
    """What is said in the clip, with times. Use it to write a true headline or caption."""
    return _send("GET", f"/campaign/clips/{clip_id}/transcript")


@server.tool(annotations=READ)
def clip_edit_info(clip_id: int) -> str:
    """Can the clip be trimmed, and how is it trimmed now (the limits for in_s and out_s)."""
    return _send("GET", f"/campaign/clips/{clip_id}/edit")


@server.tool(annotations=READ)
def upload_status(clip_id: int) -> str:
    """The latest upload of the clip to YouTube and to TikTok, with status and link."""
    return json.dumps({"youtube": json.loads(_send("GET", f"/campaign/clips/{clip_id}/youtube")),
                       "tiktok": json.loads(_send("GET", f"/campaign/clips/{clip_id}/tiktok"))}, ensure_ascii=False)


@server.tool(annotations=WRITE)
def update_clip(clip_id: int, status: Literal["renderad", "godkand", "nekad"] | None = None,
                caption: str | None = None, title: str | None = None, clip_format: str | None = None,
                note: str | None = None) -> str:
    """Approving (godkand) requires Robin's yes.
    Approve (godkand) or reject (nekad) a clip, or change caption, title, format or note. The caption is checked again against the campaign's rules; the problems show in the response.
    The headline in the image is changed with edit_clip."""
    return _send("PATCH", f"/campaign/clips/{clip_id}", {"status": status, "caption": caption, "title": title,
                                                         "format": clip_format, "note": note})


@server.tool(annotations=REPLACE)
def edit_clip(clip_id: int, in_s: float | None = None, out_s: float | None = None, title: str | None = None,
              headline: str | None = None) -> str:
    """Trim the clip (in_s/out_s within the raw clip, see clip_edit_info), change the title (title, the caption's first
    line) and the text in the image (headline, at most 80 characters; "" puts the title in the image). The clip is remade with ffmpeg.
    Not possible while a project is running."""
    return _send("POST", f"/campaign/clips/{clip_id}/edit",
                 {"in_s": in_s, "out_s": out_s, "title": title, "headline": headline})


# ---------- kampanjer ----------


@server.tool(annotations=READ)
def list_campaigns() -> str:
    """The campaigns (profiles) with channel, whether they are active and whether they are ready for a project."""
    return _send("GET", "/campaign/profiles")


@server.tool(annotations=READ)
def get_campaign(profile_id: str) -> str:
    """A campaign in detail: rules, formats, caption, payment and links."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}")


@server.tool(annotations=READ)
def get_campaign_yaml(profile_id: str) -> str:
    """The campaign's profile as YAML text, with comments. Read it before save_campaign_yaml."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}/yaml")


@server.tool(annotations=READ)
def get_reframe(profile_id: str) -> str:
    """The campaign's 9:16 crop: the boxes for the camera and the game, and the split."""
    return _send("GET", f"/campaign/profiles/{_seg(profile_id)}/reframe")


@server.tool(annotations=WRITE)
def create_campaign(channel: str, streamer: str, fields: dict | None = None) -> str:
    """Create a campaign the way the Campaigns page does. channel is the Twitch channel, streamer the name. fields can have:
    campaign, language, clip_min_s, clip_max_s, on_screen_text, text_position (top|bottom), overlay_required,
    overlay_mode (full|top|bottom), facecam_required, must_include, hashtags, caption_template,
    formats [{name, description}], cpm_usd, min_views, max_payout_per_clip_usd, engagement_min_percent."""
    return _send("POST", "/campaign/profiles", {**(fields or {}), "channel": channel, "streamer": streamer})


@server.tool(annotations=REPLACE)
def save_campaign_yaml(profile_id: str, yaml: str) -> str:
    """Save the campaign's whole profile as YAML text (replaces the file). The app checks the text first."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/yaml", {"yaml": yaml})


@server.tool(annotations=WRITE)
def set_campaign_active(profile_id: str, active: bool) -> str:
    """Turn a campaign on or off in the app. Off hides its streams, projects and clips."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/active", {"active": active})


@server.tool(annotations=REPLACE)
def upload_overlay(profile_id: str, png_path: str) -> str:
    """Replace the campaign's overlay with a PNG file on the Mac (path). Replaces the old one."""
    path = Path(png_path).expanduser()
    try:
        if path.stat().st_size > OVERLAY_MAX:
            raise ToolError(f"The file is larger than 10 MB: {png_path}")
        data = path.read_bytes()
    except OSError as error:
        raise ToolError(f"The file cannot be read: {png_path}") from error
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/overlay", content=data, content_type="image/png")


@server.tool(annotations=REPLACE)
def save_reframe(profile_id: str, webcam_rects: list[dict], gameplay_rect: dict, split_ratio: float) -> str:
    """Save the campaign's 9:16 crop. Boxes as {x, y, w, h}; read get_reframe first."""
    return _send("PUT", f"/campaign/profiles/{_seg(profile_id)}/reframe",
                 {"webcam_rects": webcam_rects, "gameplay_rect": gameplay_rect, "split_ratio": split_ratio})


# ---------- bevakningen ----------


@server.tool(annotations=READ)
def list_scout() -> str:
    """New campaigns from Scout (ClippingAlpha), with a score 0–100 and why, plus the ones sorted out."""
    return _send("GET", "/campaign/scout")


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
def refresh_scout() -> str:
    """Check now: fetch new campaigns from ClippingAlpha right away (counts against the daily limit)."""
    return _send("POST", "/campaign/scout/refresh")


@server.tool(annotations=WRITE)
def dismiss_scout(ref: str, dismissed: bool = True) -> str:
    """Hide a campaign in Scout (ref like 'brand/series'), or show it again with dismissed=false."""
    return _send("POST", f"/campaign/scout/{_seg(ref)}/{'dismiss' if dismissed else 'undismiss'}")


# ---------- schemat ----------


@server.tool(annotations=READ)
def list_schedule() -> str:
    """Scheduled uploads: waiting first, then the 20 most recently finished with status and errors."""
    return _send("GET", "/campaign/schedule")


@server.tool(annotations=READ)
def clip_accounts(clip_id: int, platform: Literal["tiktok", "youtube"]) -> str:
    """The campaign's accounts for a clip on the platform: where the clip is posted, scheduled or free (state).
    multi is true when the campaign posts the same clip on several accounts: at most 5, with its own text on each."""
    return _send("GET", f"/campaign/clips/{clip_id}/accounts", params={"platform": platform})


@server.tool(annotations=READ)
def version_options(clip_id: int, platform: Literal["tiktok", "youtube"], account: str) -> str:
    """The options for a new version of the clip for another account: whether it is possible (allowed, reason), fonts,
    stickers, overlays and the account's fixed style (defaults). The start point may be min_start_cut_s–max_start_cut_s
    seconds from the original's start (negative is earlier, positive later). new_start_s (3 s) is how far it must
    move to count as new. hook is the original's hook, hooks_used the hooks on the original and its versions,
    hook_text whether the campaign shows the hook in the image. account_warning is a warning when the clip has a post without an account
    on the platform (it may already be on the account); it does not stop the version."""
    return _send("GET", f"/campaign/clips/{clip_id}/versions/options", params={"platform": platform, "account": account})


@server.tool(annotations=WRITE)
def make_version(clip_id: int, platform: Literal["tiktok", "youtube"], account: str, font: str | None = None,
                 start_cut_s: float | None = None, overlay: str | None = None, sticker: dict | None = None,
                 mirror: bool | None = None, speed: float | None = None, filter: str | None = None,
                 brightness: float | None = None, saturation: float | None = None, hook: str | None = None) -> str:
    """Make a new version of an approved clip for another account in the campaign. Own contribution: a new hook (hook, the text
    at the top of the image and the version's headline, at most 3 lines that fit in the image, fewer in zoom, the server says if it
    is too long; empty = the original's) or a new start point
    (start_cut_s, earlier or later, within min_start_cut_s–max_start_cut_s in version_options; 3 s or more
    counts as new and turns off the cold open). Without either of them the version gets a warning. Also: another font,
    own overlay and sticker ({file, position, start_s, duration_s}), mirroring, speed (speeds),
    color filter (filters), brightness (-0.3–0.3) and saturation (-0.5–0.5). The version becomes its own clip that Robin
    approves. Nothing is published. Read version_options first."""
    return _send("POST", f"/campaign/clips/{clip_id}/versions",
                 {"platform": platform, "account": account, "font": font, "start_cut_s": start_cut_s,
                  "overlay": overlay, "sticker": sticker, "mirror": mirror, "speed": speed, "filter": filter,
                  "brightness": brightness, "saturation": saturation, "hook": hook})


@server.tool(annotations=PAID)
def suggest_variant(clip_id: int, platform: Literal["tiktok", "youtube"], account: str) -> str:
    """Costs money (Claude). Ask Robin first.
    Suggest a new headline and caption for the clip on another account (open_id or channel_id), different from the texts the
    clip already has on the platform. Nothing is posted."""
    return _send("POST", f"/campaign/clips/{clip_id}/variant", {"platform": platform, "account": account})


@server.tool(annotations=PAID)
def chat_send(profile_id: str, message: str = "", chat_id: int | None = None, confirm: bool = False) -> str:
    """Costs money (Claude). Ask Robin first.
    Write in the campaign's chat: what Robin wants clips of ("when they show new breeds", "more like these", "shorter").
    Claude searches the campaign's transcripts and answers with hits. Without chat_id a new chat starts. If the chat answers
    with confirm (an expensive search), send confirm=True with the same chat_id when Robin has said yes. The response is the whole
    chat with messages and hits (hits)."""
    if chat_id is None:
        return _send("POST", f"/campaign/profiles/{_seg(profile_id)}/chats", {"message": message})
    return _send("POST", f"/campaign/chats/{chat_id}/messages", {"confirm": True} if confirm else {"message": message})


@server.tool(annotations=PAID)
def chat_make_clips(chat_id: int, hit_ids: list[int]) -> str:
    """Costs money (image review with Claude, and download from Twitch when the video is missing). Ask Robin first.
    Make clips from chosen hits in the chat. The clips become normal clips to review (status renderad). Answers with
    the project's id; follow it with get_project."""
    return _send("POST", f"/campaign/chats/{chat_id}/clips", {"hit_ids": hit_ids})


@server.tool(annotations=OUT)
def schedule_upload(clip_id: int, platform: Literal["youtube", "tiktok"], at: str, fields: dict,
                    account: str | None = None) -> str:
    """Publishes externally. Ask Robin first.
    Schedule the upload of an approved clip. at is ISO time with time zone (Swedish time: +01:00 or +02:00).
    account is YouTube's channel_id or TikTok's open_id. fields as in upload_youtube (title, description, privacy,
    made_for_kids, notify_subscribers) or send_tiktok (title, privacy_level, allow_*, brand_*). TikTok without
    privacy_level becomes a reminder instead of a post."""
    return _send("POST", f"/campaign/clips/{clip_id}/schedule",
                 {"platform": platform, "at": at, "account": account, "fields": fields})


@server.tool(annotations=WRITE)
def cancel_schedule(post_id: int) -> str:
    """Cancel a waiting scheduled upload."""
    return _send("DELETE", f"/campaign/schedule/{post_id}")


# ---------- uppladdning och inlägg ----------


@server.tool(annotations=OUT)
def upload_youtube(clip_id: int, title: str, privacy: Literal["public", "unlisted", "private"],
                   made_for_kids: bool, description: str | None = None, channel_id: str | None = None,
                   notify_subscribers: bool = True) -> str:
    """Publishes externally. Ask Robin first.
    Upload an approved clip as a YouTube Short. description is the caption. channel_id is needed when several channels
    are connected (account_status)."""
    return _send("POST", f"/campaign/clips/{clip_id}/youtube",
                 {"title": title, "privacy": privacy, "made_for_kids": made_for_kids, "description": description,
                  "channel_id": channel_id, "notify_subscribers": notify_subscribers})


@server.tool(annotations=OUT)
def send_tiktok(clip_id: int, mode: Literal["draft", "direct"] = "draft", open_id: str | None = None,
                title: str | None = None, privacy_level: str | None = None, allow_comment: bool | None = None,
                allow_duet: bool | None = None, allow_stitch: bool | None = None,
                brand_organic: bool | None = None, brand_content: bool | None = None,
                cover_s: float | None = None) -> str:
    """Publishes externally. Ask Robin first.
    Send an approved clip to TikTok: as a draft in the inbox (draft) or posted directly (direct, requires
    privacy_level from tiktok_creator_info). title is the caption when posting directly. cover_s is the cover,
    seconds into the clip (direct only)."""
    return _send("POST", f"/campaign/clips/{clip_id}/tiktok",
                 {"mode": mode, "open_id": open_id, "title": title, "privacy_level": privacy_level,
                  "allow_comment": allow_comment, "allow_duet": allow_duet, "allow_stitch": allow_stitch,
                  "brand_organic": brand_organic, "brand_content": brand_content, "cover_s": cover_s})


@server.tool(annotations=WRITE)
def add_post(clip_id: int, platform: Literal["tiktok", "youtube_shorts", "instagram_reels"], url: str) -> str:
    """Mark a clip as posted with the link to the post (by hand, without an account: one per platform)."""
    return _send("POST", f"/campaign/clips/{clip_id}/posts", {"platform": platform, "url": url})


@server.tool(annotations=OUT)
def mark_submitted(post_id: int, status: Literal["inskickad", "analytics_inskickad"] = "inskickad") -> str:
    """Publishes externally. Ask Robin first.
    Mark that the post is submitted to Content Rewards (inskickad), or that the stats are submitted
    (analytics_inskickad). Only when Robin has submitted it himself."""
    return _send("PATCH", f"/campaign/posts/{post_id}", {"status": status})


@server.tool(annotations=WRITE)
def save_stats(post_id: int, views: int | None = None, likes: int | None = None, comments: int | None = None,
               shares: int | None = None, paid_usd: float | None = None,
               status: Literal["postad", "betald"] | None = None, avg_watch_s: float | None = None,
               full_pct: float | None = None, fyp_pct: float | None = None, saves: int | None = None,
               countries_pct: float | None = None) -> str:
    """Save stats for a post (views, likes, comments, shares), or the payout (paid_usd
    and status betald). More metrics from TikTok Studio: avg_watch_s (average watch time in seconds),
    full_pct (share who watch to the end), fyp_pct (share from For You), saves (saves) and countries_pct (share
    from the campaign's countries). The shares are percent, 0-100."""
    return _send("PATCH", f"/campaign/posts/{post_id}", {"views": views, "likes": likes, "comments": comments,
                                                          "shares": shares, "paid_usd": paid_usd, "status": status,
                                                          "avg_watch_s": avg_watch_s, "full_pct": full_pct,
                                                          "fyp_pct": fyp_pct, "saves": saves,
                                                          "countries_pct": countries_pct})


@server.tool(annotations=REPLACE)
def delete_post(post_id: int) -> str:
    """Deletes for good. Ask Robin first.
    Delete a post that was added by mistake."""
    return _send("DELETE", f"/campaign/posts/{post_id}")


if __name__ == "__main__":
    server.run()
