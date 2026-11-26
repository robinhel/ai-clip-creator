"""Smak ur virala klipp (campaign/taste.py): mätning, smakfil och skills. Inga riktiga AI- eller nätverksanrop."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from clipsmith.models.transcript import Segment, Transcript, Word

from campaign import locate, taste, viral
from campaign.brain import Brain
from tests_campaign.conftest import FakeClient, make_clip, needs_ffmpeg

MEASURES = {
    "format": {"duration": 21.5, "width": 1080, "height": 1920},
    "shots": {"cuts_per_min": 24.0, "first_cut_at": 1.2},
    "faces": {"on_screen_pct": 40.0},
    "text": {"captions": {"words_per_caption_median": 3.0, "y_centre_pct": 72.0}},
    "speech": {"words": 50, "wpm_overall": 180},
    "text_events": [
        {"start": 0.0, "end": 2.0, "text": "NO WAY", "kind": "title"},
        {"start": 5.0, "end": 6.0, "text": "hej", "kind": "caption"},
    ],
}
PICKED = {
    "length_s": 21.5,
    "first_cut_s": 1.2,
    "cuts_per_min": 24.0,
    "caption_words": 3.0,
    "caption_position": "bottom",
    "hook_text": True,
    "has_face": True,
    "words_per_min": 180,
}
ANSWER = {
    "rules": ["Cut within the first second.", "Big caption on the reaction."],
    "summary_en": "Short loud reactions with fast cuts win.",
}


@pytest.fixture
def root(settings, monkeypatch) -> Path:
    monkeypatch.setenv("KLIPP_ROOT", str(settings.root))
    return settings.root


@pytest.fixture
def fake_measure(monkeypatch):
    """Låtsas köra measure.py: kontrollerar arbetsmappen och skriver fixturen där."""
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        wd = Path(cmd[3])
        assert (wd / "video.mp4").read_bytes() == b"video"
        assert json.loads((wd / "meta.json").read_text())["platform"] == "file"
        (wd / "measures.json").write_text(json.dumps(MEASURES))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(taste.subprocess, "run", run)
    return calls


def _video(folder: Path) -> Path:
    video = folder / "klipp.mp4"
    video.write_bytes(b"video")
    return video


def test_measure_plockar_falten(tmp_path, fake_measure):
    video = _video(tmp_path)

    assert taste.measure(video) == PICKED
    cmd = fake_measure[0]
    assert cmd[2].endswith("reverse_video/measure.py")
    assert cmd[cmd.index("--stems") + 1] == "off"
    assert video.exists()  # originalet rörs inte


def test_measure_fel_ger_runtimeerror(tmp_path, monkeypatch):
    monkeypatch.setattr(
        taste.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom")
    )
    with pytest.raises(RuntimeError, match="boom"):
        taste.measure(_video(tmp_path))


def test_measure_for_lang_tid_ger_runtimeerror(tmp_path, monkeypatch):
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(taste.subprocess, "run", slow)
    with pytest.raises(RuntimeError):
        taste.measure(_video(tmp_path))


# Händelsen ur captions.json för ett TikTok-klipp från Content Rewards (blizzard_nordics): en fast text i
# caption-bandet hela klippet. Den är längre än 6 s, så captions_precise ger inga caption-mått, och händelsen
# saknar "frame".
BAND_TEXT = {
    "start": 0.0, "end": 11.567, "text": "World of Warcraft: Forever looks beautiful.", "kind": "caption",
    "words": 6, "spoken_at": None, "builds_on_previous": False,
    "box": {"x0": 0.2778, "x1": 0.7167, "y0": 0.2025, "y1": 0.2479, "h": 0.0235, "lines": 2},
}


@needs_ffmpeg
def test_measure_text_colours_klarar_bandtext_utan_frame(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    make_clip(tmp_path / "wd" / "video.mp4", size="36x64", seconds=1)  # measure.py läser videon när den laddas
    monkeypatch.setattr(sys, "argv", ["measure.py", str(tmp_path / "wd")])
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("reverse_video_measure", taste.MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = tmp_path / "frame.jpg"
    cv2.imwrite(str(frame), np.zeros((64, 36, 3), np.uint8))

    assert module.text_colours([dict(BAND_TEXT)], [frame]) is None


def _save_clips(root: Path) -> None:
    work = root / "work" / "2000"
    work.mkdir(parents=True)
    (work / "candidates.json").write_text(
        json.dumps([{"t_center": 112.0, "score": 50}, {"t_center": 500.0, "score": 90}])
    )
    viral.save(
        "testkamp",
        [
            {"id": "c", "outlier": 1.0, "measured": True, "taste": PICKED},
            {"id": "d", "outlier": None},
            {
                "id": "a",
                "outlier": 10.0,
                "measured": False,
                "source": "content_rewards",
                "video_url": "https://cdn.contentrewards.com/v/a.mp4",
                "location": {"vod_id": "2000", "start_s": 100.0, "end_s": 130.0},
            },
            {
                "id": "b",
                "outlier": 5.0,
                "measured": True,
                "location": None,
                "taste": dict(PICKED, length_s=30.0, hook_text=False),
            },
        ],
    )


@pytest.fixture
def no_place(monkeypatch):
    """Låtsas att stället i strömmen inte hittas, så att den sparade platsen står kvar."""
    monkeypatch.setattr(locate, "locate", lambda clip, vod_transcripts=None, words=None: None)


@pytest.mark.usefixtures("root", "fake_http", "no_place")
def test_analyse_bygger_smakfilen(settings, budget, fake_measure):
    root = settings.root
    _save_clips(root)
    brain = Brain(settings, budget, client=FakeClient(ANSWER))
    purposes: list[str] = []
    ask = brain.ask

    def spy(**kwargs):
        purposes.append(kwargs["purpose"])
        return ask(**kwargs)

    brain.ask = spy  # type: ignore[method-assign]

    result = taste.analyse("testkamp", top_n=2, brain=brain)

    assert purposes == ["smak"]
    assert brain._client.messages.calls[0]["model"] == settings.review_model
    assert result["profile"] == "testkamp" and result["clip_ids"] == ["a", "b"]
    assert result["numbers"] == {
        "length_s": pytest.approx(25.75),
        "first_cut_s": 1.2,
        "cuts_per_min": 24.0,
        "caption_words": 3.0,
        "hook_text_share": 0.5,
        "payoff_s": 12.0,  # starkaste ögonblicket inom klippet ligger 12 s in
    }
    assert result["rules"] == ANSWER["rules"] and result["summary_en"] == ANSWER["summary_en"]
    saved = json.loads((root / "klippdata" / "smak" / "testkamp.json").read_text())
    assert saved == result and taste.load_taste("testkamp") == result
    clip_a = next(c for c in viral.load("testkamp") if c["id"] == "a")
    assert clip_a["measured"] is True and clip_a["taste"] == PICKED
    assert len(fake_measure) == 1  # bara a mättes


CDN = "https://cdn.contentrewards.com/v/a.mp4"


@pytest.fixture
def fake_http(monkeypatch):
    """Låtsas-httpx: svarar b"video" och sparar adresserna som hämtades."""
    urls: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"vid"
            yield b"eo"

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *exc):
            return False

    def stream(method, url, **kwargs):
        assert method == "GET" and not kwargs.get("follow_redirects")
        urls.append(url)
        return Stream()

    monkeypatch.setattr(taste.httpx, "stream", stream)
    return urls


@pytest.fixture
def fake_locate(monkeypatch):
    """Låtsas hitta stället: sparar filen locate fick och om den fanns då."""
    calls: list[tuple[Path, bool]] = []

    def run(clip, vod_transcripts=None, words=None):
        path = Path(clip["path"])
        calls.append((path, path.exists()))
        return {"vod_id": "3000", "start_s": 10.0, "end_s": 31.5, "method": "transcript"}

    monkeypatch.setattr(locate, "locate", run)
    return calls


@pytest.mark.usefixtures("root", "fake_measure")
def test_analyse_laddar_ner_bara_fran_contentrewards_cdn(settings, budget, fake_http, fake_locate):
    viral.save(
        "testkamp",
        [
            {"id": "cdn", "source": "content_rewards", "outlier": 9.0, "video_url": CDN},
            {"id": "http", "outlier": 8.0, "video_url": "http://cdn.contentrewards.com/b.mp4"},
            {"id": "annan", "outlier": 7.0, "video_url": "https://evil.example.com/c.mp4"},
            {"id": "lur", "outlier": 6.0, "video_url": "https://cdn.contentrewards.com.evil.io/d"},
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", top_n=4, brain=brain)

    assert fake_http == [CDN]
    assert result["clip_ids"] == ["cdn"]
    assert len(fake_locate) == 1 and fake_locate[0][1] is True  # filen fanns när locate körde
    assert not fake_locate[0][0].exists()  # och är raderad efteråt
    saved = {c["id"]: c for c in viral.load("testkamp")}
    assert saved["cdn"]["measured"] is True and saved["cdn"]["taste"] == PICKED
    assert saved["cdn"]["location"]["vod_id"] == "3000"
    assert not saved["annan"].get("measured")


def _vod_transcript(root: Path, vod_id: str, seconds: int) -> None:
    """Ett ord per sekund: ordet heter w<sekund>."""
    words = [
        Word(start=float(i), end=i + 0.5, word=f" w{i}", probability=0.9) for i in range(seconds)
    ]
    transcript = Transcript(
        video_id=vod_id,
        language="en",
        segments=[Segment(start=0.0, end=float(seconds), text="", words=words)],
    )
    path = root / "work" / vod_id / "transcript.json"
    path.parent.mkdir(parents=True)
    path.write_text(transcript.to_json(), encoding="utf-8")


def test_analyse_ger_haiku_twitch_avvikarnas_ord(root, settings, budget, fake_measure):
    _vod_transcript(root, "2000", 400)
    place = {"vod_id": "2000", "start_s": 100.0, "end_s": 130.0, "method": "vod_offset"}
    long_place = {"vod_id": "2000", "start_s": 200.0, "end_s": 300.0, "method": "vod_offset"}
    viral.save(
        "testkamp",
        [
            {
                "id": "t1",
                "source": "twitch",
                "title": "Granaten",
                "outlier": 20.0,
                "views_per_day": 900.0,
                "location": place,
            },
            {
                "id": "t2",
                "source": "twitch",
                "title": "Långt",
                "outlier": 15.0,
                "views_per_day": 500.0,
                "location": long_place,
            },
            {
                "id": "t3",
                "source": "twitch",
                "title": "Utan VOD",
                "outlier": 12.0,
                "views_per_day": 400.0,
                "location": None,
            },
            {
                "id": "b",
                "source": "content_rewards",
                "outlier": 5.0,
                "measured": True,
                "taste": PICKED,
            },
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", top_n=3, brain=brain)

    assert result["clip_ids"] == ["b"]  # Twitch-klippen mäts inte och tar inga platser
    sent = json.loads(brain._client.messages.calls[0]["messages"][0]["content"])
    moments = sent["moments"]
    assert [m["title"] for m in moments] == ["Granaten", "Långt", "Utan VOD"]
    assert moments[0]["views_per_day"] == 900.0
    assert moments[0]["words"] == " ".join(f"w{i}" for i in range(100, 131))
    assert len(moments[1]["words"].split()) == 60
    assert "words" not in moments[2]
    assert not any(c.get("measured") for c in viral.load("testkamp") if c["source"] == "twitch")


@pytest.mark.usefixtures("root")
def test_analyse_bara_twitch_ger_smakfil_och_skills(settings, budget, tmp_path):
    place = {"vod_id": "2000", "start_s": 100.0, "end_s": 130.0, "method": "vod_offset"}
    viral.save(
        "testkamp",
        [
            {
                "id": "t1",
                "source": "twitch",
                "title": "Granaten",
                "outlier": 20.0,
                "views_per_day": 900.0,
                "location": place,
            }
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", brain=brain)

    assert result["clip_ids"] == []
    assert set(result["numbers"].values()) == {None}
    assert result["rules"] == ANSWER["rules"] and result["summary_en"] == ANSWER["summary_en"]
    sent = json.loads(brain._client.messages.calls[0]["messages"][0]["content"])
    assert sent["clips"] == [] and sent["moments"][0]["title"] == "Granaten"
    paths = taste.write_skills("testkamp", tmp_path / "skills")
    editor = next(p for p in paths if p.parent.name == "testkamp-clip-editor").read_text()
    assert "No editing data yet" in editor and "Cut within the first second." in editor


@pytest.mark.usefixtures("root")
def test_analyse_ger_haiku_kallan_for_varje_klipp_och_ogonblick(settings, budget):
    tiktok = "https://www.tiktok.com/@a/video/1"
    youtube = "https://www.youtube.com/watch?v=x"
    insta = "https://www.instagram.com/reel/y"
    clips = [
        {"id": tiktok, "url": tiktok, "source": "content_rewards", "outlier": 9.0, "measured": True},
        {"id": youtube, "url": youtube, "source": "content_rewards", "outlier": 8.0, "measured": True},
        {"id": insta, "url": insta, "source": "link", "outlier": 7.0, "measured": True},
        {"id": "t1", "url": "https://clips.twitch.tv/t1", "source": "twitch", "title": "Granaten",
         "outlier": 6.0, "views_per_day": 900.0, "location": None},
    ]
    viral.save("testkamp", [{**c, "taste": PICKED} if c.get("measured") else c for c in clips])
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    taste.analyse("testkamp", brain=brain)

    call = brain._client.messages.calls[0]
    sent = json.loads(call["messages"][0]["content"])
    assert [(c["source"], c["platform"]) for c in sent["clips"]] == [
        ("content_rewards", "tiktok"),
        ("content_rewards", "youtube"),
        ("link", "instagram"),
    ]
    assert [(m["source"], m["platform"]) for m in sent["moments"]] == [("twitch", "twitch")]
    assert "content_rewards" in call["system"] and "source" in call["system"]


@pytest.mark.usefixtures("root")
def test_analyse_utan_matta_klipp_och_ogonblick_ger_valueerror(settings, budget):
    viral.save("testkamp", [{"id": "d", "source": "content_rewards", "outlier": None}])
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    with pytest.raises(ValueError):
        taste.analyse("testkamp", brain=brain)
    assert brain._client.messages.calls == []


@pytest.mark.usefixtures("root", "fake_measure", "fake_http")
def test_analyse_fortsatter_nar_locate_kastar(settings, budget, monkeypatch):
    seen: list[Path] = []

    def broken(clip, vod_transcripts=None, words=None):
        seen.append(Path(clip["path"]))
        raise RuntimeError("whisper kraschade")

    monkeypatch.setattr(locate, "locate", broken)
    viral.save(
        "testkamp",
        [
            {
                "id": "cdn",
                "source": "content_rewards",
                "outlier": 9.0,
                "video_url": CDN,
                "location": None,
            }
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", brain=brain)

    assert result["clip_ids"] == ["cdn"]
    saved = viral.load("testkamp")[0]
    assert saved["measured"] is True and saved["location"] is None
    assert len(seen) == 1 and not seen[0].exists()


CDN2 = "https://cdn.contentrewards.com/v/b.mp4"


@pytest.mark.usefixtures("root", "fake_http", "fake_locate")
@pytest.mark.parametrize("error", [RuntimeError("measure.py misslyckades"), ValueError("trasig json"), OSError("disk")])
def test_analyse_hoppar_over_klipp_som_inte_gar_att_mata(settings, budget, monkeypatch, error):
    measured: list[str] = []

    def measure(video):
        if not measured:
            measured.append("fel")
            raise error
        measured.append("ok")
        return PICKED

    monkeypatch.setattr(taste, "measure", measure)
    viral.save(
        "testkamp",
        [
            {"id": "trasig", "source": "content_rewards", "outlier": 9.0, "video_url": CDN},
            {"id": "hel", "source": "content_rewards", "outlier": 8.0, "video_url": CDN2},
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", brain=brain)

    assert measured == ["fel", "ok"] and result["clip_ids"] == ["hel"]
    saved = {c["id"]: c for c in viral.load("testkamp")}
    assert not saved["trasig"].get("measured") and saved["hel"]["measured"] is True


@pytest.mark.usefixtures("root", "fake_http", "fake_locate")
def test_analyse_laddar_listan_pa_nytt_fore_sparning(settings, budget, monkeypatch):
    """En insamling under mätningen ska inte skrivas över av analysens gamla lista."""
    locked: list[bool] = []
    save = viral.save

    def measure(video):
        clips = viral.load("testkamp")
        clips[0]["views"] = 123
        save("testkamp", [*clips, {"id": "ny", "source": "twitch", "outlier": 1.0}])
        return PICKED

    def spy(profile, clips):
        locked.append(viral.LOCK.locked())
        save(profile, clips)

    viral.save(
        "testkamp",
        [{"id": "cdn", "source": "content_rewards", "outlier": 9.0, "views": 5, "video_url": CDN}],
    )
    monkeypatch.setattr(taste, "measure", measure)
    monkeypatch.setattr(viral, "save", spy)
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    taste.analyse("testkamp", brain=brain)

    saved = {c["id"]: c for c in viral.load("testkamp")}
    assert set(saved) == {"cdn", "ny"}
    assert saved["cdn"]["views"] == 123 and saved["cdn"]["measured"] is True
    assert saved["cdn"]["taste"] == PICKED and saved["cdn"]["location"]["vod_id"] == "3000"
    assert locked == [True]


@pytest.mark.usefixtures("root")
def test_analyse_tar_med_lankar_och_cr_klipp_utan_datum(settings, budget):
    def clip(clip_id, source, outlier, views):
        return {"id": clip_id, "source": source, "outlier": outlier, "views": views,
                "measured": True, "taste": PICKED}

    viral.save(
        "testkamp",
        [
            clip("datum", "content_rewards", 9.0, 10),
            clip("utan_lite", "content_rewards", None, 50),
            clip("utan_mycket", "content_rewards", None, 1000),
            clip("lank", "link", None, 5),
        ],
    )
    brain = Brain(settings, budget, client=FakeClient(ANSWER))

    result = taste.analyse("testkamp", top_n=2, brain=brain)

    assert result["clip_ids"] == ["datum", "utan_mycket", "lank"]


def test_write_skills_standard_ar_repots_rot(root, tmp_path, monkeypatch):
    assert taste.SKILLS_DIR == Path(taste.__file__).resolve().parents[1] / ".agents" / "skills"
    smak = root / "klippdata" / "smak"
    smak.mkdir(parents=True)
    (smak / "testkamp.json").write_text(json.dumps({"rules": ["R"], "summary_en": "S"}))
    monkeypatch.setattr(taste, "SKILLS_DIR", tmp_path / "repo-skills")
    monkeypatch.chdir(tmp_path)

    paths = taste.write_skills("testkamp")

    assert all(p.is_relative_to(tmp_path / "repo-skills") for p in paths)


def test_smakfilen_skrivs_atomiskt(root, monkeypatch):
    moved: list[tuple[Path, Path]] = []
    replace = os.replace

    def spy(src, dst):
        moved.append((Path(src), Path(dst)))
        replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)

    taste._save_taste("testkamp", {"rules": ["R"]})

    path = root / "klippdata" / "smak" / "testkamp.json"
    assert len(moved) == 1 and moved[0][1] == path and moved[0][0].parent == path.parent
    assert sorted(path.parent.iterdir()) == [path]  # ingen tmp-fil kvar
    assert taste.load_taste("testkamp") == {"rules": ["R"]}


def test_taste_text_tom_utan_fil(root, settings):
    assert taste.load_taste("testkamp") is None
    assert taste.taste_text("testkamp", settings.data_dir) == ""


def test_taste_text_tom_vid_trasig_smakfil(settings):
    smak = settings.data_dir / "smak"
    smak.mkdir(parents=True)
    (smak / "testkamp.json").write_text("{trasig", encoding="utf-8")

    assert taste.taste_text("testkamp", settings.data_dir) == ""


def test_taste_text_kort_med_fil(root, settings):
    smak = root / "klippdata" / "smak"
    smak.mkdir(parents=True)
    data = {
        "profile": "testkamp",
        "numbers": {
            "length_s": 25.0,
            "first_cut_s": 1.2,
            "cuts_per_min": 24.0,
            "caption_words": 3.0,
            "hook_text_share": 0.5,
            "payoff_s": 12.0,
        },
        "rules": [f"Rule number {i} that is fairly long to fill the space." for i in range(30)],
        "summary_en": "Short loud reactions with fast cuts win.",
    }
    (smak / "testkamp.json").write_text(json.dumps(data))

    text = taste.taste_text("testkamp", settings.data_dir)

    assert "Short loud reactions" in text and "25" in text
    assert 0 < len(text) <= 600


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n")[1])


def test_write_skills_tva_filer(root, tmp_path):
    smak = root / "klippdata" / "smak"
    smak.mkdir(parents=True)
    (smak / "testkamp.json").write_text(
        json.dumps(
            {
                "profile": "testkamp",
                "numbers": {"length_s": 25.0, "payoff_s": 12.0},
                "rules": ["Cut within the first second."],
                "summary_en": "Fast cuts win.",
            }
        )
    )

    paths = taste.write_skills("testkamp", tmp_path / "skills")

    assert [p.parent.name for p in paths] == ["testkamp-moment-scout", "testkamp-clip-editor"]
    for path in paths:
        meta = _frontmatter(path)
        assert meta["name"] == path.parent.name and path.name == "SKILL.md"
        assert 0 < len(meta["description"]) <= 1024
        assert "Cut within the first second." in path.read_text()
