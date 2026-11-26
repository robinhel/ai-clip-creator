"""Roliga repliker: Haiku läser hela transkriptet och hittar repliker som en främling förstår och skulle dela.
De blir kandidater, så att lugna roliga moment når Claude också när signalerna (chatt, hype-ord, ljud,
tittarklipp) ger dem låg poäng. Antalet kandidater till Claude ändras inte."""

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

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_LINES = 15
CHUNK_CHARS = 240_000  # ungefär 60 000 tokens; ett transkript på 3 timmar ryms i ett anrop
SOURCE = "funny_line"

SYSTEM = """You read the full transcript of a livestream. Find the funniest, most absurd or most quotable lines: \
lines that a stranger who never watched the stream understands right away and would share with a friend. \
Calm, dry and deadpan lines count as much as loud ones. Do not pick hype, shouting, cheering or game callouts, \
and do not pick lines that need earlier parts of the stream to be funny.
Each transcript line starts with its time in seconds: [seconds] text.
Return at most 15 lines, the best first. For each: t = the seconds of the transcript line where the quote is said, \
quote = the words exactly as written in the transcript, why = a few words on why a stranger would share it."""

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
                },
                "required": ["t", "quote", "why"],
            },
        }
    },
    "required": ["lines"],
}


def enabled() -> bool:
    """KLIPP_REPLIKER=0 stänger av sökningen."""
    return os.environ.get("KLIPP_REPLIKER", "1").strip() != "0"


def _parts(rows: list[str]) -> list[list[str]]:
    """Raderna i så få lika stora bitar som möjligt, var och en högst cirka CHUNK_CHARS tecken (en rad till)."""
    total = sum(len(row) + 1 for row in rows)
    count = math.ceil(total / CHUNK_CHARS)
    parts: list[list[str]] = [[] for _ in range(count)]
    used = 0
    for row in rows:  # raden hamnar i biten där den börjar
        parts[min(count - 1, used * count // total)].append(row)
        used += len(row) + 1
    return [part for part in parts if part]


def find_lines(transcript_segments: list[Segment], brain: Brain, model: str = MODEL) -> list[dict]:
    """Haikus repliker, {t, quote, why}, högst 15 per anrop. Ett anrop per bit av transkriptet (en bit upp till
    cirka 60 000 tokens). En träff utanför transkriptets tider kastas."""
    rows = [f"[{int(s.start)}] {s.text.strip()}" for s in transcript_segments if s.text.strip()]
    if not rows:
        return []
    low = min(s.start for s in transcript_segments)
    high = max(s.end for s in transcript_segments)
    found: list[dict] = []
    for part in _parts(rows):
        answer = brain.ask(
            purpose="repliker",
            model=model,
            system=SYSTEM,
            content="\n".join(part),
            schema=SCHEMA,
            max_tokens=2000,
        )
        for line in (answer.get("lines") or [])[:MAX_LINES]:
            try:
                t = float(line["t"])
            except (KeyError, TypeError, ValueError):
                continue
            if low <= t <= high:
                found.append(
                    {
                        "t": t,
                        "quote": str(line.get("quote") or "").strip(),
                        "why": str(line.get("why") or "").strip(),
                    }
                )
    return found


def _covers(c: CandidateMoment, t: float) -> bool:
    """Ligger t i transkriptet som Claude får för kandidaten (selector.py: 60 s före, 30 s efter)?"""
    return c.t_center - WINDOW_PRE_S <= t <= c.t_center + WINDOW_POST_S


def _place(t: float, taken: list[CandidateMoment], gap_s: float) -> float | None:
    """En mittpunkt minst gap_s från de tagna, vars fönster har repliken: repliken själv, annars precis utanför
    en tagen kandidats avstånd. None om ingen finns."""
    options = [t] + [c.t_center + side * (gap_s + 0.5) for c in taken for side in (-1, 1)]
    fits = [
        p
        for p in options
        if p >= 0
        and p - WINDOW_PRE_S <= t <= p + WINDOW_POST_S
        and all(abs(p - c.t_center) >= gap_s for c in taken)
    ]
    return min(fits, key=lambda p: abs(p - t), default=None)


def _mark(c: CandidateMoment, line: dict) -> None:
    if SOURCE not in c.sources:
        c.sources.append(SOURCE)
    why = f" ({line['why']})" if line.get("why") else ""
    c.reasons.append(f'Funny line at {line["t"]:.0f}s: "{line["quote"]}"{why}')


def merge(
    candidates: list[CandidateMoment],
    lines: list[dict],
    gap_s: float,
    max_n: int = 20,
    slots: int = 5,
) -> list[CandidateMoment]:
    """Kandidaterna med replikerna inlagda. Högst `slots` av de `max_n` platserna efter selector.py:s spridning
    går till repliker; resten fylls av signalerna som förut.

    Så sätts poängen: signalernas topp `max_n - slots` efter spridningen (samma _spread_candidates) är skyddad,
    och golvet är den lägsta poängen bland dem. En replik som redan finns i Claudes fönster för en skyddad
    kandidat (60 s före, 30 s efter mitten) lägger bara citatet på den kandidaten. Annars lyfts närmaste
    kandidat inom `gap_s` från repliken (eller en ny läggs till): mitten flyttas till repliken (eller precis
    utanför `gap_s` från en skyddad, så att repliken ändå är i fönstret) och poängen blir minst golvet. Listan
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
    for line in lines:  # Haikus ordning: den bästa först
        t = float(line["t"])
        near = [c for c in taken if _covers(c, t)]
        if near:
            _mark(min(near, key=lambda c: abs(c.t_center - t)), line)
            continue
        if len(added) >= slots:
            continue
        pos = _place(t, taken, gap_s)
        if pos is None:
            continue
        ids = {id(c) for c in taken}
        free = [c for c in out if id(c) not in ids and abs(c.t_center - t) < gap_s]
        lifted = min(free, key=lambda c: abs(c.t_center - t), default=None)
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
    """Sparade repliker om de gäller hela den här delen av VOD:en, annars None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["span"][0] <= span[0] and data["span"][1] >= span[1]:
            return list(data["lines"])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return None


def add_lines(
    candidates: list[CandidateMoment],
    segments: list[Segment],
    path: Path,
    brain: Brain,
    gap_s: float,
) -> list[CandidateMoment]:
    """Kandidaterna med Haikus repliker (merge). Svaret sparas i `path` och används igen vid nästa körning, utan
    nytt anrop. Ett fel från Haiku (också budgeten) blir en rad i loggen, och körningen går vidare utan repliker."""
    if not segments:
        return candidates
    span = [min(s.start for s in segments), max(s.end for s in segments)]
    lines = _cached(path, span)
    if lines is None:
        try:
            lines = find_lines(segments, brain)
        except (
            Exception
        ) as error:  # budgeten, nätverket, nyckeln: körningen klarar sig utan repliker
            log.warning(
                "Haiku hittade inga repliker (%s: %s). Körningen går vidare utan dem.",
                type(error).__name__,
                error,
            )
            return candidates
        path.write_text(
            json.dumps({"span": span, "lines": lines}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    lines = [line for line in lines if span[0] <= line["t"] <= span[1]]
    return merge(candidates, lines, gap_s)
