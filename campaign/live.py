"""Liveklipp: chatten läses medan streamen pågår. När chatten exploderar blir stunden före toppen en körning
på en kort del av VOD:en, som Twitch spelar in under tiden.

Här finns kärnan utan nätverk: toppar i chatten, var toppen ligger i VOD:en, och delen som ska hämtas.
"""

from __future__ import annotations

import datetime as dt
from collections import deque

from clipsmith.models.chat import HYPE_EMOTES, ChatLog, ChatMessage

WINDOW_S = 15.0  # samma fönster som toppsökningen i clipsmith (density_window_s)
BASELINE_S = 600.0  # det normala läget är chatten de senaste tio minuterna
WARMUP_S = 120.0  # före det finns inget normalt läge att jämföra med
GAP_S = 60.0  # toppar närmare än en minut blir samma klipp
PRE_S = 90.0  # delen börjar så här långt före toppen
POST_S = 30.0  # och slutar så här långt efter

# Skickas som focus till Claudes val, så att src/clipsmith inte behöver ändras.
CHAT_LAG_FOCUS = ("Liveklipp från en topp i chatten. Chatten reagerar 5-15 sekunder efter det som händer, "
                  "så händelsen ligger före toppen: börja klippet precis före händelsen, inte vid toppen.")


def message(t: float, text: str, author: str) -> ChatMessage:
    """Ett chattmeddelande, räknat som clipsmith räknar en VOD:s chatt."""
    text = text.strip()
    return ChatMessage(time_in_seconds=float(t), message=text, author=author,
                       is_clip_command=text.lower().startswith("!clip"),
                       hype_emote_count=len(set(text.split()) & HYPE_EMOTES))


def _weight(msg: ChatMessage) -> float:
    return 1.0 + 0.5 * msg.hype_emote_count  # som compute_density_scores: hype-emotes räknas extra


class PeakFinder:
    """Hittar toppar i chatten ett meddelande i taget. En topp är ett fönster på 15 sekunder där chatten
    väger minst `multiplier` gånger det normala, eller ett !clip från en tittare."""

    def __init__(self, multiplier: float = 3.0) -> None:
        self.multiplier = multiplier
        self._recent: deque[ChatMessage] = deque()
        self._first: float | None = None
        self._last_peak: float | None = None

    def add(self, msg: ChatMessage) -> float | None:
        """Lägg till ett meddelande. Returnerar toppens tid i sekunder när en ny topp börjar, annars None."""
        t = msg.time_in_seconds
        if self._first is None:
            self._first = t
        self._recent.append(msg)
        while self._recent and self._recent[0].time_in_seconds < t - BASELINE_S:
            self._recent.popleft()
        if self._last_peak is not None and t - self._last_peak < GAP_S:
            return None
        if msg.is_clip_command or self._is_burst(t):
            self._last_peak = t
            return t
        return None

    def _is_burst(self, t: float) -> bool:
        history = t - self._first - WINDOW_S
        if t - self._first < WARMUP_S or history <= 0:
            return False
        window = sum(_weight(m) for m in self._recent if m.time_in_seconds > t - WINDOW_S)
        before = sum(_weight(m) for m in self._recent if m.time_in_seconds <= t - WINDOW_S)
        baseline = max(before * WINDOW_S / min(history, BASELINE_S - WINDOW_S), 1.0)
        return window >= self.multiplier * baseline


def vod_seconds(sent_ms: int, vod_created_at: str) -> float:
    """Var i VOD:en ett meddelande ligger. sent_ms är chattens tid (tmi-sent-ts), created_at VOD:ens start."""
    started = dt.datetime.fromisoformat(vod_created_at.replace("Z", "+00:00"))
    return sent_ms / 1000 - started.timestamp()


def part_window(peak_s: float) -> tuple[float, float]:
    """Delen av VOD:en som hämtas för en topp: från 90 s före till 30 s efter."""
    return max(0.0, peak_s - PRE_S), peak_s + POST_S


def part_id(vod_id: str, n: int) -> str:
    """Delens id och mapp i work/. VOD:ens egen mapp rörs inte."""
    return f"{vod_id}_live_{n}"


def chat_for_part(messages: list[ChatMessage], start_s: float, end_s: float, video_id: str) -> ChatLog:
    """Chatten under delen, med tider från delens början. Sparas som chat.json, så att toppen räknas."""
    return ChatLog(video_id=video_id, messages=[
        ChatMessage(time_in_seconds=m.time_in_seconds - start_s, message=m.message, author=m.author,
                    is_clip_command=m.is_clip_command, hype_emote_count=m.hype_emote_count)
        for m in messages if start_s <= m.time_in_seconds < end_s])
