import type { CaptionWord } from "./types";

// Samma regler som undertexten i src/clipsmith/rendering/captions.py (_split_lines och _group_words).
const LIMITS = { box: { words: 3, chars: 18, fit: true }, outline: { words: 5, chars: 28, fit: false } };
const MAX_GAP = 0.6;
const MAX_WORD = 1.0;
const HOLD = 0.3;
const MIN_LEN = 0.05;

export type CaptionStyle = keyof typeof LIMITS;

/** Whisper drar ibland ut ett ord över tystnaden efter det. */
const spokenEnd = (w: CaptionWord) => Math.min(w.end, w.start + MAX_WORD);
const lineText = (line: CaptionWord[]) => line.map((w) => w.word.trim().replace(/[{}\\]/g, "")).join(" ");

/** Ordet bränns in: samma urval som servern. */
export const inRange = (w: CaptionWord, inS: number, outS: number) => w.start >= inS && w.start < outS && w.end <= outS + 0.5;

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

/** Raden som syns vid tiden t, eller null. Raderna delas som i den inbrända undertexten; box om stilen saknas. */
export function lineAt(words: CaptionWord[], t: number, style: CaptionStyle = "box"): CaptionWord[] | null {
  const max = LIMITS[style] ?? LIMITS.box;
  const lines: CaptionWord[][] = [];
  let line: CaptionWord[] = [];
  for (const w of words) {
    // fit: ett ord som gör raden för lång börjar en ny rad.
    const tooWide = max.fit && lineText([...line, w]).length > max.chars;
    if (line.length && (w.start - spokenEnd(line[line.length - 1]) > MAX_GAP || tooWide)) {
      lines.push(line);
      line = [];
    }
    line.push(w);
    if (line.length >= max.words || lineText(line).length >= max.chars) {
      lines.push(line);
      line = [];
    }
  }
  if (line.length) lines.push(line);
  return (
    lines.find((l, k) => {
      const end = Math.max(l[0].start + 0.1, spokenEnd(l[l.length - 1])) + HOLD;
      return t >= l[0].start && t < Math.min(end, lines[k + 1]?.[0].start ?? Infinity);
    }) ?? null
  );
}
