"""Hela API:t svarar bara på en lokal Host, och skrivande anrop från en främmande webbsida (Origin) nekas.

Testerna går mot vägar som inte finns: släpps anropet igenom blir svaret 404, stoppas det blir det 403.
Då ändras inget på riktigt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipsmith.api.app import app

LOCAL = "http://localhost:3000"


def _client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8765", "[::1]",
                                  "[::1]:8000", "LOCALHOST:8000"])
def test_a_local_host_gets_through(host) -> None:
    client = _client()
    assert client.get("/openapi.json", headers={"host": host}).status_code == 200
    assert client.post("/campaign/no-such-path", headers={"host": host}).status_code == 404


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "localhost.evil.example",
                                  "localhost.evil.example:8000", "127.0.0.1.evil.example", "[::2]:8000",
                                  "api:8000", "testserver", ""])
def test_any_other_host_is_refused(host) -> None:
    client = _client()
    for method in ("GET", "POST", "DELETE"):
        response = client.request(method, "/campaign/no-such-path", headers={"host": host})
        assert response.status_code == 403
        assert response.text == "Forbidden: this server only answers requests to localhost."


def test_allowed_hosts_adds_names_for_docker(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", "api, Other.Local ")
    client = _client()
    assert client.post("/campaign/no-such-path", headers={"host": "api:8000"}).status_code == 404
    assert client.get("/openapi.json", headers={"host": "other.local"}).status_code == 200
    assert client.get("/openapi.json", headers={"host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example:3000",
                                    "http://localhost:3001", "null"])
def test_a_write_from_another_web_page_is_refused(origin) -> None:
    client = _client()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.request(method, "/campaign/no-such-path",
                                  headers={"host": "localhost:8000", "origin": origin})
        assert response.status_code == 403
        assert response.text == "Forbidden: this origin may not change anything here."


def test_reads_and_preflight_from_another_origin_are_not_blocked_by_the_guard() -> None:
    client = _client()
    headers = {"host": "localhost:8000", "origin": "https://evil.example"}
    assert client.get("/openapi.json", headers=headers).status_code == 200
    assert client.head("/campaign/no-such-path", headers=headers).status_code == 404
    preflight = client.options("/campaign/no-such-path", headers={
        **headers, "access-control-request-method": "POST"})
    assert preflight.status_code == 400  # CORS says no, without the guard's 403
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("origin", [LOCAL, "http://127.0.0.1:3000"])
def test_the_dashboard_and_its_proxy_still_work(origin) -> None:
    # Proxyn skickar inte vidare Host, så fetch sätter Host till API:ts adress. Webbläsarens Origin följer med.
    client = _client()
    response = client.post("/campaign/no-such-path", headers={"host": "localhost:8000", "origin": origin})
    assert response.status_code == 404


def test_a_write_without_origin_from_a_local_tool_works() -> None:
    assert _client().post("/campaign/no-such-path", headers={"host": "127.0.0.1:8000"}).status_code == 404
