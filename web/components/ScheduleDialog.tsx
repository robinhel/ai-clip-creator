"use client";

import { useState } from "react";
import { BellRing, CalendarClock, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ScheduledPost, SchedulePlatform } from "@/lib/types";

const TZ = "Europe/Stockholm";
export const PLATFORM_NAME: Record<SchedulePlatform, string> = { youtube: "YouTube", tiktok: "TikTok" };

const short = new Intl.DateTimeFormat("en-US", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const long = new Intl.DateTimeFormat("en-US", { timeZone: TZ, dateStyle: "full", timeStyle: "short" });
const parts = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

/** "3 feb. 18:30" i svensk tid, var webbläsaren än står. */
export const atText = (iso: string) => short.format(new Date(iso));

/** Stockholms avvikelse från UTC i minuter vid en viss tidpunkt. */
function offsetMin(ms: number): number {
  const p = Object.fromEntries(parts.formatToParts(ms).map((x) => [x.type, Number(x.value)]));
  return (Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - ms) / 60000;
}

/** "2027-01-15T18:00" i svensk tid till "2027-01-15T18:00:00+01:00". null om värdet inte är en tid. */
export function stockholmIso(local: string): string | null {
  const m = local.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return null;
  const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  // Två steg, så att en tid nära bytet till sommartid får rätt avvikelse.
  const off = offsetMin(wall - offsetMin(wall) * 60000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${m[0]}:00${off < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`;
}

/** Datum och tid i svensk tid, med hela datumet utskrivet under fältet. */
export function WhenField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const at = stockholmIso(value);
  return (
    <label className="block">
      <span className="text-[13px] text-muted-foreground">Date and time, Swedish time</span>
      <input
        type="datetime-local"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 h-10 w-full rounded-lg border border-line bg-background px-3 text-sm tabular-nums outline-none [color-scheme:dark] focus:border-brand"
      />
      {at && (
        <span className="mt-1.5 flex items-center gap-1.5 text-sm text-foreground">
          <CalendarClock className="size-4 text-brand" aria-hidden />
          {long.format(new Date(at))} Swedish time
        </span>
      )}
    </label>
  );
}

/** "Schemalagt 3 feb. 18:30 på YouTube", "Påminnelse …" eller "Laddas upp nu …". */
export function scheduleText(post: ScheduledPost): string {
  if (post.status === "running") return `Uploading now to ${PLATFORM_NAME[post.platform]}`;
  if (post.reminder) return `Reminder ${atText(post.at)} to post on ${PLATFORM_NAME[post.platform]}`;
  return `Scheduled ${atText(post.at)} on ${PLATFORM_NAME[post.platform]}`;
}

/** Klippets väntande poster i klippraden, var och en med Cancel. */
export function ScheduleLines({ pending, onChange }: { pending: ScheduledPost[]; onChange: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (pending.length === 0) return null;

  async function cancel(id: number) {
    setBusy(id);
    setError(null);
    try {
      await api.campaign.schedule.cancel(id);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not cancel the post");
    } finally {
      setBusy(null);
    }
  }

  return (
    <ul aria-label="Scheduled" className="space-y-1.5">
      {pending.map((p) => (
        <li key={p.id} data-schedule-for={p.clip_id} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-xs">
          {p.reminder ? <BellRing className="size-3.5 shrink-0 text-warn" aria-hidden /> : <CalendarClock className="size-3.5 shrink-0 text-brand" aria-hidden />}
          <span className="min-w-0 flex-1 tabular-nums">{scheduleText(p)}</span>
          {p.status === "waiting" && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => cancel(p.id)}
              className="inline-flex items-center gap-1 text-muted-foreground underline underline-offset-4 hover:text-foreground disabled:opacity-50"
            >
              {busy === p.id && <Loader2 className="size-3 animate-spin" aria-hidden />}
              Cancel
            </button>
          )}
          {p.reminder && <span className="w-full text-muted-foreground">The app does not post by itself. You get a notification on the Mac at that time.</span>}
        </li>
      ))}
      {error && (
        <li role="alert" className="text-xs text-bad">
          {error}
        </li>
      )}
    </ul>
  );
}
