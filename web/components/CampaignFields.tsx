"use client";

import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Fälten i Ny kampanj och Ställ in. */

export const EXAMPLE_TITLE = "Monster Kill Then Total Chaos";

export const field =
  "h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-brand aria-invalid:border-bad";

/** Samma regel som servern (campaign/caption.py): mall, det som måste finnas, sedan hashtags. */
export function previewCaption(template: string, mustInclude: string[], hashtags: string[]): string {
  let text = template.replaceAll("{title}", EXAMPLE_TITLE);
  for (const must of mustInclude) if (!text.toLowerCase().includes(must.toLowerCase())) text = `${text} ${must}`;
  const seen = new Set<string>();
  const tags = hashtags.filter((t) => !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase()));
  return `${text} ${tags.join(" ")}`.trim();
}

/** Ett tal som Robin skrev, med komma eller punkt. Tomt blir null, fel blir NaN. */
export function parseNumber(value: string): number | null {
  const text = value.trim().replace(/\s/g, "").replace(",", ".");
  if (!text) return null;
  return /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
}

/** Talet med svenskt decimalkomma, för ett fält. */
export const numberText = (value: number | null | undefined) => (value == null ? "" : String(value).replace(".", ","));

/** Länken till kampanjen i Content Rewards: samma kontroll som servern. */
export function contentRewardsProblem(url: string): string | null {
  const text = url.trim();
  if (!text) return null;
  try {
    const parsed = new URL(text);
    if (parsed.protocol === "https:" && ["contentrewards.com", "www.contentrewards.com"].includes(parsed.hostname)) return null;
  } catch {
    // inte en adress
  }
  return "Länken ska börja med https://contentrewards.com/. Kopiera den från kampanjsidan.";
}

export function httpsProblem(url: string): string | null {
  const text = url.trim();
  if (!text) return null;
  try {
    if (new URL(text).protocol === "https:") return null;
  } catch {
    // inte en adress
  }
  return "Skriv hela adressen. Den börjar med https://.";
}

export function Field({ label, hint, error, children, className }: {
  label: string;
  hint?: React.ReactNode;
  /** Felet visas under fältet, i stället för längst ner. */
  error?: string | null;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <label className="block space-y-1">
        <span className="block text-[13px] text-muted-foreground">{label}</span>
        {children}
      </label>
      {/* Utanför etiketten, så att fältets namn bara är etiketten. */}
      {error ? (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      ) : (
        hint && <p className="text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 size-4 shrink-0 accent-[#7c5cff]" />
      <span>
        {label}
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  );
}

export function Chips({ label, values, onChange, prefix = "", placeholder }: {
  label: string;
  values: string[];
  onChange: (v: string[]) => void;
  prefix?: string;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const words = draft.split(/[\s,]+/).filter(Boolean).map((w) => (prefix && !w.startsWith(prefix) ? prefix + w : w));
    const next = [...values];
    for (const w of words) if (!next.some((v) => v.toLowerCase() === w.toLowerCase())) next.push(w);
    onChange(next);
    setDraft("");
  }
  return (
    <div className="space-y-1">
      <span className="block text-[13px] text-muted-foreground">{label}</span>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-line bg-background px-2 py-1.5 focus-within:border-brand">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-full bg-line py-0.5 pr-1 pl-2.5 text-xs">
            {v}
            <button type="button" aria-label={`Ta bort ${v}`} onClick={() => onChange(values.filter((x) => x !== v))} className="rounded-full p-0.5 text-muted-foreground hover:text-foreground">
              <X className="size-3" aria-hidden />
            </button>
          </span>
        ))}
        <input
          aria-label={label}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === ",") && draft.trim()) {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={() => draft.trim() && add()}
          placeholder={placeholder}
          className="min-w-32 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
    </div>
  );
}

export interface FormatRow {
  id?: string;
  name: string;
  description: string;
}

/** Kampanjens format. Ett format som finns behåller sitt id, så att gamla klipp hittar det. */
export function FormatList({ formats, onChange }: { formats: FormatRow[]; onChange: (formats: FormatRow[]) => void }) {
  return (
    <>
      <ul className="space-y-2">
        {formats.map((f, i) => (
          <li key={i} className="grid items-start gap-2 sm:grid-cols-[220px_minmax(0,1fr)_auto]">
            <input
              aria-label={`Format ${i + 1}, namn`}
              value={f.name}
              onChange={(e) => onChange(formats.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
              placeholder="Namn"
              aria-invalid={!f.name.trim() || undefined}
              className={field}
            />
            <input
              aria-label={`Format ${i + 1}, beskrivning`}
              value={f.description}
              onChange={(e) => onChange(formats.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))}
              placeholder="Vad som gör ett bra klipp i formatet"
              className={field}
            />
            <Button type="button" variant="ghost" className="h-9 text-muted-foreground" aria-label={`Ta bort format ${i + 1}`} onClick={() => onChange(formats.filter((_, j) => j !== i))}>
              <Trash2 aria-hidden />
            </Button>
          </li>
        ))}
      </ul>
      <Button type="button" variant="outline" className="h-9" onClick={() => onChange([...formats, { name: "", description: "" }])}>
        <Plus aria-hidden />
        Lägg till format
      </Button>
    </>
  );
}

export function Section({ step, title, id, children }: { step?: number; title: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="grid scroll-mt-24 gap-4 border-b border-line py-7 md:grid-cols-[200px_minmax(0,1fr)]">
      <h2 className="flex items-baseline gap-2.5 font-semibold">
        {step != null && (
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-line text-xs text-muted-foreground tabular-nums">
            {step}
          </span>
        )}
        {title}
      </h2>
      <div className="min-w-0 space-y-4">{children}</div>
    </section>
  );
}

export const PLATFORM_CHOICES = [
  { id: "tiktok", label: "TikTok" },
  { id: "youtube_shorts", label: "YouTube Shorts" },
  { id: "instagram_reels", label: "Instagram Reels" },
] as const;
