import type { LiveWatch, Peak, PeakStatus } from "./liveApi";
import { parseTwitchLink } from "./twitchLink";

/** Twitchs login är små bokstäver, siffror och _. Samma regel finns i LOGIN i campaign/streams.py: ändra båda samtidigt. */
const LOGIN = /^[a-z0-9_]{1,25}$/;
/** En kanallänk: twitch.tv/kanal, med eller utan https://, www. eller m., / på slutet och ?… */
const CHANNEL_LINK = /^(?:https?:\/\/)?(?:www\.|m\.)?twitch\.tv\/([^/?#]+)\/?(?:[?#].*)?$/i;

/** Det som stod i fältet: kanalens login, eller ett fel att visa. */
export type ChannelInput = { login: string } | { error: string };

/** Gör om fältet "Twitch-kanal" (namn eller kanallänk) till ett login med små bokstäver. */
export function channelLogin(text: string): ChannelInput {
  const value = text.trim();
  if (!value) return { error: "Enter a Twitch channel, for example ishowspeed." };
  if (parseTwitchLink(value).vodId) {
    return { error: "That is a link to a video. Enter the channel name or the link to the channel." };
  }
  const login = (value.match(CHANNEL_LINK)?.[1] ?? value).toLowerCase();
  if (!LOGIN.test(login)) {
    return { error: "That is not a Twitch channel. Enter the name, for example ishowspeed, or the link to the channel." };
  }
  return { login };
}

/** Läget för en fri kanal, som text. */
export function channelStateText(watch: Pick<LiveWatch, "watching" | "live">): string {
  if (!watch.watching) return "Not watched";
  return watch.live ? "Live now. The app is recording the stream." : "Waiting for the channel to go live";
}

/** Bevakningen för kampanjen, eller de fria kanalerna när profile är null. */
export const watchesOf = (all: LiveWatch[], profile: string | null): LiveWatch[] => all.filter((w) => w.profile === profile);

/** De fria kanalerna med kanalen utbytt, eller tillagd sist. Listan som kom in ändras inte. */
export function withChannel(channels: LiveWatch[], watch: LiveWatch): LiveWatch[] {
  if (!channels.some((w) => w.channel === watch.channel)) return [...channels, watch];
  return channels.map((w) => (w.channel === watch.channel ? watch : w));
}

/** Felets text, eller fallback när felet inte är ett Error. */
export const errorMessage = (err: unknown, fallback: string): string => (err instanceof Error ? err.message : fallback);

const STATUS_TEXT: Record<PeakStatus, string> = {
  vantar: "Waiting",
  hamtar: "Downloading the part",
  ko: "Queued",
  kor: "Making the clips",
  klar: "Done",
  fel: "Failed",
  hoppad: "Skipped",
};

export const statusText = (status: PeakStatus): string => STATUS_TEXT[status];

const clock = new Intl.DateTimeFormat("en-US", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" });

/** Tiden som HH:MM i svensk tid. Tom text om tiden inte går att läsa. */
export function timeText(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "" : clock.format(ms);
}

/** Senaste toppen först. Listan som kom in ändras inte. */
export const newestFirst = (peaks: Peak[]): Peak[] => [...peaks].sort((a, b) => b.n - a.n);
