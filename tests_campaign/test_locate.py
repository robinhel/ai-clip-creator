"""Stället i originalströmmen: Twitch-klippets vod_offset eller klippets ord mot VOD-transkripten."""

from __future__ import annotations

from pathlib import Path

from clipsmith.models.transcript import Segment, Transcript, Word

from campaign import locate

FILLER = "ja okej nu kör vi lite till här och sen får vi se hur det går med allt".split()
QUOTE = "vänta vänta han står bakom muren med granaten och jag har inget liv kvar alls".split()


def _transcript(vod_id: str, words: list[str], step: float = 0.5, start: float = 0.0) -> Transcript:
    ws = [Word(start=start + i * step, end=start + i * step + 0.4, word=f" {w}", probability=0.9)
          for i, w in enumerate(words)]
    return Transcript(video_id=vod_id, language="sv",
                      segments=[Segment(start=ws[0].start, end=ws[-1].end, text=" ".join(words), words=ws)])


def _save(tmp_path: Path, vod_id: str, words: list[str]) -> Path:
    path = tmp_path / "work" / vod_id / "transcript.json"
    path.parent.mkdir(parents=True)
    path.write_text(_transcript(vod_id, words).to_json(), encoding="utf-8")
    return path


def _clip_words(words: list[str], step: float = 0.5, lead: float = 1.0) -> list[dict]:
    return [{"word": w, "start": lead + i * step, "end": lead + i * step + 0.4} for i, w in enumerate(words)]


def test_twitch_klipp_med_vod_offset_ger_platsen():
    clip = {"id": "Abc", "video_id": "2222", "vod_offset": 3600, "duration": 28.5}
    assert locate.from_twitch(clip) == {"vod_id": "2222", "start_s": 3600.0, "end_s": 3628.5,
                                        "method": "vod_offset", "confidence": 1.0}


def test_borttagen_vod_ger_none():
    assert locate.from_twitch({"video_id": "", "vod_offset": 100, "duration": 20}) is None
    assert locate.from_twitch({"video_id": "2222", "vod_offset": None, "duration": 20}) is None


def test_ord_med_sma_skillnader_hittar_ratt_start_bland_liknande_stallen(tmp_path):
    # VOD:en: utfyllnad, en snarlik bit (början av citatet), mer utfyllnad, hela citatet vid ordet 3*len(FILLER)+8.
    near = QUOTE[:5] + "nej det var fel".split()
    vod_words = FILLER * 2 + near + FILLER + QUOTE + FILLER
    true_index = len(FILLER) * 3 + len(near)
    path = _save(tmp_path, "111", vod_words)
    # Klippet: en ord stavat annorlunda, ett ord saknas, skiljetecken och versaler.
    heard = ["Vänta,", "vänta!"] + QUOTE[2:6] + ["granaten,", "och", "jag", "har", "inge", "liv", "kvar", "alls."]
    clip = {"id": "x", "duration": 12.0}
    found = locate.locate(clip, {"111": path}, words=_clip_words(heard))
    assert found is not None
    assert found["vod_id"] == "111"
    assert found["method"] == "transcript"
    assert abs(found["start_s"] - (true_index * 0.5 - 1.0)) <= 2.0
    assert found["end_s"] == found["start_s"] + 12.0
    assert 0.0 < found["confidence"] <= 1.0


def test_klipp_utan_tal_ger_none(tmp_path, monkeypatch):
    path = _save(tmp_path, "111", FILLER + QUOTE)
    video = tmp_path / "klipp.mp4"
    video.write_bytes(b"")
    calls = []

    def fake_whisper(mp4, video_id, out_path, config, **kwargs):
        calls.append(mp4)
        return Transcript(video_id=video_id, language="sv", segments=[])

    monkeypatch.setattr(locate, "transcribe_local", fake_whisper)
    assert locate.locate({"id": "x", "video_url": str(video)}, {"111": path}) is None
    assert calls == [video]


def test_klipp_transkriberas_med_lokal_whisper(tmp_path, monkeypatch):
    path = _save(tmp_path, "111", FILLER + QUOTE)
    video = tmp_path / "klipp.mp4"
    video.write_bytes(b"")
    monkeypatch.setattr(locate, "transcribe_local",
                        lambda mp4, video_id, out_path, config, **kw: _transcript(video_id, QUOTE, start=0.0))
    found = locate.locate({"id": "x", "video_url": str(video), "duration": 10}, {"111": path})
    assert found is not None and found["vod_id"] == "111"
    assert abs(found["start_s"] - len(FILLER) * 0.5) <= 2.0


def test_tva_vodar_valjer_den_som_har_texten(tmp_path):
    other = _save(tmp_path, "111", FILLER * 3)
    right = _save(tmp_path, "222", FILLER + QUOTE + FILLER)
    found = locate.locate({"id": "x"}, {"111": other, "222": right}, words=_clip_words(QUOTE, lead=0.0))
    assert found is not None
    assert found["vod_id"] == "222"
    assert abs(found["start_s"] - len(FILLER) * 0.5) <= 2.0


def test_ingen_traff_ger_none(tmp_path):
    path = _save(tmp_path, "111", FILLER * 3)
    assert locate.locate({"id": "x"}, {"111": path}, words=_clip_words(QUOTE)) is None


def test_for_fa_ord_ger_none(tmp_path):
    path = _save(tmp_path, "111", FILLER + QUOTE)
    assert locate.locate({"id": "x"}, {"111": path}, words=_clip_words(QUOTE[:4])) is None


def test_kandidat_vodar_ar_transkripten_i_work(tmp_path):
    a = _save(tmp_path, "111", FILLER)
    b = _save(tmp_path, "222", FILLER)
    (tmp_path / "work" / "333").mkdir()  # ingen transkribering än
    assert locate.vod_transcripts(tmp_path / "work") == {"111": a, "222": b}
