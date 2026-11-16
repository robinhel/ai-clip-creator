import type { CaptionWord } from "./types";

// Samma regler som undertexten i src/clipsmith/rendering/captions.py: ny rad efter 5 ord, 28 tecken eller en paus.
const WORDS_PER_LINE = 5;
const MAX_LINE_CHARS = 28;
const MAX_GAP = 0.6;
const HOLD = 0.3;
const MIN_LEN = 0.05;

const round = (s: number) => Math.round(s * 100) / 100;

/**
 * Flytta ordet i med delta sekunder. Starten hålls mellan grannordens starter (och inom 0–duration),
 * och slutet hålls efter starten. Ordets längd behålls när det går.
 */
export function nudge(words: CaptionWord[], i: number, delta: number, duration: number): CaptionWord[] {
  const w = words[i];
  const lo = words[i - 1]?.start ?? 0;
  const hi = Math.min(words[i + 1]?.start ?? duration, duration - MIN_LEN);
  const start = round(Math.min(Math.max(w.start + delta, lo), hi));
  const end = round(Math.min(Math.max(w.end + (start - w.start), start + MIN_LEN), duration));
  return words.map((x, j) => (j === i ? { ...x, start, end } : x));
}

/** Raden som syns vid tiden t, eller null. Raderna delas som i den inbrända undertexten. */
export function lineAt(words: CaptionWord[], t: number): CaptionWord[] | null {
  const lines: CaptionWord[][] = [];
  let line: CaptionWord[] = [];
  for (const w of words) {
    const text = [...line, w].map((x) => x.word).join(" ");
    if (line.length && (w.start - line[line.length - 1].end > MAX_GAP || text.length > MAX_LINE_CHARS)) {
      lines.push(line);
      line = [];
    }
    line.push(w);
    if (line.length >= WORDS_PER_LINE) {
      lines.push(line);
      line = [];
    }
  }
  if (line.length) lines.push(line);
  return (
    lines.find((l, k) => {
      const next = lines[k + 1]?.[0].start ?? Infinity;
      return t >= l[0].start && t < Math.min(l[l.length - 1].end + HOLD, next);
    }) ?? null
  );
}
