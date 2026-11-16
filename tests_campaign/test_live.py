"""Liveklipp: chatten läses under streamen, och en topp blir en körning på en del av VOD:en."""

from __future__ import annotations

from campaign.live import PeakFinder, chat_for_part, message, part_id, part_window, vod_seconds
from clipsmith.models.chat import ChatLog


def _calm(finder: PeakFinder, start: float, end: float, every: float = 5.0) -> list[float]:
    """Lugn chatt: ett meddelande var femte sekund. Returnerar topparna."""
    peaks = []
    t = start
    while t < end:
        peak = finder.add(message(t, "nice", "a"))
        if peak is not None:
            peaks.append(peak)
        t += every
    return peaks


def _burst(finder: PeakFinder, start: float, count: int, text: str = "KEKW") -> list[float]:
    """Chatten exploderar: count meddelanden på fem sekunder."""
    peaks = []
    for i in range(count):
        peak = finder.add(message(start + 5.0 * i / count, text, f"u{i}"))
        if peak is not None:
            peaks.append(peak)
    return peaks


def test_a_burst_after_calm_chat_is_one_peak() -> None:
    finder = PeakFinder(multiplier=3.0)
    assert _calm(finder, 0, 600) == []
    peaks = _burst(finder, 600, 30)
    assert len(peaks) == 1  # en topp, inte en per meddelande
    assert 600 <= peaks[0] <= 605


def test_peaks_closer_than_a_minute_become_one() -> None:
    finder = PeakFinder(multiplier=3.0)
    _calm(finder, 0, 600)
    assert len(_burst(finder, 600, 30)) == 1
    _calm(finder, 606, 640)
    assert _burst(finder, 640, 30) == []  # 40 s efter förra toppen
    _calm(finder, 646, 720)
    assert len(_burst(finder, 720, 30)) == 1  # två minuter efter: en ny topp


def test_no_peak_before_the_chat_has_a_baseline() -> None:
    """De första minuterna finns inget normalt läge att jämföra med."""
    finder = PeakFinder(multiplier=3.0)
    assert _burst(finder, 10, 30) == []


def test_steady_busy_chat_is_not_a_peak() -> None:
    finder = PeakFinder(multiplier=3.0)
    assert _calm(finder, 0, 900, every=0.5) == []


def test_clip_command_is_a_peak_on_its_own() -> None:
    finder = PeakFinder(multiplier=3.0)
    _calm(finder, 0, 600)
    assert finder.add(message(601, "!clip", "fan")) == 601


def test_message_counts_hype_emotes_and_clip_command() -> None:
    msg = message(12.5, "KEKW KEKW OMEGALUL nice", "a")
    assert (msg.time_in_seconds, msg.hype_emote_count, msg.is_clip_command) == (12.5, 2, False)
    assert message(1, "!Clip that", "b").is_clip_command


def test_vod_seconds_counts_from_when_the_vod_started() -> None:
    # VOD:en startade 20:00:00 UTC. Meddelandet kom 21:02:03,5 UTC.
    sent_ms = 1_789_000_000_000
    import datetime as dt

    started = dt.datetime.fromtimestamp(sent_ms / 1000 - 3723.5, dt.UTC).isoformat().replace("+00:00", "Z")
    assert abs(vod_seconds(sent_ms, started) - 3723.5) < 0.01


def test_part_starts_before_the_peak_and_ends_after() -> None:
    assert part_window(3723.0) == (3633.0, 3753.0)  # 90 s före och 30 s efter
    assert part_window(40.0) == (0.0, 70.0)  # aldrig före VOD:ens början
    assert part_id("2927104410", 3) == "2927104410_live_3"


def test_chat_for_the_part_has_times_from_the_part_start() -> None:
    msgs = [message(t, "KEKW", "a") for t in (100.0, 3640.0, 3700.0, 3760.0)]
    log = chat_for_part(msgs, 3633.0, 3753.0, "2927104410_live_3")
    assert log.video_id == "2927104410_live_3"
    assert [m.time_in_seconds for m in log.messages] == [7.0, 67.0]
    assert ChatLog.from_json(log.to_json()).messages[0].hype_emote_count == 1
