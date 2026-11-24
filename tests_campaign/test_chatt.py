"""Chatten på kampanjsidan: Robin skriver vad han vill ha klipp på, Claude letar i kampanjens transkript,
och träffarna blir vanliga klipp. Låtsas-Claude och låtsas-Twitch; inga riktiga anrop."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from campaign import chat, download, pipeline, profiles_api
from campaign.brain import Brain
from campaign.budget import Budget, BudgetExceeded, budget_from_settings
from campaign.cliplog import ClipLog
from campaign.media import probe
from campaign.profile import load_profile
from campaign.subtitles import words_path

from .conftest import PAKET, make_clip, needs_subtitles, set_profile
from .test_api import client  # noqa: F401  (fixture)
from .test_natt_lage import _run

SEGMENTS = {  # vodA: tre meningar med tystnad mellan
    "vodA": [(0.0, 10.0, "In this video we talk about the weather today"),
             (20.0, 30.0, "The new races are the Skyborn and they can walk on air"),
             (40.0, 50.0, "Thanks for watching and see you next time friends")],
    "vodB": [(0.0, 10.0, "Dwarf shamans are opening the alliance up to totems")],
    "other": [(0.0, 10.0, "Secret words from another campaign entirely here")],
}


class Script:
    """Låtsas-Claude som svarar med svaren i tur och ordning och sparar varje anrop."""

    def __init__(self, *answers: dict):
        self.answers = list(answers)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                               content=[SimpleNamespace(type="tool_use", input=self.answers.pop(0))])


def _words(vod: str) -> list[dict]:
    """Orden i vod:ens transkript med tider, som _write_transcript skriver dem."""
    words = []
    for start, end, text in SEGMENTS[vod]:
        parts = text.split()
        step = (end - start) / len(parts)
        words += [{"start": round(start + i * step, 3), "end": round(start + i * step + step * 0.8, 3),
                   "word": " " + w, "probability": 0.9} for i, w in enumerate(parts)]
    return words


def _write_transcript(settings, vod: str) -> None:
    words = _words(vod)
    segments = []
    for start, end, text in SEGMENTS[vod]:
        segments.append({"start": start, "end": end, "text": " " + text,
                         "words": [w for w in words if start <= w["start"] < end]})
    folder = settings.work_dir / vod
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "transcript.json").write_text(json.dumps({"video_id": vod, "language": "en", "segments": segments}),
                                            encoding="utf-8")


def _word(vod: str, text: str, nth: int = 0) -> dict:
    return [w for w in _words(vod) if w["word"].strip() == text][nth]


@pytest.fixture
def chatapp(client, settings, monkeypatch):  # noqa: F811
    """Kampanjen med tre källor: vodA (äldst, transkript), vodB (nyast, transkript), vodC (inget transkript),
    och en körning för en annan kampanj. Claude är `script`, som testet fyller i."""
    set_profile(settings, active=True)
    log = ClipLog(settings.data_dir / "klipp.db")
    for run_id, vod, profile in ((1, "vodA", "skiimasktimmy"), (2, "vodB", "skiimasktimmy"),
                                 (3, "vodC", "skiimasktimmy"), (4, "other", "annan")):
        log.add_run(run_id, vod, profile, "Bodycam", title=f"Titel {vod}", duration_s=60)
    with sqlite3.connect(log.db_path) as db:  # vodC nyast, sedan vodB, sist vodA
        for run_id, day in ((1, 10), (2, 11), (3, 12), (4, 13)):
            db.execute("UPDATE runs SET created = ? WHERE run_id = ?", (f"2026-11-{day}T08:00:00+00:00", run_id))
    for vod in ("vodA", "vodB", "other"):
        _write_transcript(settings, vod)
    (settings.work_dir / "vodC").mkdir(parents=True)
    state = SimpleNamespace(script=Script())
    monkeypatch.setattr(chat, "brain", lambda s: Brain(s, budget_from_settings(s), client=state.script))
    state.log = log
    return state


def _search(sources=(), query="new races", reply="Jag letar.") -> dict:
    return {"action": "search", "reply": reply, "sources": list(sources), "query": query}


def _hit(vod="vodA", start_quote="The new races", end_quote="walk on air", start_s=20.0, end_s=30.0,
         title="New races revealed", grade=8, **extra) -> dict:
    return {"vod_id": vod, "start_quote": start_quote, "end_quote": end_quote, "start_s": start_s,
            "end_s": end_s, "title": title, "reason": "Visar nya raser.", "grade": grade, **extra}


def _new_chat(client, chatapp, *answers, message="när de visar nya raser"):  # noqa: F811
    chatapp.script.answers += list(answers)
    return client.post("/campaign/profiles/skiimasktimmy/chats", json={"message": message})


# ---------- material, filter och profilfält ----------


def test_material_lists_only_the_campaigns_sources_newest_first(client, chatapp, settings) -> None:  # noqa: F811
    make_clip(settings.work_dir / "vodA" / "vodA.mp4", seconds=1)
    chatapp.log.add(vod_id="vodA", profile="skiimasktimmy", file="x.mp4", title="t", caption="c")
    chatapp.log.add(vod_id="vodA", profile="annan", file="y.mp4", title="t", caption="c")
    items = client.get("/campaign/profiles/skiimasktimmy/material").json()
    assert [i["vod_id"] for i in items] == ["vodC", "vodB", "vodA"]
    a = items[2]
    assert (a["kind"], a["title"], a["duration_s"], a["has_video"], a["has_transcript"], a["clips"]) == \
        ("local", "Titel vodA", 60, True, True, 1)
    assert a["created"].startswith("2026-11-10")
    assert (items[0]["has_transcript"], items[1]["has_video"]) == (False, False)
    assert client.get("/campaign/profiles/finnsinte/material").status_code == 404


def test_a_twitch_vod_is_kind_twitch(client, chatapp) -> None:  # noqa: F811
    chatapp.log.add_run(9, "2926877904", "skiimasktimmy", None, title="Stream", duration_s=100)
    items = client.get("/campaign/profiles/skiimasktimmy/material").json()
    assert {i["vod_id"]: i["kind"] for i in items}["2926877904"] == "twitch"


def test_clips_filter_on_campaign(client, chatapp) -> None:  # noqa: F811
    mine = chatapp.log.add(vod_id="vodA", profile="skiimasktimmy", file="x.mp4", title="t", caption="c")
    chatapp.log.add(vod_id="vodA", profile="annan", file="y.mp4", title="t", caption="c")
    assert [c["id"] for c in client.get("/campaign/clips", params={"profile": "skiimasktimmy"}).json()] == [mine]


def test_default_scope_comes_from_the_profile(settings) -> None:
    assert load_profile("skiimasktimmy", settings.profiles_dir).search_default_scope == "latest"
    set_profile(settings, search={"default_scope": "all"})
    assert load_profile("skiimasktimmy", settings.profiles_dir).search_default_scope == "all"
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    assert profiles_api.get_profile("skiimasktimmy", settings)["search_default_scope"] == "all"
    set_profile(settings, search={"default_scope": "allt"})
    with pytest.raises(SystemExit, match="search.default_scope"):
        load_profile("skiimasktimmy", settings.profiles_dir)


def test_blizzard_searches_all_material() -> None:
    data = yaml.safe_load((PAKET / "profiles" / "blizzard_nordics.yaml").read_text(encoding="utf-8"))
    assert data["search"]["default_scope"] == "all"


# ---------- tider ur citat ----------


def test_times_come_from_the_quoted_words() -> None:
    words = [{"start": float(i), "end": i + 0.5, "word": w} for i, w in enumerate(
        "go go now we win the fight and then we win the fight again".split())]
    # "we win the fight" sägs två gånger: platsen närmast Claudes sekunder gäller
    assert chat.place(words, "we win", "the fight", 8.5, 12.0) == (9.0, 12.5)
    assert chat.place(words, "we win", "the fight", 3.0, 6.0) == (3.0, 6.5)
    assert chat.place(words, "Now, we WIN!", "fight.", 3.0, 6.0) == (2.0, 6.5)  # skiljetecken och versaler
    assert chat.place(words, "nobody said this", "the fight", 3.0, 6.0) is None
    assert chat.place(words, "again", "go now", 13.0, 14.0) is None  # slutet före början


# ---------- en sökning ----------


def test_a_search_gives_hits_with_times_from_the_quotes(client, chatapp, settings) -> None:  # noqa: F811
    set_profile(settings, active=True, search={"default_scope": "all"})
    answer = _new_chat(client, chatapp, _search(),
                       {"hits": [_hit(), _hit(start_quote="nobody said this", title="Lost")]})
    assert answer.status_code == 201, answer.text
    data = answer.json()
    assert (data["profile"], data["default_scope"]) == ("skiimasktimmy", "all")
    [hit] = data["hits"]  # träffen vars citat inte finns slängs
    start = _word("vodA", "The")["start"]
    assert hit["start_s"] == pytest.approx(start)
    assert hit["end_s"] == pytest.approx(start + 15)  # kortare än config.timmy.yaml:s min 15 s
    assert (hit["vod_id"], hit["source_title"], hit["title"], hit["grade"], hit["status"], hit["clip_id"]) == \
        ("vodA", "Titel vodA", "New races revealed", 8, "ny", None)
    assert "walk on air" in hit["quote"]
    user, bot = data["messages"]
    assert (user["role"], user["text"], bot["role"], bot["confirm"]) == \
        ("user", "när de visar nya raser", "assistant", None)
    assert set(bot["sources"]) == {"vodA", "vodB"} and bot["cost_usd"] > 0
    assert "Titel vodC" in bot["text"]  # material utan transkript nämns
    interpret, search = chatapp.script.calls
    assert interpret["model"] == settings.review_model
    assert search["model"] == "claude-sonnet-5-5"  # config.timmy.yaml, llm.model_anthropic
    text = json.dumps(search["messages"], ensure_ascii=False)
    assert "Skyborn" in text and "Dwarf shamans" in text and "Secret words" not in text


def test_latest_searches_only_the_newest_material_with_a_transcript(client, chatapp) -> None:  # noqa: F811
    _new_chat(client, chatapp, _search(), {"hits": []})
    text = json.dumps(chatapp.script.calls[1]["messages"], ensure_ascii=False)
    assert "Dwarf shamans" in text and "Skyborn" not in text


def test_material_outside_the_campaign_is_ignored(client, chatapp) -> None:  # noqa: F811
    _new_chat(client, chatapp, _search(sources=["other", "vodA"]), {"hits": [_hit(vod="other")]})
    text = json.dumps(chatapp.script.calls[1]["messages"], ensure_ascii=False)
    assert "Skyborn" in text and "Secret words" not in text
    assert chatapp.script.calls and client.get("/campaign/chats/1").json()["hits"] == []


def test_a_long_hit_is_cut_to_max_and_duplicates_merge(client, chatapp, settings) -> None:  # noqa: F811
    set_profile(settings, active=True, search={"default_scope": "all"})
    long = _hit(start_quote="In this video", end_quote="next time friends", start_s=0, end_s=50, grade=6)
    same = _hit(start_quote="In this video", end_quote="next time friends", start_s=0, end_s=50, grade=9,
                title="Better")
    data = _new_chat(client, chatapp, _search(), {"hits": [long, same]}).json()
    [hit] = data["hits"]
    assert (hit["title"], hit["grade"], hit["start_s"], hit["end_s"]) == ("Better", 9, 0.0, 45.0)


def test_a_new_search_replaces_new_hits_but_keeps_chosen_ones(client, chatapp, settings) -> None:  # noqa: F811
    _new_chat(client, chatapp, _search(sources=["vodA"]),
              {"hits": [_hit(), _hit(start_quote="In this video", end_quote="weather today", start_s=0, end_s=10,
                                     title="Weather")]})
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE chat_hits SET status = 'gjord', clip_id = 7 WHERE title = 'Weather'")
    chatapp.script.answers += [_search(sources=["vodA"]),
                               {"hits": [_hit(start_quote="Thanks for watching", end_quote="friends",
                                              start_s=40, end_s=50, title="Outro")]}]
    data = client.post("/campaign/chats/1/messages", json={"message": "fler sådana"}).json()
    assert sorted(h["title"] for h in data["hits"]) == ["Outro", "Weather"]
    assert len(data["messages"]) == 4
    # Claude minns samtalet: tolkningen får de tidigare meddelandena
    assert "när de visar nya raser" in json.dumps(chatapp.script.calls[2]["messages"], ensure_ascii=False)


def test_refine_changes_the_hits_with_one_cheap_call(client, chatapp, settings) -> None:  # noqa: F811
    _new_chat(client, chatapp, _search(sources=["vodA"]),
              {"hits": [_hit(), _hit(start_quote="Thanks for watching", end_quote="friends", start_s=40, end_s=50,
                                     title="Outro")]})
    first = client.get("/campaign/chats/1").json()["hits"]
    keep = next(h for h in first if h["title"] == "New races revealed")
    chatapp.script.answers.append({"action": "refine", "reply": "Bara raserna, och den börjar senare.",
                                   "hits": [{"id": keep["id"], "start_quote": "the Skyborn",
                                             "end_quote": "walk on air", "title": "Skyborn", "reason": "r",
                                             "grade": 7}]})
    data = client.post("/campaign/chats/1/messages", json={"message": "bara raserna, kortare"}).json()
    [hit] = data["hits"]
    assert (hit["id"], hit["title"], hit["grade"]) == (keep["id"], "Skyborn", 7)
    assert hit["start_s"] == pytest.approx(_word("vodA", "the", 1)["start"])
    assert len(chatapp.script.calls) == 3  # bara tolkningen, ingen ny sökning
    assert chatapp.script.calls[2]["model"] == settings.review_model
    assert data["messages"][-1]["text"] == "Bara raserna, och den börjar senare."


def test_an_answer_is_only_text(client, chatapp) -> None:  # noqa: F811
    data = _new_chat(client, chatapp, {"action": "answer", "reply": "Vilken video menar du?"}, message="hm").json()
    assert data["messages"][-1]["text"] == "Vilken video menar du?" and data["hits"] == []
    assert len(chatapp.script.calls) == 1


def test_an_expensive_search_asks_first(client, chatapp, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(chat, "CONFIRM_USD", 0.0)
    data = _new_chat(client, chatapp, _search(sources=["vodA"])).json()
    confirm = data["messages"][-1]["confirm"]
    assert confirm["sources"] == ["vodA"] and confirm["cost_usd"] > 0 and confirm["minutes"] == pytest.approx(1.0)
    assert data["hits"] == [] and len(chatapp.script.calls) == 1
    chatapp.script.answers.append({"hits": [_hit()]})
    data = client.post("/campaign/chats/1/messages", json={"confirm": True}).json()
    assert len(data["hits"]) == 1 and len(chatapp.script.calls) == 2
    assert data["messages"][-1]["confirm"] is None
    again = client.post("/campaign/chats/1/messages", json={"confirm": True})
    assert again.status_code == 400 and "confirm" in again.json()["detail"]


def test_budget_stop_gives_402_and_saves_nothing(client, chatapp, monkeypatch) -> None:  # noqa: F811
    def full(self):
        raise BudgetExceeded("Dagstaket är nått.")

    monkeypatch.setattr(Budget, "check", full)
    answer = _new_chat(client, chatapp, _search())
    assert answer.status_code == 402 and "Dagstaket" in answer.json()["detail"]
    assert client.get("/campaign/profiles/skiimasktimmy/chats").json() == []


def test_budget_stop_in_an_existing_chat_adds_no_message(client, chatapp, monkeypatch) -> None:  # noqa: F811
    _new_chat(client, chatapp, {"action": "answer", "reply": "Hej."})
    monkeypatch.setattr(Budget, "check", lambda self: (_ for _ in ()).throw(BudgetExceeded("Taket.")))
    assert client.post("/campaign/chats/1/messages", json={"message": "leta"}).status_code == 402
    assert len(client.get("/campaign/chats/1").json()["messages"]) == 2


@pytest.mark.parametrize("message", ["", "   ", "x" * 1001])
def test_empty_or_long_message_gives_400(client, chatapp, message) -> None:  # noqa: F811
    assert _new_chat(client, chatapp, message=message).status_code == 400
    assert chatapp.script.calls == []


def test_409_while_a_run_or_a_chat_search_is_going(client, chatapp) -> None:  # noqa: F811
    client.app.state.active_run_id = 5
    answer = _new_chat(client, chatapp, _search())
    assert answer.status_code == 409 and "project" in answer.json()["detail"]
    client.app.state.active_run_id = None
    with chat.lock:
        assert _new_chat(client, chatapp, _search()).status_code == 409
    assert chatapp.script.calls == []


def test_chats_are_listed_newest_first_with_a_short_title(client, chatapp) -> None:  # noqa: F811
    _new_chat(client, chatapp, {"action": "answer", "reply": "a"}, message="första " + "x" * 80)
    _new_chat(client, chatapp, _search(sources=["vodA"]), {"hits": [_hit()]}, message="andra")
    items = client.get("/campaign/profiles/skiimasktimmy/chats").json()
    assert [(i["title"], i["hits"]) for i in items] == [("andra", 1), (("första " + "x" * 80)[:60], 0)]
    assert set(items[0]) == {"id", "title", "created", "updated", "hits"}
    assert client.get("/campaign/chats/99").status_code == 404
    assert client.get("/campaign/profiles/finnsinte/chats").status_code == 404


# ---------- klipp av träffar ----------


def _chat_with_hits(client, chatapp, *hits) -> list[dict]:  # noqa: F811
    _new_chat(client, chatapp, _search(sources=["vodA", "vodB"]), {"hits": list(hits)})
    return client.get("/campaign/chats/1").json()["hits"]


def test_a_local_source_without_video_gives_a_problem(client, chatapp) -> None:  # noqa: F811
    [hit] = _chat_with_hits(client, chatapp, _hit())
    answer = client.post("/campaign/chats/1/clips", json={"hit_ids": [hit["id"]]})
    assert answer.status_code == 400
    [hit] = client.get("/campaign/chats/1").json()["hits"]
    assert (hit["status"], hit["problem"]) == ("ny", "The video no longer exists.")


def test_unknown_hits_give_400(client, chatapp) -> None:  # noqa: F811
    _chat_with_hits(client, chatapp, _hit())
    assert client.post("/campaign/chats/1/clips", json={"hit_ids": [999]}).status_code == 400
    assert client.post("/campaign/chats/1/clips", json={"hit_ids": []}).status_code == 400


def test_a_twitch_vod_without_video_downloads_only_the_part(client, chatapp, settings, monkeypatch) -> None:  # noqa: F811
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    vod = "2926877904"
    SEGMENTS[vod] = [(100.0, 110.0, "The new races are the Skyborn and they can walk on air")]
    _write_transcript(settings, vod)
    chatapp.log.add_run(9, vod, "skiimasktimmy", "Bodycam", title="Stream", duration_s=4000)
    _new_chat(client, chatapp, _search(sources=[vod]), {"hits": [_hit(vod=vod, start_s=100, end_s=110)]})
    [hit] = client.get("/campaign/chats/1").json()["hits"]
    state = SimpleNamespace()

    def twitchdl(cmd, on_progress):
        state.cmd = cmd
        out = cmd[cmd.index("--output") + 1]
        make_clip(Path(out), seconds=1)

    def cut(mp4, transcript, picks, run_dir, cfg):
        state.cut = (mp4, [(r.pick.start_offset_s, r.pick.end_offset_s) for r in picks],
                     transcript.segments[0].start)
        return []

    monkeypatch.setattr(download, "_run_twitchdl", twitchdl)
    monkeypatch.setattr(download, "_resolve_quality", lambda vod_id, quality: quality)  # frågar annars Twitch
    monkeypatch.setattr(pipeline, "cut_all_clips", cut)
    monkeypatch.setattr(pipeline, "finish", lambda *a, **k: [])
    for _ in range(9):  # körningarna 1-9 finns i clipsmiths databas, så chattens körning blir nyare än 9
        _run("done", vod)
    answer = client.post("/campaign/chats/1/clips", json={"hit_ids": [hit["id"]]})
    assert answer.status_code == 202, answer.text
    cmd = state.cmd
    assert cmd[cmd.index("--start") + 1] == "00:01:35" and cmd[cmd.index("--end") + 1] == "00:02:00"
    part, times, first_segment = state.cut
    assert times == [(pytest.approx(hit["start_s"] - 95), pytest.approx(hit["end_s"] - 95))]
    assert first_segment == pytest.approx(5.0)  # transkriptet flyttas lika mycket som delen
    assert not part.exists()  # delen tas bort efteråt
    del SEGMENTS[vod]
    # Chattens körning gör inget nytt kort: projektet för VOD:en är den gamla körningen.
    run_id = answer.json()["run_id"]
    assert run_id == 10
    assert (chatapp.log.run(run_id)["origin"], chatapp.log.run(run_id)["project_id"]) == ("chat", None)
    assert [r["run_id"] for r in client.get("/campaign/runs").json() if r["vod_id"] == vod] == [9]
    assert client.get(f"/campaign/runs/{run_id}").json()["origin"] == "chat"


def test_a_chat_run_for_an_own_file_lands_in_the_campaigns_project(client, chatapp, settings,  # noqa: F811
                                                                    monkeypatch) -> None:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    (settings.work_dir / "vodA" / "vodA.mp4").write_bytes(b"video")
    [hit] = _chat_with_hits(client, chatapp, _hit())
    for run_id in range(1, 5):  # körningarna i fixturen finns också i clipsmiths databas, som i appen
        assert _run("done", "vodA") == run_id
    seen = {}
    monkeypatch.setattr(pipeline, "cut_picks", lambda mp4, transcript, picks, run_dir, cfg: seen.update(dir=run_dir))
    monkeypatch.setattr(pipeline, "finish", lambda *a, **k: [])
    answer = client.post("/campaign/chats/1/clips", json={"hit_ids": [hit["id"]]})
    assert answer.status_code == 202, answer.text
    run_id = answer.json()["run_id"]
    assert run_id != 1 and seen["dir"] == settings.out_dir / "vodA" / f"run_{run_id}"  # en egen mapp
    project_id = chatapp.log.run(1)["project_id"]
    assert project_id is not None
    assert (chatapp.log.run(run_id)["origin"], chatapp.log.run(run_id)["project_id"]) == ("chat", project_id)
    assert [r["run_id"] for r in client.get("/campaign/runs").json() if r["vod_id"] == "vodA"] == [1]
    [project] = [p for p in client.get("/campaign/projects").json() if p["profile"] == "skiimasktimmy"]
    assert project["id"] == project_id
    assert {f["vod_id"]: f["run_id"] for f in project["files"]} == {"vodA": 1, "vodB": 2, "vodC": 3}


@needs_subtitles
def test_hits_become_clips_to_review(client, chatapp, settings, monkeypatch) -> None:  # noqa: F811
    from campaign import finish

    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.anthropic_api_key = ""  # ingen bildgranskning i testet
    make_clip(settings.work_dir / "vodA" / "vodA.mp4", size="1920x1080", seconds=60)
    hits = _chat_with_hits(client, chatapp, _hit(),
                           _hit(start_quote="Thanks for watching", end_quote="friends", start_s=40, end_s=50,
                                title="Outro"))
    answer = client.post("/campaign/chats/1/clips", json={"hit_ids": [h["id"] for h in hits]})
    assert answer.status_code == 202, answer.text
    run_id = answer.json()["run_id"]
    meta = chatapp.log.run(run_id)
    assert (meta["vod_id"], meta["profile"], meta["focus"]) == ("vodA", "skiimasktimmy", "när de visar nya raser")
    assert client.get(f"/campaign/runs/{run_id}").json()["status"] == "done"
    assert client.app.state.active_run_id is None
    done = {h["title"]: h for h in client.get("/campaign/chats/1").json()["hits"]}
    for title, hit in done.items():
        assert hit["status"] == "gjord", hit
        clip = chatapp.log.get(hit["clip_id"])
        assert clip["status"] in ("renderad", "underkand") and clip["title"] == title
        assert clip["start_s"] == pytest.approx(hit["start_s"])
        raw = settings.out_dir / "vodA" / f"run_{run_id}" / Path(clip["file"]).name
        assert words_path(raw).exists()
        length = hit["end_s"] - hit["start_s"]
        assert probe(clip["file"])["duration"] == pytest.approx(length, abs=1.0)


def test_material_video_streams_with_range(client, chatapp, settings) -> None:  # noqa: F811
    make_clip(settings.work_dir / "vodA" / "vodA.mp4", seconds=1)
    answer = client.get("/campaign/material/vodA/video", headers={"Range": "bytes=0-99"})
    assert answer.status_code == 206 and len(answer.content) == 100
    make_clip(settings.work_dir / "loose" / "loose.mp4", seconds=1)
    assert client.get("/campaign/material/loose/video").status_code == 404  # ingen kampanj har den
    assert client.get("/campaign/material/vodB/video").status_code == 404  # ingen video


# ---------- modeller utan tvingat verktyg, och Claude nere ----------


def test_brain_can_let_the_model_choose_the_tool(settings) -> None:
    """Sonnet 5.5 tar inte tool_choice tool. Utan tvång läses svaret ur verktyget eller ur JSON i texten."""
    claude = Script({"hits": []})
    b = Brain(settings, budget_from_settings(settings), client=claude)
    assert b.ask(purpose="p", model="m", system="s", content="c", schema={}, force_tool=False) == {"hits": []}
    assert claude.calls[0]["tool_choice"] == {"type": "auto"}
    text = SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                           content=[SimpleNamespace(type="text", text='Här: {"hits": [1]} klart')])
    claude.create = lambda **kwargs: text
    assert b.ask(purpose="p", model="m", system="s", content="c", schema={}, force_tool=False) == {"hits": [1]}
    claude.create = Script({"a": 1}).create
    assert b.ask(purpose="p", model="m", system="s", content="c", schema={}) == {"a": 1}  # som förut


def test_the_search_does_not_force_the_tool(client, chatapp) -> None:  # noqa: F811
    _new_chat(client, chatapp, _search(), {"hits": []})
    interpret, search = chatapp.script.calls
    assert interpret["tool_choice"] == {"type": "tool", "name": "svar"}
    assert search["tool_choice"] == {"type": "auto"}


def test_claude_down_gives_502_and_saves_nothing(client, chatapp) -> None:  # noqa: F811
    import anthropic
    import httpx

    def down(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    chatapp.script.create = down
    answer = _new_chat(client, chatapp)
    assert answer.status_code == 502 and "Claude" in answer.json()["detail"]
    assert client.get("/campaign/profiles/skiimasktimmy/chats").json() == []
    assert not chat.lock.locked()


# ---------- nyckeln och materialet kontrolleras före Claude ----------


def test_without_the_claude_key_the_chat_says_so_and_asks_nothing(client, settings, monkeypatch) -> None:  # noqa: F811
    _write_transcript(settings, "vodA")
    ClipLog(settings.data_dir / "klipp.db").add_run(1, "vodA", "skiimasktimmy", None, title="Titel", duration_s=60)
    settings.anthropic_api_key = ""
    calls = []
    monkeypatch.setattr(Brain, "ask", lambda self, **kw: calls.append(kw))
    response = client.post("/campaign/profiles/skiimasktimmy/chats", json={"message": "roliga dödsfall"})
    assert response.status_code == 400
    assert "Claude key is missing" in response.json()["detail"] and "Settings" in response.json()["detail"]
    assert calls == []
    assert client.get("/campaign/profiles/skiimasktimmy/chats").json() == []


def test_without_material_with_a_transcript_the_chat_says_so_and_asks_nothing(client, chatapp, settings) -> None:  # noqa: F811
    for vod in ("vodA", "vodB"):
        (settings.work_dir / vod / "transcript.json").unlink()
    response = _new_chat(client, chatapp, _search())
    assert response.status_code == 400
    assert "transcript" in response.json()["detail"]
    assert chatapp.script.calls == []
