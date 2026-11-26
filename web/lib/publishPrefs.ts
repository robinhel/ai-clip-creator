import type { Platform, TikTokStatus } from "./types";

/** Senaste valen i Publicera: plattform, sätt, när och tid. Sparas i localStorage. */
export const PUBLISH_PREFS = "publicera-val";

export type Way = "utkast" | "direkt" | "sjalv";
export type When = "nu" | "senare";

export interface PublishPrefs {
  platform?: Platform;
  way?: Way;
  when?: When;
  /** Tiden i fältet, som "2026-11-28T18:00" i Stockholmstid. */
  local?: string;
}

const PLATFORMS: Platform[] = ["tiktok", "youtube_shorts", "instagram_reels"];
const WAYS: Way[] = ["utkast", "direkt", "sjalv"];

/** Läser sparade val. Okända värden hoppas över. En tid som har passerat ger Nu. */
export function readPrefs(raw: string | null, isFuture: (local: string) => boolean): PublishPrefs {
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    return {};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return {};
  const d = data as Record<string, unknown>;
  const prefs: PublishPrefs = {};
  if (PLATFORMS.includes(d.platform as Platform)) prefs.platform = d.platform as Platform;
  if (WAYS.includes(d.way as Way)) prefs.way = d.way as Way;
  if (d.when === "senare" && typeof d.local === "string" && isFuture(d.local)) {
    prefs.when = "senare";
    prefs.local = d.local;
  }
  return prefs;
}

/** Det valda sättet om det går att använda nu, annars det första som går. */
export function chooseOption<T extends { way: string; off: unknown }>(options: T[], chosen: string | null | undefined): T | undefined {
  return options.find((o) => o.way === chosen && !o.off) ?? options.find((o) => !o.off);
}

/** Läget för TikTok-kontot. unknown: statusanropet misslyckades, det är inte samma sak som inget konto. */
export type AccountCheck = "checking" | "unknown" | "missing" | "connected";

export function accountCheck(status: TikTokStatus | null, failed: boolean): AccountCheck {
  if (!status) return failed ? "unknown" : "checking";
  const connected = status.accounts ? status.accounts.some((a) => a.connected) : status.connected;
  return connected ? "connected" : "missing";
}
