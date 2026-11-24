"""Inställningar: nycklar som Robin klistrar in i appen. En tmp-.env, påhittad Twitch, inga riktiga anrop.
Inget svar och ingen loggrad får innehålla ett nyckelvärde."""

from __future__ import annotations

import logging
import os
import stat

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, keys, streams
from clipsmith.config.loaders import load_secrets

NAMES = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "BERGET_API_KEY",
         "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET")
CLIENT_ID = "abcdefghij0123456789klmnopqrst"
SECRET = "zyxwvutsrq9876543210ponmlkjihg"
ENV = """# Robins nycklar
KLIPP_ROOT=/x
# TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=gammaltvarde
# en kommentar till

OTHER=1
"""


class FakeTwitch:
    def __init__(self):
        self.error: Exception | None = None
        self.calls: list[tuple] = []

    def client(self, client_id, client_secret, **kwargs):
        fake = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def get_user_id(self, login):
                fake.calls.append((client_id, client_secret, login))
                if fake.error:
                    raise fake.error
                return "12826"

        return Client()


@pytest.fixture
def twitch(monkeypatch) -> FakeTwitch:
    fake = FakeTwitch()
    monkeypatch.setattr(keys, "TwitchClient", fake.client)
    return fake


@pytest.fixture
def env(settings, monkeypatch):
    """Tomma variabler i processen, som när servern startar utan nycklar. Återställs efter testet."""
    for name in NAMES:
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    monkeypatch.chdir(settings.root)  # clipsmiths load_secrets läser .env i arbetsmappen
    path = settings.root / ".env"
    path.write_text(ENV)
    return path


def _client(settings, host: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.include_router(keys.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app, base_url="http://127.0.0.1:8000", client=(host, 50000))


@pytest.fixture
def client(settings, env, twitch) -> TestClient:
    return _client(settings)


def _set(answer: dict) -> dict:
    return {key["name"]: key["set"] for group in answer["groups"] for key in group["keys"]}


def test_get_shows_the_groups_and_what_is_set_without_values(client, env, monkeypatch) -> None:
    env.write_text(ENV + f"ANTHROPIC_API_KEY={SECRET}\nBERGET_API_KEY=\n")
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", CLIENT_ID)
    response = client.get("/campaign/keys")
    assert response.status_code == 200
    answer = response.json()
    assert [(g["id"], g["label"]) for g in answer["groups"]] == [
        ("twitch", "Twitch"), ("anthropic", "Claude"), ("berget", "Berget (transcription)"), ("tiktok", "TikTok"),
        ("youtube", "YouTube")]
    assert answer["groups"][0]["keys"][0] == {"name": "TWITCH_CLIENT_ID", "label": "Client ID", "set": False}
    assert _set(answer) == {"TWITCH_CLIENT_ID": False, "TWITCH_CLIENT_SECRET": True, "ANTHROPIC_API_KEY": True,
                            "BERGET_API_KEY": False, "TIKTOK_CLIENT_KEY": True, "TIKTOK_CLIENT_SECRET": False,
                            "YOUTUBE_CLIENT_ID": False, "YOUTUBE_CLIENT_SECRET": False}
    assert SECRET not in response.text and CLIENT_ID not in response.text and "gammaltvarde" not in response.text


def test_put_twitch_keys_replaces_the_lines_tests_them_and_is_used_at_once(client, env, twitch, caplog,
                                                                             monkeypatch) -> None:
    monkeypatch.setattr(streams, "_cache", {"skiimasktimmy": (0.0, [])})
    with caplog.at_level(logging.DEBUG):
        response = client.put("/campaign/keys", json={"values": {
            "TWITCH_CLIENT_ID": f"  {CLIENT_ID} ", "TWITCH_CLIENT_SECRET": SECRET}})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert _set(answer)["TWITCH_CLIENT_ID"] and _set(answer)["TWITCH_CLIENT_SECRET"]
    assert answer["test"] == {"ok": True, "message": "The keys work."}
    assert twitch.calls == [(CLIENT_ID, SECRET, "twitch")]
    assert env.read_text() == ENV.replace("# TWITCH_CLIENT_ID=", f"TWITCH_CLIENT_ID={CLIENT_ID}").replace(
        "TWITCH_CLIENT_SECRET=gammaltvarde", f"TWITCH_CLIENT_SECRET={SECRET}")
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert (os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"]) == (CLIENT_ID, SECRET)
    assert (load_secrets().twitch_client_id, load_secrets().twitch_client_secret) == (CLIENT_ID, SECRET)
    assert streams._cache == {}
    for text in (response.text, caplog.text):
        assert SECRET not in text and CLIENT_ID not in text
    assert [p.name for p in env.parent.iterdir() if p.name.startswith(".env")] == [".env"]  # ingen tmp-fil kvar


def test_put_other_key_is_added_last_and_does_not_test_twitch(client, env, twitch, monkeypatch) -> None:
    from campaign.config import load_settings

    monkeypatch.setattr(streams, "_cache", {"skiimasktimmy": (0.0, [])})
    response = client.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}})
    assert response.status_code == 200, response.text
    assert response.json()["test"] is None and twitch.calls == []
    assert env.read_text() == ENV + f"BERGET_API_KEY={SECRET}\n"
    assert load_settings(env.parent).berget_api_key == SECRET
    assert streams._cache != {}


def test_put_creates_env_when_it_is_missing_and_adds_a_line_break(client, env) -> None:
    env.unlink()
    assert client.put("/campaign/keys", json={"values": {"ANTHROPIC_API_KEY": SECRET}}).status_code == 200
    assert env.read_text() == f"ANTHROPIC_API_KEY={SECRET}\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    env.write_text("A=1")  # sista raden utan radbrytning
    assert client.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}}).status_code == 200
    assert env.read_text() == f"A=1\nBERGET_API_KEY={SECRET}\n"


@pytest.mark.parametrize("values", [
    {"OPENAI_API_KEY": SECRET},
    {"CLIPSMITH_API_KEY": SECRET},
    {"BERGET_API_KEY": "   "},
    {"BERGET_API_KEY": f"{SECRET} x"},
    {"BERGET_API_KEY": f"{SECRET}\nCLIPSMITH_API_KEY=x"},
    {"BERGET_API_KEY": f'{SECRET}"'},
    {"BERGET_API_KEY": f"{SECRET}'"},
    {"BERGET_API_KEY": f"{SECRET}#"},
    {"BERGET_API_KEY": f"{SECRET}å"},
    {"BERGET_API_KEY": f"{SECRET}\tx"},
    {"BERGET_API_KEY": SECRET * 11},
    {"BERGET_API_KEY": 12345},
    {"ANTHROPIC_API_KEY": SECRET, "BERGET_API_KEY": f"{SECRET} x"},  # inget sparas om en är fel
])
def test_bad_values_are_refused_without_showing_the_value(client, env, values) -> None:
    response = client.put("/campaign/keys", json={"values": values})
    assert response.status_code == 400
    assert SECRET not in response.text and "12345" not in response.text
    assert env.read_text() == ENV
    assert os.environ["ANTHROPIC_API_KEY"] == "" and os.environ["BERGET_API_KEY"] == ""


def test_test_route_tells_what_went_wrong_without_twitchs_text(client, env, twitch) -> None:
    url = "/campaign/keys/test/twitch"
    assert client.post(url).json() == {"ok": False, "message": "Fill in both Client ID and Client secret."}
    assert twitch.calls == []
    env.write_text(ENV + f"TWITCH_CLIENT_ID={CLIENT_ID}\n")  # secret finns redan i .env
    assert client.post(url).json() == {"ok": True, "message": "The keys work."}
    assert twitch.calls == [(CLIENT_ID, "gammaltvarde", "twitch")]

    request = httpx.Request("POST", f"https://id.twitch.tv/oauth2/token?client_secret={SECRET}")
    twitch.error = httpx.HTTPStatusError(f"401 {SECRET}", request=request, response=httpx.Response(403))
    response = client.post(url)
    assert response.json() == {"ok": False,
                               "message": "Twitch did not accept the keys. Check Client ID and secret."}
    twitch.error = httpx.ConnectError(f"nere {SECRET}", request=request)
    response = client.post(url)
    assert response.json() == {"ok": False, "message": "Twitch is not answering. Try again in a while."}
    assert SECRET not in response.text


def test_changing_keys_needs_the_api_key_and_this_computer(settings, env, twitch, monkeypatch) -> None:
    body = {"values": {"BERGET_API_KEY": SECRET}}
    other = _client(settings, host="192.168.1.20")
    assert other.put("/campaign/keys", json=body).status_code == 403
    assert other.post("/campaign/keys/test/twitch").status_code == 403
    assert other.get("/campaign/keys").status_code == 200
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    local = _client(settings)
    assert local.put("/campaign/keys", json=body).status_code == 401
    assert local.post("/campaign/keys/test/twitch").status_code == 401
    assert local.put("/campaign/keys", json=body, headers={"X-Api-Key": "nyckel"}).status_code == 200
    assert env.read_text() == ENV + f"BERGET_API_KEY={SECRET}\n"


@pytest.mark.parametrize("body", [{"values": SECRET}, {"values": [SECRET]}, {"other": SECRET}, [SECRET], SECRET])
def test_a_badly_shaped_body_is_refused_without_echoing_it(client, env, body) -> None:
    response = client.put("/campaign/keys", json=body)
    assert response.status_code == 400
    assert SECRET not in response.text
    assert env.read_text() == ENV


def test_a_plain_text_body_is_refused_without_echoing_it(client, env) -> None:
    response = client.put("/campaign/keys", content=SECRET, headers={"content-type": "text/plain"})
    assert response.status_code == 400
    assert SECRET not in response.text
    assert env.read_text() == ENV


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "192.168.1.20:8000", "localhost.evil.example"])
def test_a_page_under_another_host_name_cannot_change_keys(settings, env, twitch, host) -> None:
    """DNS rebinding: a web page whose name resolves to 127.0.0.1 reaches the server from this computer."""
    local = _client(settings)
    body = {"values": {"BERGET_API_KEY": SECRET}}
    assert local.put("/campaign/keys", json=body, headers={"host": host}).status_code == 403
    assert local.post("/campaign/keys/test/twitch", headers={"host": host}).status_code == 403
    assert env.read_text() == ENV


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"])
def test_local_host_names_can_change_keys(settings, env, twitch, host) -> None:
    local = _client(settings)
    body = {"values": {"BERGET_API_KEY": SECRET}}
    assert local.put("/campaign/keys", json=body, headers={"host": host}).status_code == 200


DOCKER_WEB = "172.29.0.3"  # the web container on the Compose network (docker-compose.yml: 172.29.0.0/24)
COMPOSE_NET = "172.29.0.0/24"


@pytest.fixture(autouse=True)
def _compose_net(monkeypatch):
    """What Compose sets. Tests that need it unset remove it."""
    monkeypatch.setenv("TRUSTED_PROXY_NET", COMPOSE_NET)


@pytest.mark.parametrize("host", ["api", "api:8000"])
def test_the_docker_proxy_host_is_refused_without_trusted_proxy_hosts(settings, env, twitch, monkeypatch,
                                                                       host) -> None:
    monkeypatch.delenv("TRUSTED_PROXY_HOSTS", raising=False)
    web = _client(settings, host=DOCKER_WEB)
    assert web.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}},
                   headers={"host": host}).status_code == 403
    assert env.read_text() == ENV


@pytest.mark.parametrize("host", ["api", "api:8000", "API:8000"])
def test_trusted_proxy_hosts_lets_the_docker_proxy_change_keys(settings, env, twitch, monkeypatch, host) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", " api ,other")
    web = _client(settings, host=DOCKER_WEB)
    assert web.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}},
                   headers={"host": host}).status_code == 200
    assert web.post("/campaign/keys/test/twitch", headers={"host": host}).status_code == 200
    assert env.read_text() == ENV + f"BERGET_API_KEY={SECRET}\n"


@pytest.mark.parametrize(("ip", "host"), [
    (DOCKER_WEB, "evil.example"), (DOCKER_WEB, "evil.example:8000"), (DOCKER_WEB, "api.evil.example"),
    (DOCKER_WEB, "apix:8000"), (DOCKER_WEB, ""),
    ("8.8.8.8", "api:8000"),  # the trusted name from a public address
    ("192.168.1.5", "api:8000"), ("100.64.0.1", "api:8000"),  # the LAN or CGNAT, if the port were on 0.0.0.0
    ("172.18.0.3", "api:8000"), ("10.0.0.5", "api:8000"),  # private, but not the Compose network
    ("172.29.0.1", "localhost:8000"), ("172.29.0.1", "127.0.0.1:8000"),  # local names stay loopback-only
])
def test_trusted_proxy_hosts_still_refuses_other_hosts_and_addresses(settings, env, twitch, monkeypatch,
                                                                     ip, host) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "api,localhost,127.0.0.1")
    other = _client(settings, host=ip)
    assert other.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}},
                     headers={"host": host}).status_code == 403
    assert other.post("/campaign/keys/test/twitch", headers={"host": host}).status_code == 403
    assert env.read_text() == ENV


def test_klipp_env_file_moves_the_saved_keys(settings, env, twitch, monkeypatch) -> None:
    """Docker mounts only data/, so the keys are saved in data/.env on the host."""
    target = settings.root / "data" / ".env"
    target.parent.mkdir()
    monkeypatch.setenv("KLIPP_ENV_FILE", str(target))
    client = _client(settings)
    assert client.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}}).status_code == 200
    assert target.read_text() == f"BERGET_API_KEY={SECRET}\n"
    assert env.read_text() == ENV
    monkeypatch.setenv("BERGET_API_KEY", "")  # a fresh process: the value is read from the file
    assert _set(client.get("/campaign/keys").json())["BERGET_API_KEY"] is True


@pytest.mark.parametrize("net", [None, "", "not-a-network", "0.0.0.0/0"])
def test_without_a_usable_trusted_proxy_net_no_proxy_is_trusted(settings, env, twitch, monkeypatch, net) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "api")
    if net is None:
        monkeypatch.delenv("TRUSTED_PROXY_NET", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_NET", net)
    web = _client(settings, host=DOCKER_WEB)
    assert web.put("/campaign/keys", json={"values": {"BERGET_API_KEY": SECRET}},
                   headers={"host": "api:8000"}).status_code == 403
    assert env.read_text() == ENV


def test_the_compose_files_set_the_same_network(settings) -> None:
    import yaml

    from tests_campaign.conftest import PAKET

    for name in ("docker-compose.yml", "docker-compose.release.yml"):
        compose = yaml.safe_load((PAKET / name).read_text(encoding="utf-8"))
        assert compose["services"]["api"]["environment"]["TRUSTED_PROXY_NET"] == COMPOSE_NET, name
        assert compose["networks"]["default"]["ipam"]["config"] == [{"subnet": COMPOSE_NET}], name
