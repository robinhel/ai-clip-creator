"""Stänga av en kampanj i appen: raden active: false i profilen. En avstängd kampanj syns bara under
Kampanjer. Dess streams, projekt, klipp och siffror döljs. Påhittad Twitch, inga riktiga anrop."""

from __future__ import annotations

from campaign.cliplog import ClipLog

from .test_api import client  # noqa: F401  (fixture)
from .test_omgang5 import _done_run, _node, _second_campaign, twitch_streams  # noqa: F401  (fixture)
from .test_profiles_api import _profile_text
from .test_profiles_api import client as profiles_client  # noqa: F401  (fixture)
from .test_profiles_api import twitch  # noqa: F401  (fixture)


def test_turning_a_campaign_off_and_on_is_saved_in_its_profile(profiles_client, settings, monkeypatch) -> None:  # noqa: F811
    before = _profile_text(settings)
    assert profiles_client.get("/campaign/profiles/skiimasktimmy").json()["active"] is True

    response = profiles_client.put("/campaign/profiles/skiimasktimmy/active", json={"active": False})
    assert response.status_code == 200, response.text
    assert response.json()["active"] is False
    after = _profile_text(settings)
    assert after.startswith(before)  # kommentarerna och resten av filen är orörda
    assert "\nactive: false" in after[len(before):]
    listed = profiles_client.get("/campaign/profiles").json()
    assert [p["active"] for p in listed if p["name"] == "skiimasktimmy"] == [False]

    assert profiles_client.put("/campaign/profiles/skiimasktimmy/active", json={"active": True}).json()["active"]
    turned_on = _profile_text(settings)
    assert turned_on.count("active:") == 1  # samma rad ändras, ingen ny
    assert profiles_client.put("/campaign/profiles/finnsinte/active", json={"active": False}).status_code == 404

    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert profiles_client.put("/campaign/profiles/skiimasktimmy/active", json={"active": False}).status_code == 401
    assert _profile_text(settings) == turned_on


def test_active_must_be_true_or_false_in_the_yaml(profiles_client, settings) -> None:  # noqa: F811
    response = profiles_client.put("/campaign/profiles/skiimasktimmy/yaml",
                                   json={"yaml": _profile_text(settings) + 'active: "nej"\n'})
    assert response.status_code == 400
    assert "active" in response.json()["detail"]


def test_an_off_campaign_is_hidden_everywhere_but_under_campaigns(client, settings, twitch_streams) -> None:  # noqa: F811
    _second_campaign(settings, "annan", "annankanal")
    twitch_streams.users["annankanal"] = ("AnnanKanal", [_node("2925999999", "2026-11-12T10:00:00Z")])
    log = ClipLog(settings.data_dir / "klipp.db")
    timmy_run = _done_run(settings, "2925991945")
    other_run = _done_run(settings, "2925999999")
    log.add_run(other_run, "2925999999", "annan", "Bodycam")  # samma körning hör till den andra kampanjen
    log.add(vod_id="2925991945", profile="skiimasktimmy", file="a.mp4", title="a", caption="c", status="godkand")
    log.add(vod_id="2925999999", profile="annan", file="b.mp4", title="b", caption="c", status="godkand")
    log.add(vod_id="2925999999", profile="annan", file="c.mp4", title="c", caption="c", status="renderad")

    def shown() -> dict:
        summary = client.get("/campaign/summary").json()
        return {"streams": sorted(s["vod_id"] for s in client.get("/campaign/streams").json()),
                "runs": sorted(r["run_id"] for r in client.get("/campaign/runs").json()),
                "clips": sorted(c["profile"] for c in client.get("/campaign/clips").json()),
                "counts": summary["counts"], "todo": len(summary["todo"])}

    assert shown() == {"streams": ["2925070238", "2925991945", "2925999999"], "runs": sorted([timmy_run, other_run]),
                       "clips": ["annan", "annan", "skiimasktimmy"], "counts": {"godkand": 2, "renderad": 1},
                       "todo": 2}

    path = settings.profiles_dir / "annan.yaml"
    path.write_text(path.read_text() + "active: false\n")
    assert shown() == {"streams": ["2925070238", "2925991945"], "runs": [timmy_run], "clips": ["skiimasktimmy"],
                       "counts": {"godkand": 1}, "todo": 1}
    listed = {p["name"]: p["active"] for p in client.get("/campaign/profiles").json()}
    assert listed == {"annan": False, "skiimasktimmy": True}
    # En direktlänk till den avstängda kampanjens projekt visar fortfarande dess klipp.
    assert len(client.get("/campaign/clips", params={"run_id": other_run}).json()) == 2
