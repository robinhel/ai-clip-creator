"""Virala klipp: hitta var i VOD:en de kommer ifrån och jämför med appens klipp. Inga riktiga anrop görs."""

from __future__ import annotations

import json

import yaml

from campaign import viral
from campaign.facit import compare
from campaign.transcribe import transcribe_vod
from clipsmith.models.transcript import Segment, Transcript, Word

from .conftest import make_clip, needs_ffmpeg

TEXT = (
    "hej och välkommen till kvällens stream vi kör bodycam igen med hela gänget nu ska vi se om "
    "vi klarar banan utan att dö den här gången jag har en plan som faktiskt kan fungera om alla "
    "lyssnar på mig och inte springer iväg som förra veckan okej vi går in genom dörren till "
    "vänster och sen upp för trappan där borde nyckeln ligga"
)
VOD = TEXT.split()


def vod_words(tokens: list[str], t0: float = 1000.0, step: float = 2.5) -> list[Word]:
    """VOD-ord med tider som inte liknar klippets, så att fel bit syns direkt."""
    return [
        Word(start=t0 + i * step, end=t0 + i * step + 1.0, word=w, probability=1.0)
        for i, w in enumerate(tokens)
    ]


def clip_words(tokens: list[str]) -> list[Word]:
    return [
        Word(start=i * 0.4, end=i * 0.4 + 0.3, word=w, probability=1.0)
        for i, w in enumerate(tokens)
    ]


def spans(pieces: list[dict]) -> list[tuple[float, float]]:
    return [(p["start"], p["end"]) for p in pieces]


def t(i: int) -> float:
    return 1000.0 + i * 2.5


def test_a_piece_in_the_middle_gives_its_start_and_end() -> None:
    pieces = viral.find(clip_words(VOD[20:32]), vod_words(VOD))
    assert spans(pieces) == [(t(20), t(31) + 1.0)]
    assert not viral.cold_open(pieces)


def test_a_cut_in_the_middle_gives_two_pieces_in_order() -> None:
    pieces = viral.find(clip_words(VOD[10:18] + VOD[40:48]), vod_words(VOD))
    assert spans(pieces) == [(t(10), t(17) + 1.0), (t(40), t(47) + 1.0)]
    assert not viral.cold_open(pieces)


def test_a_cold_open_keeps_the_clips_order() -> None:
    pieces = viral.find(clip_words(VOD[50:58] + VOD[12:22]), vod_words(VOD))
    assert spans(pieces) == [(t(50), t(57) + 1.0), (t(12), t(21) + 1.0)]
    assert viral.cold_open(pieces)


def test_words_that_are_not_in_the_vod_give_no_match() -> None:
    other = "helt andra ord som ingen sa i streamen alls någon gång".split()
    assert viral.find(clip_words(other), vod_words(VOD)) == []
    # fyra ord i rad räcker inte: vanliga fraser finns i varje lång stream
    assert viral.find(clip_words(["blabla", *VOD[30:34], "blabla"]), vod_words(VOD)) == []


def test_the_same_phrase_twice_picks_the_place_with_most_words_in_a_row() -> None:
    vod = [
        "nu",
        "kör",
        "vi",
        "en",
        "gång",
        "till",
        "då",
        *VOD[:30],
        "nu",
        "kör",
        "vi",
        "en",
        "gång",
        "till",
        "och",
        "vinner",
        "den",
        "här",
        *VOD[30:],
    ]
    pieces = viral.find(
        clip_words("Nu kör vi en gång till, och vinner den här!".split()), vod_words(vod)
    )
    assert spans(pieces) == [(t(37), t(46) + 1.0)]


def test_single_misheard_words_do_not_split_a_piece() -> None:
    clip = VOD[20:36]
    clip[5] = "bananer"  # felhört ord
    clip.insert(10, "eh")  # ett extra ord som inte kom med i VOD:ens transkript
    pieces = viral.find(clip_words(clip), vod_words(VOD))
    assert spans(pieces) == [(t(20), t(35) + 1.0)]


def test_compare_gives_found_with_differences_and_missed() -> None:
    moments = [
        {"time": 1015.0, "start": 1010.0, "end": 1020.0, "note": "a"},
        {"time": 3000.0, "start": 2995.0, "end": 3005.0, "note": "b"},
    ]
    result = compare(moments, [(1004.0, 1023.5), (2000.0, 2030.0)])
    assert result[0]["found"] and result[0]["clip"] == (1004.0, 1023.5)
    assert (result[0]["start_diff"], result[0]["end_diff"]) == (-6.0, 3.5)
    assert not result[1]["found"]


def test_report_says_found_missed_and_the_total() -> None:
    results = [
        {
            "title": "a",
            "views": 12000,
            "url": "https://x/1",
            "pieces": [{"start": 1010.0, "end": 1020.0, "hits": 8}],
        },
        {
            "title": "b",
            "views": None,
            "url": "https://x/2",
            "pieces": [{"start": 2995.0, "end": 3005.0, "hits": 8}],
        },
        {"title": "c", "views": None, "url": "https://x/3", "pieces": []},
    ]
    picks = [{"start": 1004.0, "end": 1023.5}, {"start": 2000.0, "end": 2030.0}]
    text = "\n".join(viral.report(results, picks))
    assert "börjar 6,0 s tidigare" in text and "slutar 3,5 s senare" in text
    assert "missade" in text and "Ingen träff i VOD:en" in text
    assert text.endswith(
        "Appen hittade 1 av 2 virala klipp från den här VOD:en (1 fanns inte i den)."
    )


def test_a_common_phrase_from_another_stream_gives_no_match() -> None:
    other = (
        "helt andra ord om något annat".split()
        + VOD[30:36]
        + "och sen pratar han om helt andra saker".split()
    )
    assert viral.find(clip_words(other), vod_words(VOD)) == []


def test_a_cold_open_counts_as_found_when_the_app_covers_the_main_piece() -> None:
    pieces = [
        {"start": 1100.0, "end": 1110.0, "hits": 8},
        {"start": 1000.0, "end": 1030.0, "hits": 14},
    ]
    text = "\n".join(
        viral.report([{"title": "a", "pieces": pieces}], [{"start": 995.0, "end": 1035.0}])
    )
    assert "Appen hittade 1 av 1 virala klipp." in text


@needs_ffmpeg
def test_a_second_run_does_not_call_berget_again_and_facit_keeps_earlier_clips(
    settings, budget, capsys
) -> None:
    vod_dir = settings.work_dir / "777"
    vod_dir.mkdir(parents=True)
    words = vod_words(VOD)
    transcript = Transcript("777", "sv", [Segment(words[0].start, words[-1].end, TEXT, words)])
    (vod_dir / "transcript.json").write_text(transcript.to_json(), encoding="utf-8")
    mp4 = make_clip(settings.root / "klipp" / "viralt.mp4", size="320x180", seconds=3)
    calls = []

    def post(audio: bytes, fields: dict) -> dict:
        calls.append(fields)
        said = VOD[20:30] if len(calls) == 1 else VOD[40:50]
        clip = [
            {"word": w, "start": 1.0 + i * 0.2, "end": 1.1 + i * 0.2, "score": 0.9}
            for i, w in enumerate(said)
        ]
        return {
            "segments": [{"start": 0.0, "end": 4.0, "text": " ".join(said)}],
            "words": clip,
        }

    def transcribe(path, out):
        return transcribe_vod(
            path, "klipp", out, post=post, model="m", language="sv", budget=budget
        )

    for _ in range(2):
        viral.run("777", [str(mp4.parent)], settings.work_dir, transcribe)
    assert len(calls) == 1
    facit = yaml.safe_load((vod_dir / "viral" / "facit.yaml").read_text(encoding="utf-8"))
    assert facit["vod"] == "777" and facit["moments"][0]["start"] == t(20)
    meta = json.loads(next((vod_dir / "viral").glob("*/meta.json")).read_text(encoding="utf-8"))
    assert meta["source"] == "fil"
    other = make_clip(settings.root / "annat" / "andra.mp4", size="320x180", seconds=3)
    viral.run("777", [str(other)], settings.work_dir, transcribe)
    assert len(calls) == 2
    facit = yaml.safe_load((vod_dir / "viral" / "facit.yaml").read_text(encoding="utf-8"))
    assert sorted(m["start"] for m in facit["moments"]) == [t(20), t(40)]
    assert "picks.json saknas" in capsys.readouterr().out


def test_a_txt_file_is_a_list_of_sources(tmp_path, monkeypatch) -> None:
    lista = tmp_path / "utvalda.txt"
    lista.write_text("# från samla lista\nhttps://www.tiktok.com/@a/video/1\n\nhttps://www.tiktok.com/@b/video/2\n",
                     encoding="utf-8")
    seen = []
    monkeypatch.setattr(viral, "_ytdlp", lambda url, folder: seen.append(url) or [{"url": url}])
    clips = viral.collect(str(lista), tmp_path / "viral")
    assert seen == ["https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@b/video/2"]
    assert len(clips) == 2


def test_a_bad_line_in_a_txt_file_is_skipped_and_the_next_line_is_read(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "annan.txt").write_text("https://www.tiktok.com/@c/video/3\n", encoding="utf-8")
    lista = tmp_path / "utvalda.txt"
    lista.write_text(f"{tmp_path / 'finns-inte.mp4'}\n{tmp_path / 'annan.txt'}\nhttps://www.tiktok.com/@a/video/1\n",
                     encoding="utf-8")
    seen = []
    monkeypatch.setattr(viral, "_ytdlp", lambda url, folder: seen.append(url) or [{"url": url}])
    viral.collect(str(lista), tmp_path / "viral")
    assert seen == ["https://www.tiktok.com/@a/video/1"]
    out = capsys.readouterr().out
    assert "finns-inte.mp4, hoppar över den" in out and "en .txt-lista i en .txt-lista läses inte" in out
