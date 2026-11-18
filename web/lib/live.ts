import type { Peak, PeakStatus } from "./liveApi";

const STATUS_TEXT: Record<PeakStatus, string> = {
  vantar: "Väntar",
  hamtar: "Hämtar delen",
  ko: "I kö",
  kor: "Gör klippen",
  klar: "Klar",
  fel: "Misslyckades",
  hoppad: "Hoppades över",
};

export const statusText = (status: PeakStatus): string => STATUS_TEXT[status];

const clock = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" });

/** Tiden som HH:MM i svensk tid. Tom text om tiden inte går att läsa. */
export function timeText(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "" : clock.format(ms);
}

/** Senaste toppen först. Listan som kom in ändras inte. */
export const newestFirst = (peaks: Peak[]): Peak[] => [...peaks].sort((a, b) => b.n - a.n);
