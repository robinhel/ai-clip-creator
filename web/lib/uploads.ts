// Rena hjälpfunktioner för Egna videor (/uploads): valen, längden, formuläret och de senaste valen per kampanj.
import { parseNumber } from "./numbers";
import type { StickerPosition } from "./types";

// Samma val som servern (campaign/versions.py och campaign/drafts.py). Servern kontrollerar dem.
export const SPEEDS = [1, 1.05, 1.1, 1.15, 1.2];
export const FILTERS: [string, string][] = [
  ["none", "None"],
  ["fresh", "Fresh"],
  ["warm", "Warm"],
  ["cool", "Cool"],
  ["punch", "Punchy"],
];
export const FRAMINGS: [string, string, string][] = [
  ["fill", "Fill the frame", "Zooms in and crops the edges."],
  ["fit", "Whole frame", "The whole frame on a blurred background."],
];
const POSITION_VALUES: StickerPosition[] = ["top_left", "top_right", "center", "bottom_left", "bottom_right"];

export const VIDEO_TYPES = [".mp4", ".mov", ".m4v", ".webm", ".mkv"];
export const ACCEPT = VIDEO_TYPES.join(",");

/** Ljudet i klippet: originalljudet, tyst eller musik ur kampanjens mapp. */
export type Audio = "keep" | "mute" | "music";
export const AUDIOS: [Audio, string][] = [
  ["keep", "Keep the audio"],
  ["mute", "Mute"],
  ["music", "Music"],
];
// Samma filer som servern tar emot i POST /campaign/uploads/music: ljudfiler och videor.
export const MUSIC_TYPES = [".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ...VIDEO_TYPES];
export const MUSIC_ACCEPT = MUSIC_TYPES.join(",");

/** Valen för en video. startS och endS är sekunder i källvideon; endS 0 betyder videons slut. */
export interface Choices {
  mirror: boolean;
  speed: number;
  filter: string;
  brightness: number;
  saturation: number;
  framing: string;
  font: string;
  overlay: string;
  sticker: string;
  position: StickerPosition;
  stickerStart: string;
  stickerLength: string;
  startS: number;
  endS: number;
  audio: Audio;
  /** Filnamnet i kampanjens musik. Används bara när audio är music. */
  music: string;
  /** Musiken börjar vid, sekunder i musikfilen. Text, så att Robin kan skriva komma. */
  musicStart: string;
  /** Originalljudet under musiken, 0–100 %. */
  originalVolume: number;
}

export const START_CHOICES: Choices = {
  mirror: false,
  speed: 1,
  filter: "none",
  brightness: 0,
  saturation: 0,
  framing: "fit",
  font: "",
  overlay: "",
  sticker: "",
  position: "top_right",
  stickerStart: "0",
  stickerLength: "3",
  startS: 0,
  endS: 0,
  audio: "keep",
  music: "",
  musicStart: "0",
  originalVolume: 30,
};

/** Sekunder ur ett fält, i engelskt format (1,200.5). Tomt är 0, fel är NaN. */
export const seconds = (text: string) => parseNumber(text) ?? 0;

const oneDecimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
// Två decimaler när längden är utanför gränserna, så att 45.04 s inte visas som 45 s när taket är 45 s.
const twoDecimals = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** Sekunder med högst en decimal: 18.2. */
export const formatSeconds = (s: number) => oneDecimal.format(s);

/** Det färdiga klippet blir så här mycket längre än (slut − start) / hastighet (ljudet), som servern räknar. */
export const EXTRA_S = 0.1;

/** Klippets längd i sekunder: (slut − start) / hastighet. */
export function clipLength(c: Choices, durationS: number): number {
  const end = c.endS > 0 ? c.endS : durationS;
  return Math.round(((end - c.startS) / c.speed) * 1000) / 1000;
}

export interface LengthCheck {
  ok: boolean;
  text: string;
}

/** Texten under tidslinjen. ok false: klippet går inte att göra, och texten säger vad Robin ska göra. */
export function lengthCheck(length: number, min: number, max: number | null | undefined): LengthCheck {
  if (length < min) {
    return {
      ok: false,
      text: `The clip will be ${twoDecimals.format(Math.max(length, 0))} s. The campaign requires at least ${twoDecimals.format(min)} s. Move the start earlier or the end later, or choose a lower speed.`,
    };
  }
  if (max != null && length > max) {
    return {
      ok: false,
      text: `The clip will be ${twoDecimals.format(length)} s. The campaign allows at most ${twoDecimals.format(max)} s. Move the start later or the end earlier, or choose a higher speed.`,
    };
  }
  const limits =
    max != null ? `The campaign allows ${twoDecimals.format(min)}–${twoDecimals.format(max)} s.` : `The campaign requires at least ${twoDecimals.format(min)} s.`;
  return { ok: true, text: `The clip will be ${oneDecimal.format(length)} s. ${limits}` };
}

/** parseNumber läser inte minustecken, så "-1" blir NaN. Det här skiljer ett negativt tal från ett felskrivet. */
const negative = (text: string) => /^\s*-/.test(text);

/** Kontrollerar klistermärkets tider. null när de går att använda. */
export function stickerProblemText(c: Choices): string | null {
  if (!c.sticker) return null;
  const startS = seconds(c.stickerStart);
  const lengthS = seconds(c.stickerLength);
  if (negative(c.stickerStart)) return "The sticker start cannot be negative.";
  if (!Number.isFinite(startS)) return "The sticker start is not a number. Write seconds like 1.5.";
  if (!Number.isFinite(lengthS)) return "The sticker length is not a number. Write seconds like 1.5.";
  return lengthS >= 0.5 && lengthS <= 10 ? null : "The sticker must show for 0.5–10 s.";
}

/** Kontrollerar musikens val. null när de går att använda. */
export function audioProblemText(c: Choices): string | null {
  if (c.audio !== "music") return null;
  if (!c.music) return "Choose a song under Audio, or add music.";
  const startS = seconds(c.musicStart);
  if (negative(c.musicStart)) return "The start in the music must be 0 s or more.";
  return Number.isFinite(startS) ? null : "The start in the music is not a number. Write seconds like 1.5.";
}

/** Texterna vid reglaget när originalljudet är 0 %. Tom lista när det hörs. */
export function volumeWarning(originalVolume: number, rules: string[] | null | undefined): string[] {
  if (originalVolume > 0) return [];
  const warning = ["The original audio is not heard in the clip."];
  return rules?.length ? [...warning, "Read the campaign's rule above."] : warning;
}

/** Ljudet med serverns namn. Musikens fält bara när Musik är valt. Bilden har inget ljud, så frame får dem inte. */
function audioFields(c: Choices): [string, string][] {
  if (c.audio !== "music") return [["audio", c.audio]];
  return [
    ["audio", c.audio],
    ["music", c.music],
    ["music_start_s", String(seconds(c.musicStart))],
    ["original_volume", String(c.originalVolume / 100)],
  ];
}

/** Valen med serverns namn, som formulärfält. Klistermärket bara när det är valt. */
function choiceFields(c: Choices, profile: string, title: string): [string, string][] {
  const fields: [string, string][] = [
    ["profile", profile],
    ["title", title],
    ["mirror", String(c.mirror)],
    ["speed", String(c.speed)],
    ["filter", c.filter],
    ["brightness", String(c.brightness)],
    ["saturation", String(c.saturation)],
    ["framing", c.framing],
    ["font", c.font],
    ["overlay", c.overlay],
    ["start_s", String(c.startS)],
    ["end_s", String(c.endS)],
  ];
  if (c.sticker) {
    fields.push(
      ["sticker", c.sticker],
      ["sticker_position", c.position],
      ["sticker_start_s", String(seconds(c.stickerStart))],
      ["sticker_duration_s", String(seconds(c.stickerLength))]
    );
  }
  return fields;
}

/** Det som skickas till render och save för ett utkast. */
export function choicesForm(c: Choices, profile: string, title: string): FormData {
  const form = new FormData();
  for (const [key, value] of [...choiceFields(c, profile, title), ...audioFields(c)]) form.append(key, value);
  return form;
}

/** Query för bilden. t är sekunder i det färdiga klippet. */
export function frameQuery(c: Choices, profile: string, title: string, t: number): string {
  return new URLSearchParams([["t", String(t)], ...choiceFields(c, profile, title)]).toString();
}

/** Valen som en sträng, för att se om de har ändrats sedan förhandsvisningen. */
export const previewKey = (c: Choices, profile: string, title: string) => JSON.stringify([...choiceFields(c, profile, title), ...audioFields(c)]);

/** true när valen inte längre är de som förhandsvisades. Utan förhandsvisning: false. */
export function previewOutdated(key: string | null | undefined, c: Choices, profile: string, title: string): boolean {
  return key != null && key !== previewKey(c, profile, title);
}

/** Videofilerna och namnen på de andra filerna. */
export function splitVideoFiles<T extends { name: string }>(files: ArrayLike<T> | Iterable<T>): { videos: T[]; rejected: string[] } {
  const videos: T[] = [];
  const rejected: string[] = [];
  for (const file of Array.from(files)) {
    if (VIDEO_TYPES.some((type) => file.name.toLowerCase().endsWith(type))) videos.push(file);
    else rejected.push(file.name);
  }
  return { videos, rejected };
}

/** Texten när filen inte är en ljudfil eller video, innan den laddas upp. null när den går att använda. */
export function musicFileProblem(name: string): string | null {
  if (MUSIC_TYPES.some((type) => name.toLowerCase().endsWith(type))) return null;
  return `${name} is not an audio file or video. Choose mp3, m4a, aac, wav, ogg, flac, mp4, mov, m4v, webm or mkv.`;
}

/** "a.txt och b.png är ingen video …", eller null när alla filer var videor. */
export function rejectText(rejected: string[]): string | null {
  if (rejected.length === 0) return null;
  const names = rejected.length === 1 ? rejected[0] : `${rejected.slice(0, -1).join(", ")} and ${rejected.at(-1)}`;
  return `${names} is not a video and was not added. Choose mp4, mov, m4v, webm or mkv.`;
}

/** Som localStorage. Ett eget gränssnitt, så att testerna kan använda ett i minnet. */
export interface ChoiceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const storageKey = (profile: string) => `egna-videor:val:${profile}`;
// Start och slut hör till videon, och rubriken skriver Robin per video. De sparas inte.
const SAVED_KEYS = (Object.keys(START_CHOICES) as (keyof Choices)[]).filter((k) => k !== "startS" && k !== "endS");
const ALLOWED: Partial<Record<keyof Choices, readonly unknown[]>> = {
  speed: SPEEDS,
  filter: FILTERS.map(([value]) => value),
  framing: FRAMINGS.map(([value]) => value),
  position: POSITION_VALUES,
  audio: AUDIOS.map(([value]) => value),
};

/** Sparar valen som de senaste i kampanjen. Utan localStorage händer inget. */
export function saveLastChoices(storage: ChoiceStorage, profile: string, c: Choices): void {
  const saved = Object.fromEntries(SAVED_KEYS.map((k) => [k, c[k]]));
  try {
    storage.setItem(storageKey(profile), JSON.stringify(saved));
  } catch {
    // Blockerad eller full lagring: valen gäller ändå på sidan.
  }
}

function usable(key: keyof Choices, value: unknown): boolean {
  if (typeof value !== typeof START_CHOICES[key]) return false;
  if (typeof value === "number" && !Number.isFinite(value)) return false;
  if (key === "originalVolume" && ((value as number) < 0 || (value as number) > 100)) return false;
  return ALLOWED[key]?.includes(value) ?? true;
}

/** Musik som inte finns i kampanjens lista längre ger Behåll. Annars samma val. */
export function withKnownMusic(c: Choices, music: string[]): Choices {
  if (c.audio !== "music" || music.includes(c.music)) return c;
  return { ...c, audio: "keep", music: "" };
}

/**
 * De senaste valen i kampanjen. Ett trasigt värde, eller ett fält med fel typ, ger startvalet.
 * music: kampanjens musikfiler, när de är kända. En sparad låt som inte finns där ger Behåll.
 */
export function loadLastChoices(storage: ChoiceStorage, profile: string, music?: string[]): Choices {
  let stored: unknown;
  try {
    stored = JSON.parse(storage.getItem(storageKey(profile)) ?? "null");
  } catch {
    return { ...START_CHOICES };
  }
  if (!stored || typeof stored !== "object") return { ...START_CHOICES };
  const record = stored as Record<string, unknown>;
  const loaded = SAVED_KEYS.filter((k) => usable(k, record[k])).map((k) => [k, record[k]]);
  const choices = { ...START_CHOICES, ...Object.fromEntries(loaded) };
  return music ? withKnownMusic(choices, music) : choices;
}

/** Använd på alla: valen till en annan video. Start och slut hör till varje video och följer inte med. */
export function copyChoices(from: Choices, to: Choices): Choices {
  return { ...from, startS: to.startS, endS: to.endS };
}

/** ", där 2 egna klipp redan väntar" i texten överst. */
export function alreadyWaiting(approved: number): string {
  if (approved === 0) return "";
  return approved === 1 ? ", where 1 own clip is already waiting" : `, where ${approved} own clips are already waiting`;
}

/** Storleken på en fil: 12,3 MB. */
export function formatSize(bytes: number): string {
  const mb = bytes / 1_000_000;
  return mb >= 1000 ? `${oneDecimal.format(mb / 1000)} GB` : `${oneDecimal.format(mb)} MB`;
}

/** Längden på en video: 0:42 eller 12:05. */
export function formatDuration(s: number): string {
  const whole = Math.round(s);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
