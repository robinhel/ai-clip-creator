"""MCP-servern för Claude Code. Låtsas-appen via httpx.MockTransport: inga anrop till :8000."""

from __future__ import annotations

import asyncio
import json
import os

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
    ("dismiss_scout", {"ref": "blizzard/wow"}, "POST", "/campaign/scout/blizzard%2Fwow/dismiss", None),
    ("dismiss_scout", {"ref": "blizzard/wow", "dismissed": False}, "POST",
     "/campaign/scout/blizzard%2Fwow/undismiss", None),
    ("get_campaign", {"profile_id": "a b"}, "GET", "/campaign/profiles/a%20b", None),
    ("tiktok_creator_info", {"open_id": "-_x/y"}, "GET", "/campaign/tiktok/accounts/-_x%2Fy/creator_info", None),
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


@pytest.mark.parametrize(("tool", "arguments"), [
    ("dismiss_scout", {"ref": "../clips/5/tiktok?"}),
    ("vod_info", {"vod_id": "../../clips/5/youtube"}),
    ("get_campaign_yaml", {"profile_id": "../clips/5#"}),
    ("set_campaign_active", {"profile_id": "x/../../clips/5", "active": True}),
])
def test_a_text_in_the_path_cannot_reach_another_route(app, tool, arguments):
    _call(tool, arguments)
    for request in app.requests:
        path = request.url.raw_path.decode()
        assert "/clips/" not in path and "?" not in path and "#" not in path, path


def test_dismiss_with_a_tricky_ref_stays_in_the_scout(app):
    _call("dismiss_scout", {"ref": "../clips/5/tiktok?"})
    path = app.requests[-1].url.raw_path.decode()
    assert path.startswith("/campaign/scout/") and "/clips/" not in path


@pytest.mark.parametrize("ref", ["..", "."])
def test_dot_segments_are_refused_without_a_call(app, ref):
    result = _call("dismiss_scout", {"ref": ref})
    assert result.is_error and app.requests == []


def test_the_real_scout_route_decodes_the_slash(settings, monkeypatch):
    """Appens {ref:path} avkodar %2F, så att blizzard/wow fortfarande träffar rätt kampanj."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from campaign import api, scout_api
    from campaign.scout import Scout

    scout = Scout(settings)
    with scout._db() as db:
        db.execute("INSERT INTO scout_campaigns (ref, data) VALUES ('blizzard/wow', '{}')")
    web = FastAPI()
    web.include_router(scout_api.router)
    web.dependency_overrides[api.get_settings] = lambda: settings
    monkeypatch.setattr(mcp_server, "http_client", lambda: TestClient(web, base_url=mcp_server.BASE_URL))
    monkeypatch.setattr(mcp_server, "api_key", lambda: None)
    result = _call("dismiss_scout", {"ref": "blizzard/wow"})
    assert not result.is_error, result.content
    with scout._db() as db:
        assert db.execute("SELECT dismissed FROM scout_campaigns WHERE ref = 'blizzard/wow'").fetchone()[0] == 1


def test_tools_that_cost_money_say_so_first():
    tools = _tools()
    paid = [name for name, tool in tools.items()
            if tool.annotations.open_world_hint and not tool.annotations.destructive_hint and name != "refresh_scout"]
    assert {"start_run", "retry_run", "finish_run"} <= set(paid)
    for name in paid:
        assert tools[name].description.startswith("Kostar pengar"), name
    assert tools["finish_run"].description.startswith("Kostar pengar (bildgranskning med Claude). Fråga Robin först.")


def test_risky_descriptions_ask_robin_first():
    tools = _tools()
    assert tools["update_clip"].description.startswith("Att godkänna (godkand) kräver Robins ja.")
    assert tools["delete_post"].description.startswith("Tar bort för gott. Fråga Robin först.")


def test_a_timeout_says_the_call_may_have_gone_through(monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("för långsamt", request=request)

    monkeypatch.setattr(mcp_server, "http_client",
                        lambda: httpx.Client(base_url=mcp_server.BASE_URL, transport=httpx.MockTransport(slow)))
    monkeypatch.setattr(mcp_server, "api_key", lambda: None)
    result = _call("upload_youtube", {"clip_id": 5, "title": "x", "privacy": "public", "made_for_kids": False})
    assert result.is_error
    assert ("Appen svarade inte inom 10 minuter. Anropet kan ha gått igenom. Kolla upload_status eller "
            "list_schedule innan du försöker igen.") in result.content[0].text


def test_api_key_is_read_fresh_from_the_environment_or_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    assert mcp_server.api_key() is None
    (tmp_path / ".env").write_text("CLIPSMITH_API_KEY=fran-filen\nANTHROPIC_API_KEY=annan\n")
    assert mcp_server.api_key() == "fran-filen"
    monkeypatch.setenv("CLIPSMITH_API_KEY", "fran-miljon")
    assert mcp_server.api_key() == "fran-miljon"
    assert os.environ.get("ANTHROPIC_API_KEY") != "annan"  # bara appens nyckel läses, inget annat ur .env


def test_a_too_big_overlay_is_refused_before_it_is_read(app, tmp_path):
    big = tmp_path / "stor.png"
    with big.open("wb") as file:
        file.truncate(10 * 1024 * 1024 + 1)
    result = _call("upload_overlay", {"profile_id": "x", "png_path": str(big)})
    assert result.is_error and "10 MB" in result.content[0].text and app.requests == []


def test_the_accounts_of_a_clip_and_a_new_text_for_another_account(app):
    tools = _tools()
    assert tools["clip_accounts"].annotations.readOnlyHint
    assert tools["suggest_variant"].description.startswith("Kostar pengar (Claude). Fråga Robin först.")
    _call("clip_accounts", {"clip_id": 5, "platform": "tiktok"})
    assert (app.requests[-1].method, app.requests[-1].url.raw_path.decode()) == (
        "GET", "/campaign/clips/5/accounts?platform=tiktok")
    _call("suggest_variant", {"clip_id": 5, "platform": "tiktok", "account": "openB"})
    request = app.requests[-1]
    assert (request.method, request.url.path, json.loads(request.content)) == (
        "POST", "/campaign/clips/5/variant", {"platform": "tiktok", "account": "openB"})
