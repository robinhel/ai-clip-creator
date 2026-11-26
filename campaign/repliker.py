"""Roliga repliker: Haiku läser transkriptet i bitar på cirka 40 000 tecken och hittar repliker som en främling
förstår och skulle dela, med ett betyg 1-10. De bästa blir kandidater, så att lugna roliga moment når Claude
också när signalerna (chatt, hype-ord, ljud, tittarklipp) ger dem låg poäng. Antalet kandidater till Claude
ändras inte.

Varför bitar: på Macen gav ett enda anrop för 3 timmar (45 470 tokens in) bara 2 repliker, men 1 timme
(10 503 tokens) gav 15. En bit på 40 000 tecken är cirka 10 000 tokens.

Kostnad med Haiku (0,10 dollar per miljon tokens in, 0,50 ut, priser.yaml). Systemtexten (cirka 300 tokens)
skickas i varje anrop, och svaret är högst cirka 900 tokens (15 repliker):
- 1 timme, cirka 10 500 tokens och 1-2 anrop: in cirka 11 000 tokens = 0,0011, ut högst 1 800 = 0,0009,
  alltså högst cirka 0,002 dollar.
- 3 timmar, cirka 45 500 tokens och 5 anrop: in cirka 47 000 = 0,0047, ut högst 4 500 = 0,0023,
  alltså högst cirka 0,007 dollar.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import replace
from pathlib import Path

from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment
from clipsmith.selection.selector import WINDOW_POST_S, WINDOW_PRE_S, _spread_candidates

from .brain import Brain
from .budget import BudgetExceeded

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_LINES = 15  # per bit
CHUNK_CHARS = 40_000  # ungefär 10 000 tokens; ett stort anrop ger för få repliker
SLOTS = 5  # högst så många av Claudes kandidater blir repliker
VERSION = 2  # repliker.json med annan version (eller ingen) frågas om
SOURCE = "funny_line"

SYSTEM = """You read one part of the transcript of a livestream. Find the funniest, most absurd or most quotable \
lines: lines that a stranger who never watched the stream understands right away and would share with a friend. \
Calm, dry and deadpan lines count as much as loud ones. Do not pick hype, shouting, cheering or game callouts, \
and do not pick lines that need earlier parts of the stream to be funny.
Each transcript line starts with its time in seconds: [seconds] text.
Return up to 15 lines, the best first; give 15 when the part has that many worth a look. For each: \
t = the seconds of the transcript line where the quote is said, quote = the words exactly as written in the \
transcript, why = a few words on why a stranger would share it, funny = how funny it is to a stranger from 1 \
(barely) to 10 (would share it right away)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "maxItems": MAX_LINES,
            "items": {
                "type": "object",
                "properties": {
                    "t": {"type": "number"},
                    "quote": {"type": "string"},
                    "why": {"type": "string"},
                    "funny": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["t", "quote", "why", "funny"],
            },
        }
    },
    "required": ["lines"],
}


def enabled() -> bool:
    """KLIPP_REPLIKER=0 stänger av sökningen."""
    return os.environ.get("KLIPP_REPLIKER", "1").strip() != "0"


def _parts(rows: list[tuple[str, float, float]]) -> list[list[tuple[str, float, float]]]:
    """Raderna (text, start, slut) i så få lika stora bitar som möjligt, var och en högst cirka CHUNK_CHARS
    tecken (en rad till)."""
    total = sum(len(row[0]) + 1 for row in rows)
    count = math.ceil(total / CHUNK_CHARS)
    parts: list[list[tuple[str, float, float]]] = [[] for _ in range(count)]
    used = 0
    for row in rows:  # raden hamnar i biten där den börjar
        parts[min(count - 1, used * count // total)].append(row)
        used += len(row[0]) + 1
    return [part for part in parts if part]


def _funny(value) -> int:
    try:
        return max(0, min(10, int(value)))
    except (TypeError, ValueError):
        return 0


def find_lines(
    transcript_segments: list[Segment],
    brain: Brain,
    model: str = MODEL,
    problems: list[str] | None = None,
) -> list[dict]:
    """Haikus repliker, {t, quote, why, funny}, fördelade mellan bitarna: först varje bits bästa, sedan varje
    bits näst bästa och så vidare, och inom samma varv på funny (högst först) och sedan tid. Då avgör inte en
    enda bits betyg alla platser. Ett anrop per
    bit på cirka CHUNK_CHARS tecken, högst MAX_LINES repliker per bit. En träff utanför bitens tider kastas.

    Ett fel i en bit hoppar över biten och skrivs i `problems`. Är budgeten slut (BudgetExceeded) görs inga
    fler anrop; replikerna från bitarna före finns kvar."""
    rows = [
        (f"[{int(s.start)}] {s.text.strip()}", s.start, s.end)
        for s in transcript_segments
        if s.text.strip()
    ]
    parts = _parts(rows) if rows else []
    found: list[tuple[int, dict]] = []  # (varv, replik)
    for number, part in enumerate(parts, 1):
        low, high = int(part[0][1]), max(row[2] for row in part)  # t är radens hela sekunder
        try:
            answer = brain.ask(
                purpose="repliker",
                model=model,
                system=SYSTEM,
                content="\n".join(row[0] for row in part),
                schema=SCHEMA,
                max_tokens=2000,
            )
        except Exception as error:  # budgeten, nätverket, nyckeln: biten hoppas över
            text = f"Bit {number} av {len(parts)}: {type(error).__name__}: {error}"
            log.warning("Haiku: %s", text)
            if problems is not None:
                problems.append(text)
            if isinstance(error, BudgetExceeded):
                break
            continue
        mine: list[dict] = []
        for line in (answer.get("lines") or [])[:MAX_LINES]:
            try:
                t = float(line["t"])
            except (KeyError, TypeError, ValueError):
                continue
            if low <= t <= high:
                mine.append(
                    {
                        "t": t,
                        "quote": str(line.get("quote") or "").strip(),
                        "why": str(line.get("why") or "").strip(),
                        "funny": _funny(line.get("funny")),
                    }
                )
        mine.sort(key=lambda line: (-line["funny"], line["t"]))
        found += list(enumerate(mine))
    found.sort(key=lambda item: (item[0], -item[1]["funny"], item[1]["t"]))
    return [line for _, line in found]


def _covers(c: CandidateMoment, t: float, pre_s: float = WINDOW_PRE_S) -> bool:
    """Ligger t i transkriptet som Claude får för kandidaten (selector.py: pre_s före, 30 s efter)?"""
    return c.t_center - pre_s <= t <= c.t_center + WINDOW_POST_S


def _place(t: float, taken: list[CandidateMoment], gap_s: float, pre_s: float = WINDOW_PRE_S) -> float | None:
    """En mittpunkt minst gap_s från de tagna, vars fönster har repliken: repliken själv, annars precis utanför
    en tagen kandidats avstånd. None om ingen finns."""
    options = [t] + [c.t_center + side * (gap_s + 0.5) for c in taken for side in (-1, 1)]
    fits = [
        p
        for p in options
        if p >= 0
        and p - pre_s <= t <= p + WINDOW_POST_S
        and all(abs(p - c.t_center) >= gap_s for c in taken)
    ]
    return min(fits, key=lambda p: abs(p - t), default=None)


def _mark(c: CandidateMoment, line: dict) -> None:
    if SOURCE not in c.sources:
        c.sources.append(SOURCE)
    c.reasons.append(
        f'Hint (Haiku): possible funny line at {line["t"]:.0f}s: "{line["quote"]}". The viewers\' signals may '
        "point at a different moment; clip that moment if it is stronger."
    )


def merge(
    candidates: list[CandidateMoment],
    lines: list[dict],
    gap_s: float,
    max_n: int = 20,
    slots: int = SLOTS,
    pre_s: float = WINDOW_PRE_S,
) -> list[CandidateMoment]:
    """Kandidaterna med replikerna inlagda. Högst `slots` av de `max_n` platserna efter selector.py:s spridning
    går till repliker; resten fylls av signalerna som förut.

    Så sätts poängen: signalernas topp `max_n - slots` efter spridningen (samma _spread_candidates) är skyddad,
    och golvet är den lägsta poängen bland dem. En replik som redan finns i Claudes fönster för en skyddad
    kandidat (`pre_s` före, 30 s efter mitten, som Claude får det) lägger bara citatet på den kandidaten. Annars
    lyfts närmaste kandidat inom `gap_s` från repliken (eller en ny läggs till) och poängen blir minst golvet.
    Har den lyftas fönster redan repliken och ligger dess mitt minst `gap_s` från alla tagna, behåller den sin
    mitt: chatten och replikerna kommer efter händelsen, och fönstret före mitten har situationen. Annars flyttas
    mitten till repliken (eller precis utanför `gap_s` från en skyddad, så att repliken ändå är i fönstret). Listan
    sorteras med de skyddade först, sedan replikerna och sist resten vid lika poäng. Eftersom de lyfta ligger
    minst `gap_s` från alla skyddade och från varandra och ingen annan kandidat kommer före dem i ordningen
    (de med högre poäng är redan spärrade av de skyddade), tar spridningen de skyddade, sedan replikerna och
    fyller resten med signalerna. Signalernas topp `max_n - slots` finns alltså alltid kvar.
    """
    out = [replace(c, sources=list(c.sources), reasons=list(c.reasons)) for c in candidates]
    kept = _spread_candidates(
        sorted(out, key=lambda c: c.score, reverse=True), gap_s, max_n - slots
    )
    floor = min((c.score for c in kept), default=0.0)
    taken = list(kept)
    added: list[CandidateMoment] = []
    for line in lines:  # den bästa först (find_lines sorterar på funny)
        t = float(line["t"])
        near = [c for c in taken if _covers(c, t, pre_s)]
        if near:
            _mark(min(near, key=lambda c: abs(c.t_center - t)), line)
            continue
        if len(added) >= slots:
            continue
        ids = {id(c) for c in taken}
        free = [c for c in out if id(c) not in ids and abs(c.t_center - t) < gap_s]
        lifted = min(free, key=lambda c: abs(c.t_center - t), default=None)
        if (
            lifted is not None
            and t <= lifted.t_center
            and _covers(lifted, t, pre_s)
            and all(abs(lifted.t_center - c.t_center) >= gap_s for c in taken)
        ):
            pos = lifted.t_center  # repliken före mitten: fönstret har den och det som kommer efter
        else:
            pos = _place(t, taken, gap_s, pre_s)
        if pos is None:
            continue
        if lifted is None:
            lifted = CandidateMoment(t_center=pos, score=floor, sources=[], reasons=[])
            out.append(lifted)
        lifted.t_center, lifted.score = pos, max(lifted.score, floor)
        _mark(lifted, line)
        taken.append(lifted)
        added.append(lifted)
    order = {id(c): 0 for c in kept} | {id(c): 1 for c in added}
    return sorted(out, key=lambda c: (-c.score, order.get(id(c), 2)))


def _cached(path: Path, span: list[float]) -> list[dict] | None:
    """Sparade repliker om de har den här versionen och gäller hela den här delen av VOD:en, annars None.
    En rad utan t eller citat hoppas över."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            return None
        if data["span"][0] <= span[0] and data["span"][1] >= span[1]:
            return [
                line
                for line in data["lines"]
                if isinstance(line, dict)
                and isinstance(line.get("t"), (int, float))
                and line.get("quote")
            ]
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        pass
    return None


def span_of(segments: list[Segment]) -> list[float]:
    return [min(s.start for s in segments), max(s.end for s in segments)]


def save(path: Path, span: list[float], lines: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {"version": VERSION, "span": span, "lines": lines}, ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )


def add_lines(
    candidates: list[CandidateMoment],
    segments: list[Segment],
    path: Path,
    brain: Brain,
    gap_s: float,
    slots: int = SLOTS,
    max_n: int = 20,
    pre_s: float = WINDOW_PRE_S,
) -> list[CandidateMoment]:
    """Kandidaterna med Haikus repliker (merge, med `pre_s` s transkript före mitten som Claude får). Svaret sparas i `path` och används igen vid nästa körning, utan
    nya anrop. Ett fel från Haiku (också budgeten) blir en rad i loggen och körningen går vidare med replikerna
    från bitarna som gick bra. Svaret sparas bara när alla bitar gick bra, så att nästa körning frågar igen."""
    if not segments:
        return candidates
    span = span_of(segments)
    lines = _cached(path, span)
    if lines is None:
        problems: list[str] = []
        lines = find_lines(segments, brain, problems=problems)
        if problems:
            log.warning(
                "Haiku gav repliker för en del av VOD:en (%d fel). Körningen går vidare.",
                len(problems),
            )
        else:
            save(path, span, lines)
    lines = [line for line in lines if int(span[0]) <= line["t"] <= span[1]]  # t i hela sekunder
    return merge(candidates, lines, gap_s, max_n=max_n, slots=slots, pre_s=pre_s)
