"""MCP-servern för Claude Code. Låtsas-appen via httpx.MockTransport: inga anrop till :8000."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp import Client

from campaign import mcp_server

PUBLISH = "Publicerar utåt. Fråga Robin först."
PUBLISHING = {"upload_youtube", "send_tiktok", "schedule_upload", "mark_submitted"}
READING = {"summary", "list_projects", "get_project", "list_clips", "clip_transcript", "list_streams",
           "list_campaigns", "get_campaign", "get_campaign_yaml", "list_scout", "list_schedule",
           "account_status", "check_setup"}


class FakeApp:
    """Svarar som appen och sparar varje anrop."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answer = httpx.Response(200, json={"ok": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer


@pytest.fixture
def app(monkeypatch) -> FakeApp:
    app = FakeApp()
    monkeypatch.setattr(mcp_server, "http_client",
                        lambda: httpx.Client(base_url=mcp_server.BASE_URL, transport=httpx.MockTransport(app)))
    monkeypatch.setattr(mcp_server, "api_key", lambda: None)
    return app


def _tools():
    async def go():
        async with Client(mcp_server.server) as client:
            return (await client.list_tools()).tools
    return {tool.name: tool for tool in asyncio.run(go())}


def _call(name: str, arguments: dict):
    async def go():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(go())


def test_the_tools_cover_the_web_app():
    tools = _tools()
    assert PUBLISHING | READING <= set(tools)
    assert {"start_run", "retry_run", "finish_run", "update_clip", "edit_clip", "create_campaign",
            "save_campaign_yaml", "set_campaign_active", "refresh_scout", "dismiss_scout", "cancel_schedule",
            "add_post", "save_stats"} <= set(tools)
    assert all(tool.description for tool in tools.values())


def test_annotations_say_what_publishes_and_what_only_reads():
    tools = _tools()
    for name in PUBLISHING:
        tool = tools[name]
        assert tool.description.startswith(PUBLISH), name
        assert tool.annotations.destructive_hint is True and tool.annotations.open_world_hint is True, name
        assert not tool.annotations.read_only_hint
    for name in READING:
        assert tools[name].annotations.read_only_hint is True, name
    for name, tool in tools.items():
        if name not in PUBLISHING:
            assert not tool.description.startswith(PUBLISH), name


@pytest.mark.parametrize(("tool", "arguments", "method", "path", "body"), [
    ("summary", {}, "GET", "/campaign/summary", None),
    ("list_projects", {}, "GET", "/campaign/runs", None),
    ("get_project", {"run_id": 8}, "GET", "/campaign/runs/8", None),
    ("list_clips", {"status": "godkand", "run_id": 8}, "GET", "/campaign/clips?status=godkand&run_id=8", None),
    ("clip_transcript", {"clip_id": 5}, "GET", "/campaign/clips/5/transcript", None),
    ("start_run", {"link": "https://www.twitch.tv/videos/2926877904", "profile": "skiimasktimmy"},
     "POST", "/campaign/runs", {"vod_id": "2926877904", "profile": "skiimasktimmy"}),
    ("update_clip", {"clip_id": 5, "status": "godkand"}, "PATCH", "/campaign/clips/5", {"status": "godkand"}),
    ("update_clip", {"clip_id": 5, "caption": "Text\n#ad"}, "PATCH", "/campaign/clips/5", {"caption": "Text\n#ad"}),
    ("finish_run", {"run_id": 8}, "POST", "/campaign/runs/8/finish", None),
    ("set_campaign_active", {"profile_id": "blizzard_nordics", "active": False}, "PUT",
     "/campaign/profiles/blizzard_nordics/active", {"active": False}),
    ("dismiss_scout", {"ref": "blizzard/wow"}, "POST", "/campaign/scout/blizzard/wow/dismiss", None),
    ("dismiss_scout", {"ref": "blizzard/wow", "dismissed": False}, "POST",
     "/campaign/scout/blizzard/wow/undismiss", None),
    ("upload_youtube", {"clip_id": 5, "title": "Big play", "privacy": "public", "made_for_kids": False},
     "POST", "/campaign/clips/5/youtube",
     {"title": "Big play", "privacy": "public", "made_for_kids": False, "notify_subscribers": True}),
    ("send_tiktok", {"clip_id": 5}, "POST", "/campaign/clips/5/tiktok", {"mode": "draft"}),
    ("schedule_upload", {"clip_id": 5, "platform": "youtube", "at": "2026-11-20T20:00:00+01:00",
                         "fields": {"privacy": "public"}}, "POST", "/campaign/clips/5/schedule",
     {"platform": "youtube", "at": "2026-11-20T20:00:00+01:00", "fields": {"privacy": "public"}}),
    ("cancel_schedule", {"post_id": 3}, "DELETE", "/campaign/schedule/3", None),
    ("add_post", {"clip_id": 5, "platform": "tiktok", "url": "https://www.tiktok.com/@a/video/1"}, "POST",
     "/campaign/clips/5/posts", {"platform": "tiktok", "url": "https://www.tiktok.com/@a/video/1"}),
    ("mark_submitted", {"post_id": 7}, "PATCH", "/campaign/posts/7", {"status": "inskickad"}),
    ("save_stats", {"post_id": 7, "views": 2500, "likes": 30}, "PATCH", "/campaign/posts/7",
     {"views": 2500, "likes": 30}),
])
def test_each_tool_calls_the_same_route_as_the_web(app, tool, arguments, method, path, body):
    result = _call(tool, arguments)
    assert not result.is_error, result.content
    request = app.requests[-1]
    assert request.method == method
    assert request.url.raw_path.decode() == path
    if body is not None:
        assert json.loads(request.content) == body


def test_account_status_reads_both_platforms(app):
    _call("account_status", {})
    assert [r.url.path for r in app.requests] == ["/campaign/youtube/status", "/campaign/tiktok/status"]


def test_the_server_error_comes_back_as_its_swedish_text(app):
    app.answer = httpx.Response(400, json={"detail": "Bara godkända klipp kan laddas upp till YouTube."})
    result = _call("upload_youtube", {"clip_id": 5, "title": "x", "privacy": "public", "made_for_kids": False})
    assert result.is_error
    assert "Bara godkända klipp kan laddas upp till YouTube." in result.content[0].text


def test_a_validation_error_list_becomes_text(app):
    app.answer = httpx.Response(422, json={"detail": [{"loc": ["body", "url"], "msg": "Field required"}]})
    result = _call("add_post", {"clip_id": 5, "platform": "tiktok", "url": "x"})
    assert result.is_error and "url: Field required" in result.content[0].text


def test_app_not_running_says_so(monkeypatch):
    def down(request):
        raise httpx.ConnectError("nere", request=request)

    monkeypatch.setattr(mcp_server, "http_client",
                        lambda: httpx.Client(base_url=mcp_server.BASE_URL, transport=httpx.MockTransport(down)))
    monkeypatch.setattr(mcp_server, "api_key", lambda: None)
    result = _call("summary", {})
    assert result.is_error and "Appen svarar inte" in result.content[0].text


def test_a_link_that_is_not_a_vod_is_refused_without_a_call(app):
    result = _call("start_run", {"link": "https://www.twitch.tv/skiimasktimmy", "profile": "skiimasktimmy"})
    assert result.is_error and app.requests == []


def test_api_key_is_sent_when_the_app_has_one(app, monkeypatch):
    _call("summary", {})
    assert "x-api-key" not in app.requests[-1].headers
    monkeypatch.setattr(mcp_server, "api_key", lambda: "hemlig")
    _call("summary", {})
    assert app.requests[-1].headers["x-api-key"] == "hemlig"
