"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { inRange, nudge } from "@/lib/words";
import type { CaptionWord } from "@/lib/types";

const STEP = 0.1;
const sec = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

interface Props {
  words: CaptionWord[];
  /** Orden som servern skickade, för att markera de ändrade. */
  original: CaptionWord[];
  onChange: (words: CaptionWord[]) => void;
  /** Videons tid, intervallet och råklippets längd, i sekunder från råklippets start. */
  now: number;
  inS: number;
  outS: number;
  duration: number;
  onSeek: (t: number) => void;
}

/** Orden i undertexten mellan in och ut. Klick spolar dit; ordet kan skrivas om, flyttas 0,1 s eller tas bort. */
export function CaptionWords({ words, original, onChange, now, inS, outS, duration, onSeek }: Props) {
  const [picked, setPicked] = useState<number | null>(null);
  // Bara orden som bränns in, samma urval som servern.
  const shown = words.map((w, i) => ({ w, i })).filter(({ w }) => inRange(w, inS, outS));
  const sel = picked != null ? words[picked] : undefined;
  const changed = (w: CaptionWord) => !original.some((o) => o.start === w.start && o.end === w.end && o.word === w.word);

  return (
    <section aria-label="Words in the subtitles" data-caption-words className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium">Subtitles</h3>
        <p className="text-xs text-muted-foreground">Click a word to fix it.</p>
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No words between in and out.</p>
      ) : (
        <p className="max-h-36 overflow-y-auto rounded-xl border border-line bg-background px-2.5 py-2 text-sm leading-8">
          {shown.map(({ w, i }) => {
            const current = now >= w.start && now < w.end;
            return (
              <button
                key={i}
                type="button"
                data-word-index={i}
                data-current={current || undefined}
                data-changed={changed(w) || undefined}
                aria-pressed={picked === i}
                onClick={() => {
                  setPicked(i);
                  onSeek(w.start);
                }}
                className={cn(
                  "mx-px rounded-md px-1.5 py-0.5 outline-none transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-brand",
                  current && "bg-brand/25 text-foreground",
                  picked === i && "ring-1 ring-brand",
                  changed(w) && "underline decoration-warn decoration-dotted underline-offset-4"
                )}
              >
                {w.word}
              </button>
            );
          })}
        </p>
      )}
      {sel && picked != null && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            aria-label="The word"
            value={sel.word}
            maxLength={40}
            autoFocus
            onChange={(e) => onChange(words.map((w, j) => (j === picked ? { ...w, word: e.target.value } : w)))}
            className="h-9 w-40 rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand"
          />
          <span className="text-xs text-muted-foreground tabular-nums">
            {sec.format(sel.start)}–{sec.format(sel.end)} s
          </span>
          <div className="ml-auto flex gap-1.5">
            {([-STEP, STEP] as const).map((d) => (
              <Button
                key={d}
                variant="outline"
                className="h-9 tabular-nums"
                onClick={() => {
                  const next = nudge(words, picked, d, duration);
                  onChange(next);
                  onSeek(next[picked].start);
                }}
              >
                {d < 0 ? "−0,1 s" : "+0,1 s"}
              </Button>
            ))}
            <Button
              variant="ghost"
              className="h-9 text-muted-foreground"
              onClick={() => {
                onChange(words.filter((_, j) => j !== picked));
                setPicked(null);
              }}
            >
              <Trash2 aria-hidden />
              Remove
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
