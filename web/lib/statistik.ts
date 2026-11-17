import type { CampaignClip, CampaignStatus, ClipPost } from "./types";
import { serverTime } from "./utils";

/** Siffrorna som fylls i per inlägg, med orden som Statistik visar. */
export const STATS = [
  { key: "views", label: "Visningar" },
  { key: "likes", label: "Gillningar" },
  { key: "comments", label: "Kommentarer" },
  { key: "shares", label: "Delningar" },
] as const;
export type StatKey = (typeof STATS)[number]["key"];

/** Det som står i radens fält, som text. paid är fältet Betalt i dollar. */
export type Draft = Record<StatKey | "paid", string>;

/** Det som är sparat. paid_usd saknas på en rad utan inlägg: den har inget fält Betalt. */
export type Saved = Pick<ClipPost, StatKey> & { paid_usd?: number | null };

export type PatchBody = Partial<Record<StatKey | "paid_usd", number | null>> & { status?: CampaignStatus };

/** Belopp med decimalkomma, som Robin skriver det: 14,20. */
const usdText = (value: number) => value.toFixed(2).replace(".", ",");

export function draftFrom(saved: Saved): Draft {
  return {
    views: saved.views?.toString() ?? "",
    likes: saved.likes?.toString() ?? "",
    comments: saved.comments?.toString() ?? "",
    shares: saved.shares?.toString() ?? "",
    paid: saved.paid_usd == null ? "" : usdText(saved.paid_usd),
  };
}

/** Fälten som skiljer sig från det sparade. */
export function changed(draft: Draft, saved: Saved): (StatKey | "paid")[] {
  const before = draftFrom(saved);
  const keys: (StatKey | "paid")[] = [...STATS.map((s) => s.key), ...(saved.paid_usd !== undefined ? ["paid" as const] : [])];
  return keys.filter((key) => draft[key].trim() !== before[key]);
}

/** Antalet i ett fält. undefined: texten är inget helt tal. */
export function parseCount(text: string): number | null | undefined {
  const clean = text.replace(/\s/g, "");
  if (clean === "") return null;
  return /^\d+$/.test(clean) ? Number(clean) : undefined;
}

/** Beloppet i fältet Betalt, med komma eller punkt. undefined: texten är inget belopp. */
export function parseUsd(text: string): number | null | undefined {
  const clean = text.replace(/\s/g, "").replace(",", ".");
  if (clean === "") return null;
  return /^\d+(\.\d{1,2})?$/.test(clean) ? Number(clean) : undefined;
}

/**
 * Det som ska sparas: de ändrade fälten och, om ett steg ges, den nya statusen i samma anrop.
 * Så försvinner inget som är skrivet men inte sparat när Robin trycker Betald eller ett annat steg.
 */
export function buildPatch(draft: Draft, saved: Saved, status?: CampaignStatus): { body: PatchBody } | { error: string } {
  const body: PatchBody = {};
  for (const key of changed(draft, saved)) {
    if (key === "paid") {
      const usd = parseUsd(draft.paid);
      if (usd === undefined) return { error: "Betalt: skriv beloppet i dollar, till exempel 12,50." };
      body.paid_usd = usd;
      continue;
    }
    const count = parseCount(draft[key]);
    if (count === undefined) {
      const label = STATS.find((s) => s.key === key)!.label;
      return { error: `${label}: skriv ett helt tal utan decimaler, till exempel 2500.` };
    }
    body[key] = count;
  }
  if (status) body.status = status;
  return { body };
}

/** Efter så många dagar utan ändring påminner Att göra om visningarna igen. */
export const REFRESH_DAYS = 3;
const DAY_MS = 86_400_000;

/** Steget som väntar på Robin. posta görs i Att posta, de andra på raden i Statistik. */
export type Step = "inskickad" | "analytics_inskickad" | "visningar" | "betald" | "visningar_igen" | "posta";
const STEP_ORDER: Step[] = ["inskickad", "analytics_inskickad", "visningar", "betald", "visningar_igen", "posta"];

/** En rad i Statistik: ett inlägg, eller ett klipp utan inlägg. */
export interface Row {
  clip: CampaignClip;
  post?: ClipPost;
}

export interface TodoItem extends Row {
  key: string;
  step: Step;
  /** id på raden i Statistik, eller null när steget görs i Att posta. */
  rowId: string | null;
  /** Kampanjens minsta visningar för betalning. */
  minViews: number;
}

export const rowId = ({ clip, post }: Row) => (post ? `inlagg-${post.id}` : `klipp-${clip.id}`);

/** Kampanjens gräns. Servern använder 2000 när kampanjen inte går att läsa. */
export const minViewsOf = (clip: CampaignClip) => clip.min_views ?? 2000;

/** Statusen på raden: inläggets, eller klippets när det inte har några inlägg. */
export const rowStatus = ({ clip, post }: Row): CampaignStatus => post?.status ?? clip.status;

/** Raderna i Statistik: en per inlägg, och en för ett klipp utan inlägg. */
export function rowsOf(clips: CampaignClip[]): Row[] {
  return clips.flatMap((clip) => (clip.posts?.length ? clip.posts.map((post) => ({ clip, post })) : [{ clip }]));
}

/** Steget som väntar på raden, eller null. */
export function stepFor(row: Row, now: number): Step | null {
  const data = row.post ?? row.clip;
  switch (rowStatus(row)) {
    case "godkand":
      return "posta";
    case "postad":
      return "inskickad";
    case "inskickad":
      if (data.views == null) return "visningar";
      if (data.views >= minViewsOf(row.clip)) return "analytics_inskickad";
      return now - serverTime(data.updated) >= REFRESH_DAYS * DAY_MS ? "visningar_igen" : null;
    case "analytics_inskickad":
      return "betald";
    default:
      return null;
  }
}

/** Att göra: ett steg per inlägg, det mest brådskande först. */
export function todoItems(clips: CampaignClip[], now: number): TodoItem[] {
  return rowsOf(clips)
    .map((row) => ({ row, step: stepFor(row, now) }))
    .filter((x): x is { row: Row; step: Step } => x.step !== null)
    .sort((a, b) => STEP_ORDER.indexOf(a.step) - STEP_ORDER.indexOf(b.step))
    .map(({ row, step }) => ({
      ...row,
      key: rowId(row),
      step,
      rowId: step === "posta" ? null : rowId(row),
      minViews: minViewsOf(row.clip),
    }));
}

// Postade rader utan steg kommer efter dem med steg, i statusens ordning. Opostade klipp sist.
const DONE_ORDER: CampaignStatus[] = ["inskickad", "analytics_inskickad", "postad", "betald"];

function rank(row: Row, now: number): number {
  const step = stepFor(row, now);
  if (step && step !== "posta") return STEP_ORDER.indexOf(step);
  const done = DONE_ORDER.indexOf(rowStatus(row));
  return done >= 0 ? 10 + done : 20;
}

/** Det som behöver göras först. Inom samma steg: det som har väntat längst först. */
export function sortRows<T extends Row>(rows: T[], now: number): T[] {
  const time = (r: Row) => serverTime((r.post ?? r.clip).created);
  return [...rows].sort((a, b) => rank(a, now) - rank(b, now) || time(a) - time(b));
}
