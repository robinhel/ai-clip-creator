import type { LiveWatch, Peak, PeakStatus } from "./liveApi";
import { parseTwitchLink } from "./twitchLink";

/** Som LOGIN i campaign/streams.py: Twitchs login är små bokstäver, siffror och _. */
const LOGIN = /^[a-z0-9_]{1,25}$/;
/** En kanallänk: twitch.tv/kanal, med eller utan https://, www. eller m., / på slutet och ?… */
const CHANNEL_LINK = /^(?:https?:\/\/)?(?:www\.|m\.)?twitch\.tv\/([^/?#]+)\/?(?:[?#].*)?$/i;

/** Det som stod i fältet: kanalens login, eller ett fel att visa. */
export type ChannelInput = { login: string } | { error: string };

/** Gör om fältet "Twitch-kanal" (namn eller kanallänk) till ett login med små bokstäver. */
export function channelLogin(text: string): ChannelInput {
  const value = text.trim();
  if (!value) return { error: "Skriv en Twitch-kanal, till exempel ishowspeed." };
  if (parseTwitchLink(value).vodId) {
    return { error: "Det är en länk till en video. Skriv kanalens namn eller länken till kanalen." };
  }
  const login = (value.match(CHANNEL_LINK)?.[1] ?? value).toLowerCase();
  if (!LOGIN.test(login)) {
    return { error: "Det är ingen Twitch-kanal. Skriv namnet, till exempel ishowspeed, eller länken till kanalen." };
  }
  return { login };
}

/** Läget för en fri kanal, som text. */
export function channelStateText(watch: Pick<LiveWatch, "watching" | "live">): string {
  if (!watch.watching) return "Bevakas inte";
  return watch.live ? "Live nu. Appen spelar in strömmen." : "Väntar på att kanalen går live";
}

/** Kanalerna som bevakas utan kampanj. */
export const freeChannels = (all: LiveWatch[]): LiveWatch[] => all.filter((w) => w.profile === null);

const sameFreeChannel = (a: LiveWatch, b: LiveWatch) => a.profile === null && b.profile === null && a.channel === b.channel;

/** Listan med den fria kanalen utbytt, eller tillagd sist. Listan som kom in ändras inte. */
export function withChannel(all: LiveWatch[], watch: LiveWatch): LiveWatch[] {
  if (!all.some((w) => sameFreeChannel(w, watch))) return [...all, watch];
  return all.map((w) => (sameFreeChannel(w, watch) ? watch : w));
}

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
