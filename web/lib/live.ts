import type { LiveWatch, Peak, PeakStatus } from "./liveApi";

const STATUS_TEXT: Record<PeakStatus, string> = {
  vantar: "Väntar",
  hamtar: "Hämtar delen",
  ko: "I kö",
  kor: "Gör klippen",
  klar: "Klar",
  fel: "Misslyckades",
  hoppad: "Hoppades över",
};

export const statusText = (status: PeakStatus): string => STATUS_TEXT[status] ?? status;

const clock = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" });

/** Tiden som HH:MM i svensk tid. Tom text om tiden inte går att läsa. */
export function timeText(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "" : clock.format(ms);
}

/** av: bevakningen står inte på. vantar: den står på men kanalen är inte live. live: kanalen är live. */
export type WatchState = "av" | "vantar" | "live";

export function watchState(w: LiveWatch): WatchState {
  if (!w.watching) return "av";
  return w.live ? "live" : "vantar";
}

/** Senaste toppen först. Listan som kom in ändras inte. */
export const newestFirst = (peaks: Peak[]): Peak[] => [...peaks].sort((a, b) => b.n - a.n);
