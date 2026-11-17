"""Run LLM over each candidate: extract transcript window, pick, clamp duration."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass

from ..llm.base import ClipPick, ClipPicker, LLMUnavailable
from ..models.candidates import CandidateMoment
from ..models.transcript import Transcript, Word
from ..settings import ClipConfig

log = logging.getLogger(__name__)

# Reason of a clip that signals mode chose; campaign.workflow shows such clips without a grade.
SIGNALS_REASON = "Vald av signalerna utan Claude"

# Seconds of transcript to send before/after the candidate center.
WINDOW_PRE_S = 60.0
WINDOW_POST_S = 30.0
# A sentence border further than this from Claude's time is not the one Claude meant.
SNAP_MAX_S = 8.0


@dataclass
class PickResult:
    candidate: CandidateMoment
    pick: ClipPick
    webcam_rect: list[int] | None = None  # chosen at clipping time when there are several

    def to_dict(self) -> dict:
        pick = self.pick.to_dict()
        if self.webcam_rect:
            pick["webcam_rect"] = self.webcam_rect
        return {
            "candidate": asdict(self.candidate),
            "pick": pick,
        }


def _spread_candidates(
    candidates: list[CandidateMoment],
    min_gap_s: float,
    max_n: int,
) -> list[CandidateMoment]:
    """Greedy time-spread: pick highest-score candidates at least min_gap_s apart.

    Iterates candidates in descending score order. A candidate is included only
    if it is at least min_gap_s seconds from every already-selected candidate.
    """
    selected: list[CandidateMoment] = []
    for c in candidates:
        if all(abs(c.t_center - s.t_center) >= min_gap_s for s in selected):
            selected.append(c)
            if len(selected) >= max_n:
                break
    return selected


def select_clips(
    candidates: list[CandidateMoment],
    transcript: Transcript,
    picker: ClipPicker | None,
    stream_context: str,
    config: ClipConfig,
    *,
    max_candidates: int = 20,
    min_picks: int | None = None,
    video_end_s: float = 0.0,
) -> list[PickResult]:
    """Run the LLM over the top candidates and return accepted picks.

    Only passes the top max_candidates by score to avoid excessive API calls.
    If the LLM accepts fewer than min_picks, the top-scored remaining candidates
    are force-accepted. min_picks defaults to config.min_picks (0 = never force).
    video_end_s: the video's length; a clip that ends after it moves back inside (0 = unknown).
    """
    if config.select == "signals":
        return _inside_video(_signal_picks(candidates, config), video_end_s, config)
    assert picker is not None, "select: llm needs a picker"
    if min_picks is None:
        min_picks = config.min_picks
    top = _spread_candidates(
        sorted(candidates, key=lambda c: c.score, reverse=True),
        min_gap_s=config.min_clip_gap_s,
        max_n=max_candidates,
    )
    v2 = config.selection_mode == "v2"
    borders = _sentence_borders(transcript) if v2 else ([], [])
    picks: list[PickResult] = []
    picked_centers: set[float] = set()
    asked = failed = 0

    for i, candidate in enumerate(top, 1):
        log.info(
            "LLM candidate %d/%d  t=%.1f  score=%.1f  signals=%s",
            i,
            len(top),
            candidate.t_center,
            candidate.score,
            candidate.sources,
        )
        window = _extract_transcript_window(transcript, candidate.t_center)
        if window.startswith("(no transcript") and not config.ask_without_speech:
            log.warning("no transcript for t=%.1f — skipping LLM call", candidate.t_center)
            continue
        t0 = time.monotonic()
        pick = picker.pick(window, candidate, stream_context)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        asked += 1
        if pick is None:
            failed += 1
            log.warning("picker returned None for t=%.1f — skipping", candidate.t_center)
            continue
        log.info(
            "llm_pick",
            extra={
                "candidate_id": f"{candidate.t_center:.1f}",
                "include": pick.include,
                "score": round(candidate.score, 2),
                "elapsed_ms": elapsed_ms,
            },
        )
        if not pick.include:
            log.debug("LLM rejected t=%.1f: %s", candidate.t_center, pick.reason)
            continue

        if v2 and _times_from_quotes(pick, transcript, candidate.t_center):
            pick = _clamp_duration(pick, config)  # Claude's own words decide the cut: no snap
        elif v2:
            pick = _snap_to_sentences(pick, borders, config)
        else:
            pick = _clamp_duration(pick, config)
        log.info(
            "  accepted: [%.1f-%.1f] (%.1fs) - %s",
            pick.start_offset_s,
            pick.end_offset_s,
            pick.duration_s,
            pick.title.encode("ascii", "replace").decode("ascii"),
        )
        picks.append(PickResult(candidate=candidate, pick=pick))
        picked_centers.add(candidate.t_center)

    if asked and failed == asked:  # no answer at all is an error, not "the LLM chose no clips"
        raise LLMUnavailable(
            f"Claude svarade inte: inget av {asked} svar gick att läsa. Kör igen när det fungerar."
        )
    if v2:
        _calibrate(picks, transcript, picker, stream_context)

    _force_accept(picks, top, picked_centers, min_picks, config)
    return _inside_video(picks, video_end_s, config)


def _inside_video(picks: list[PickResult], video_end_s: float, config: ClipConfig) -> list[PickResult]:
    """End each clip at the video's end at the latest. The start moves back so the clip keeps
    min_seconds, otherwise the cut stops at the file's end and the clip is too short."""
    if video_end_s <= 0:
        return picks
    for result in picks:
        pick = result.pick
        if pick.end_offset_s > video_end_s:
            pick.end_offset_s = video_end_s
            if pick.end_offset_s - pick.start_offset_s < config.min_seconds:
                pick.start_offset_s = max(0.0, video_end_s - config.min_seconds)
    return picks


def _force_accept(
    picks: list[PickResult],
    top: list[CandidateMoment],
    picked_centers: set[float],
    min_picks: int,
    config: ClipConfig,
) -> None:
    """Fallback: if the LLM was too strict, force-accept top candidates to meet min_picks."""
    if len(picks) < min_picks and top:
        log.warning(
            "LLM accepted only %d/%d candidates — force-accepting top picks to reach min_picks=%d",
            len(picks),
            len(top),
            min_picks,
        )
        for candidate in top:
            if len(picks) >= min_picks:
                break
            if candidate.t_center in picked_centers:
                continue
            if candidate.score <= 0:
                log.warning(
                    "skipping force-accept for t=%.1f: score=%.1f (zero-scored candidate)",
                    candidate.t_center,
                    candidate.score,
                )
                continue
            start_s = max(0.0, candidate.t_center - config.preroll_s)
            end_s = start_s + config.max_seconds
            forced_pick = ClipPick(
                include=True,
                start_offset_s=start_s,
                end_offset_s=end_s,
                title="Momento destacado",
                reason="Force-accepted: LLM accepted fewer than min_picks candidates.",
            )
            forced_pick = _clamp_duration(forced_pick, config)
            log.info(
                "  force-accepted: [%.1f-%.1f] t=%.1f score=%.1f",
                forced_pick.start_offset_s,
                forced_pick.end_offset_s,
                candidate.t_center,
                candidate.score,
            )
            picks.append(PickResult(candidate=candidate, pick=forced_pick))
            picked_centers.add(candidate.t_center)


def _signal_picks(candidates: list[CandidateMoment], config: ClipConfig) -> list[PickResult]:
    """The signals_top_n best candidates, min_clip_gap_s apart, as clips from preroll to postroll.
    No LLM call: the title is a placeholder the user replaces when reviewing."""
    top = _spread_candidates(
        sorted(candidates, key=lambda c: c.score, reverse=True),
        min_gap_s=config.min_clip_gap_s,
        max_n=config.signals_top_n,
    )
    picks = []
    for c in top:
        hours, rest = divmod(int(c.t_center), 3600)
        pick = ClipPick(
            include=True,
            start_offset_s=max(0.0, c.t_center - config.preroll_s),
            end_offset_s=c.t_center + config.postroll_s,
            title=f"Moment {hours}:{rest // 60:02d}:{rest % 60:02d}",
            reason=f"{SIGNALS_REASON}: {', '.join(c.sources)}.",
        )
        picks.append(PickResult(candidate=c, pick=_clamp_duration(pick, config)))
    log.info("signals mode: %d clips from %d candidates, no LLM", len(picks), len(candidates))
    return picks


def _extract_transcript_window(transcript: Transcript, t_center: float) -> str:
    """Return a formatted string of transcript lines within the window."""
    t_start = max(0.0, t_center - WINDOW_PRE_S)
    t_end = t_center + WINDOW_POST_S

    lines: list[str] = []
    for seg in transcript.segments:
        if seg.end < t_start or seg.start > t_end:
            continue
        rel = seg.start - t_center
        sign = "+" if rel >= 0 else ""
        lines.append(f"[{sign}{rel:+.1f}s] {seg.text.strip()}")

    if not lines:
        return "(no transcript available for this window)"
    return "\n".join(lines)


def _token(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def _matches(tokens: list[str], quote: list[str]) -> list[int]:
    """Indexes where quote starts in tokens."""
    n = len(quote)
    return [i for i in range(len(tokens) - n + 1) if tokens[i : i + n] == quote]


def _times_from_quotes(pick: ClipPick, transcript: Transcript, t_center: float) -> bool:
    """Set the times from the words Claude quoted: the start of the start quote's first word and the end
    of the end quote's last word, in the window Claude saw. A quote said twice takes the place nearest
    Claude's seconds, and the end is never before the start. A quote that is not found shortens from
    its far side down to two words. False, and the times stay, when either quote is not found."""
    start_q = [t for t in (_token(w) for w in pick.start_quote.split()) if t]
    end_q = [t for t in (_token(w) for w in pick.end_quote.split()) if t]
    if not start_q or not end_q:
        return False
    low, high = t_center - WINDOW_PRE_S, t_center + WINDOW_POST_S
    words = [
        w
        for seg in transcript.segments
        if seg.end >= low and seg.start <= high
        for w in seg.words or []
        if _token(w.word)
    ]
    tokens = [_token(w.word) for w in words]
    starts: list[int] = []
    for n in range(len(start_q), min(2, len(start_q)) - 1, -1):  # the first words matter most
        if starts := _matches(tokens, start_q[:n]):
            break
    if not starts:
        return False
    first = min(starts, key=lambda i: abs(words[i].start - pick.start_offset_s))
    ends: list[int] = []
    for n in range(len(end_q), min(2, len(end_q)) - 1, -1):  # the last words matter most
        found = [i + n - 1 for i in _matches(tokens, end_q[-n:]) if i >= first]
        if found:
            ends = found
            break
    if not ends:
        return False
    last = min(ends, key=lambda i: abs(words[i].end - pick.end_offset_s))
    pick.start_offset_s, pick.end_offset_s = words[first].start, words[last].end
    return True


def _sentence_borders(transcript: Transcript) -> tuple[list[float], list[float]]:
    """Times where sentences start and end, from the words' punctuation. A segment without words
    counts as one word."""
    starts: list[float] = []
    ends: list[float] = []
    sentence_done = True
    for seg in transcript.segments:
        for word in seg.words or [Word(seg.start, seg.end, seg.text, 1.0)]:
            if sentence_done:
                starts.append(word.start)
            sentence_done = word.word.strip().rstrip("\"')]").endswith((".", "!", "?", "…"))
            if sentence_done:
                ends.append(word.end)
    return starts, ends


def _nearest(times: list[float], t: float) -> float | None:
    near = [x for x in times if abs(x - t) <= SNAP_MAX_S]
    return min(near, key=lambda x: abs(x - t), default=None)


def _snap_to_sentences(
    pick: ClipPick, borders: tuple[list[float], list[float]], config: ClipConfig
) -> ClipPick:
    """Move the start to the nearest sentence start and the end to the nearest sentence end that
    keeps the length within limits. No border nearby: the time stays. Then clamp as before."""
    starts, ends = borders
    if pick.payoff_start_s is not None:  # never cut into the payoff
        starts = [s for s in starts if s <= pick.payoff_start_s]
    if pick.payoff_end_s is not None:
        ends = [e for e in ends if e >= pick.payoff_end_s]
        # a start further back than max_seconds before the payoff's end would make the clamp cut it
        starts = [s for s in starts if s >= pick.payoff_end_s - config.max_seconds]
    start = _nearest(starts, pick.start_offset_s)
    if start is not None:
        pick.start_offset_s = start
    fits = [e for e in ends if config.min_seconds <= e - pick.start_offset_s <= config.max_seconds]
    end = _nearest(fits, pick.end_offset_s)
    if end is not None:
        pick.end_offset_s = end
    return _clamp_duration(pick, config)


def _calibrate(
    picks: list[PickResult], transcript: Transcript, picker: ClipPicker, stream_context: str
) -> None:
    """One extra call where Claude compares the VOD's accepted clips, so a score means the same in
    every VOD. Skipped for a picker without rank (OpenAI, Ollama) and for fewer than two clips."""
    rank = getattr(picker, "rank", None)
    if rank is None or len(picks) < 2:
        return
    lines = []
    for result in picks:
        p = result.pick
        said = " ".join(
            seg.text.strip()
            for seg in transcript.segments
            if seg.start < p.end_offset_s and seg.end > p.start_offset_s
        )
        lines.append(
            f"{p.title} | hook {p.hook}, engagement {p.engagement}, value {p.value}, "
            f"shareability {p.shareability}, total {p.score} | starts: {said[:300]}"
        )
    scores = rank(lines, stream_context)
    if scores is None:
        return
    if len(scores) != len(picks):
        log.warning(
            "rank gave %d scores for %d clips; the first scores stay", len(scores), len(picks)
        )
        return
    for result, score in zip(picks, scores, strict=True):
        result.pick.score = score


def _clamp_duration(pick: ClipPick, config: ClipConfig) -> ClipPick:
    """Ensure the clip is within [min_seconds, max_seconds]."""
    duration = pick.end_offset_s - pick.start_offset_s
    if duration < config.min_seconds:
        pick.end_offset_s = pick.start_offset_s + config.min_seconds
    elif duration > config.max_seconds:
        pick.end_offset_s = pick.start_offset_s + config.max_seconds
    return pick
