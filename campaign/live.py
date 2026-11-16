"""Liveklipp: chatten läses medan streamen pågår. När chatten exploderar blir stunden före toppen en körning
på en kort del av VOD:en, som Twitch spelar in under tiden.

Här finns kärnan utan nätverk: toppar i chatten, var toppen ligger i VOD:en, och delen som ska hämtas.
"""

from __future__ import annotations

import datetime as dt
import socket
from collections import deque
from collections.abc import Iterator

from clipsmith.models.chat import HYPE_EMOTES, ChatLog, ChatMessage

WINDOW_S = 15.0  # samma fönster som toppsökningen i clipsmith (density_window_s)
BASELINE_S = 600.0  # det normala läget är chatten de senaste tio minuterna
WARMUP_S = 120.0  # före det finns inget normalt läge att jämföra med
GAP_S = 60.0  # toppar närmare än en minut blir samma klipp
PRE_S = 90.0  # delen börjar så här långt före toppen
POST_S = 30.0  # och slutar så här långt efter
AUTHOR_CAP = 3.0  # en avsändare väger högst så här mycket per fönster: en spammare är inte hela chatten
SURGE_GAIN = 0.5  # ökningen mot förra fönstret räknas extra: en topp är en plötslig ökning

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
    väger minst `multiplier` gånger det normala, eller ett !clip från en tittare. Varje avsändare har ett tak
    per fönster, och en ökning mot förra fönstret räknas extra."""

    def __init__(self, multiplier: float = 3.0) -> None:
        self.multiplier = multiplier
        self._recent: deque[tuple[ChatMessage, float]] = deque()  # meddelandet och dess vikt efter taket
        self._first: float | None = None
        self._last_peak: float | None = None

    def add(self, msg: ChatMessage) -> float | None:
        """Lägg till ett meddelande. Returnerar toppens tid i sekunder när en ny topp börjar, annars None."""
        t = msg.time_in_seconds
        if self._first is None:
            self._first = t
        # Avsändarens vikt de senaste 15 sekunderna räknas mot taket. Det som går över taket väger inget.
        used = sum(w for m, w in self._recent if m.author == msg.author and m.time_in_seconds > t - WINDOW_S)
        self._recent.append((msg, max(0.0, min(_weight(msg), AUTHOR_CAP - used))))
        while self._recent and self._recent[0][0].time_in_seconds < t - BASELINE_S:
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
        window = sum(w for m, w in self._recent if m.time_in_seconds > t - WINDOW_S)
        previous = sum(w for m, w in self._recent if t - 2 * WINDOW_S < m.time_in_seconds <= t - WINDOW_S)
        before = sum(w for m, w in self._recent if m.time_in_seconds <= t - WINDOW_S)
        baseline = max(before * WINDOW_S / min(history, BASELINE_S - WINDOW_S), 1.0)
        score = window + SURGE_GAIN * max(0.0, window - previous)
        return score >= self.multiplier * baseline


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


IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6667


def parse_irc(line: str) -> tuple[str, str, int] | None:
    """(avsändare, text, tid i ms) för ett chattmeddelande från Twitchs IRC, annars None."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        tags = dict(part.partition("=")[::2] for part in raw[1:].split(";"))
    prefix, _, rest = line.partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG" or not prefix.startswith(":"):
        return None
    _, _, text = rest.partition(" :")
    author = prefix[1:].partition("!")[0]
    sent = tags.get("tmi-sent-ts", "")
    return author, text, int(sent) if sent.isdigit() else int(dt.datetime.now(dt.UTC).timestamp() * 1000)


def read_chat(channel: str, timeout_s: float = 300.0) -> Iterator[tuple[str, str, int]]:
    """Läser kanalens chatt live, utan inloggning (justinfan är Twitchs anonyma läsare).
    Ger (avsändare, text, tid i ms) tills anslutningen stängs."""
    with socket.create_connection((IRC_HOST, IRC_PORT), timeout=timeout_s) as conn:
        conn.sendall(b"CAP REQ :twitch.tv/tags\r\nNICK justinfan31415\r\n"
                     + f"JOIN #{channel.lower()}\r\n".encode())
        buffer = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buffer += data
            *lines, buffer = buffer.split(b"\r\n")
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                if line.startswith("PING"):
                    conn.sendall(b"PONG :tmi.twitch.tv\r\n")
                elif (msg := parse_irc(line)) is not None:
                    yield msg
