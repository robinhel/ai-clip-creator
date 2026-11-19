import type { CampaignClip, CampaignStatus, ClipPost, PostStatus } from "./types";
import { serverTime } from "./utils";

/** Siffrorna som fylls i per inlägg, med orden som Statistik visar. */
export const STATS = [
  { key: "views", label: "Visningar" },
  { key: "likes", label: "Gillningar" },
  { key: "comments", label: "Kommentarer" },
  { key: "shares", label: "Delningar" },
] as const;
export type StatKey = (typeof STATS)[number]["key"];

const PERCENT = { max: 100, hint: "skriv ett tal från 0 till 100, till exempel 45,5." } as const;

/** Fler mått per inlägg, ifyllda från TikTok Studio. Sparningar är ett helt tal, de andra tal med decimaler upp till max. */
export const METRICS = [
  { key: "avg_watch_s", label: "Tittartid (s)", max: Infinity, hint: "skriv sekunderna, till exempel 6,4." },
  { key: "full_pct", label: "Ser klart (%)", ...PERCENT },
  { key: "fyp_pct", label: "Från För dig (%)", ...PERCENT },
  { key: "saves", label: "Sparningar", hint: "skriv ett helt tal utan decimaler, till exempel 12." },
  { key: "countries_pct", label: "Från kampanjens länder (%)", ...PERCENT },
] as const;
export type MetricKey = (typeof METRICS)[number]["key"];
type Metric = (typeof METRICS)[number];
type Field = StatKey | "paid" | MetricKey;

/** Det som står i radens fält, som text. paid är fältet Betalt i dollar. */
export type Draft = Record<Field, string>;

/** Det som är sparat. paid_usd och måtten saknas på en rad utan inlägg: den har inga fält för dem. */
export type Saved = Pick<ClipPost, StatKey> & { paid_usd?: number | null } & Partial<Record<MetricKey, number | null>>;

export type PatchBody = Partial<Record<StatKey | "paid_usd" | MetricKey, number | null>> & { status?: CampaignStatus };

/** Belopp med decimalkomma, som Robin skriver det: 14,20. */
const usdText = (value: number) => value.toFixed(2).replace(".", ",");

export function draftFrom(saved: Saved): Draft {
  // Måtten med decimalkomma: 6,4.
  const metrics = Object.fromEntries(METRICS.map(({ key }) => [key, saved[key]?.toString().replace(".", ",") ?? ""]));
  return {
    ...(metrics as Record<MetricKey, string>),
    views: saved.views?.toString() ?? "",
    likes: saved.likes?.toString() ?? "",
    comments: saved.comments?.toString() ?? "",
    shares: saved.shares?.toString() ?? "",
    paid: saved.paid_usd == null ? "" : usdText(saved.paid_usd),
  };
}

/** Fälten som skiljer sig från det sparade. Betalt och måtten finns bara på en rad med inlägg. */
export function changed(draft: Draft, saved: Saved): Field[] {
  const before = draftFrom(saved);
  const postOnly: Field[] = ["paid", ...METRICS.map((m) => m.key)];
  const keys: Field[] = [...STATS.map((s) => s.key), ...(saved.paid_usd !== undefined ? postOnly : [])];
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

/** Talet i ett mått. Sparningar är ett helt tal, de andra tal med komma eller punkt, högst max. undefined: texten går inte att läsa. */
function parseMetric(metric: Metric, draft: Draft): number | null | undefined {
  if (metric.key === "saves") return parseCount(draft.saves);
  const clean = draft[metric.key].replace(/\s/g, "").replace(",", ".");
  if (clean === "") return null;
  return /^\d+(\.\d+)?$/.test(clean) && Number(clean) <= metric.max ? Number(clean) : undefined;
}

/**
 * Det som ska sparas: de ändrade fälten och, om ett steg ges, den nya statusen i samma anrop.
 * Så försvinner inget som är skrivet men inte sparat när Robin trycker Betald eller ett annat steg.
 */
export function buildPatch(draft: Draft, saved: Saved, status?: CampaignStatus): { body: PatchBody } | { error: string } {
  const body: PatchBody = {};
  for (const key of changed(draft, saved)) {
    const metric = METRICS.find((m) => m.key === key);
    if (metric) {
      const value = parseMetric(metric, draft);
      if (value === undefined) return { error: `${metric.label}: ${metric.hint}` };
      body[metric.key] = value;
      continue;
    }
    const value = key === "paid" ? parseUsd(draft.paid) : parseCount(draft[key]);
    if (value === undefined) return { error: errorFor(key) };
    body[key === "paid" ? "paid_usd" : key] = value;
  }
  if (status) body.status = status;
  return { body };
}

/** Felet när Betalt eller ett av de fyra talen inte går att läsa. */
function errorFor(key: Field): string {
  if (key === "paid") return "Betalt: skriv beloppet i dollar, till exempel 12,50.";
  return `${STATS.find((s) => s.key === key)!.label}: skriv ett helt tal utan decimaler, till exempel 2500.`;
}

/** Tittartiden i procent av klippets längd, med en decimal. null: längden eller tittartiden saknas. */
export function watchPctOfLength(avgWatchS?: number | null, lengthS?: number | null): number | null {
  if (avgWatchS == null || !lengthS) return null;
  return Math.round((avgWatchS / lengthS) * 1000) / 10;
}

/** En rad i tabellen Längd och visningar. avgViews: snittet för inlägg med visningar, null när inget har några. */
export interface LengthGroup {
  label: string;
  posts: number;
  avgViews: number | null;
}

const LENGTHS: { label: string; has: (seconds: number) => boolean }[] = [
  { label: "Under 15 s", has: (s) => s < 15 },
  { label: "15–20 s", has: (s) => s >= 15 && s <= 20 },
  { label: "Över 20 s", has: (s) => s > 20 },
];

function lengthGroup(label: string, rows: Row[]): LengthGroup {
  const views = rows.map((r) => r.post?.views).filter((v): v is number => v != null);
  const avgViews = views.length ? views.reduce((sum, v) => sum + v, 0) / views.length : null;
  return { label, posts: rows.length, avgViews };
}

/** Inlägg och snittvisningar per klippets längd. Räknas per inlägg. Okänd längd bara när den har inlägg. */
export function lengthGroups(rows: Row[]): LengthGroup[] {
  const posts = rows.filter((r) => r.post);
  const groups = LENGTHS.map(({ label, has }) =>
    lengthGroup(label, posts.filter(({ clip }) => clip.length_s != null && has(clip.length_s)))
  );
  const unknown = posts.filter(({ clip }) => clip.length_s == null);
  return unknown.length ? [...groups, lengthGroup("Okänd längd", unknown)] : groups;
}

/** Statusar där klippet har minst ett inlägg. */
export const POSTED: CampaignStatus[] = ["postad", "inskickad", "analytics_inskickad", "betald"];
const STATUSES: CampaignStatus[] = ["renderad", "underkand", "godkand", "nekad", "postad_utan_lank", ...POSTED];

/** Filtret i Statistik: postade inlägg (standard), alla eller en status. Står i adressen som ?status=. */
export type Filter = CampaignStatus | "postade" | "alla";
export const DEFAULT_FILTER: Filter = "postade";

/** ?status= ur adressen. Okänt eller tomt ger standard, så att en gammal länk ändå visar något. */
export function parseFilter(text?: string | null): Filter {
  return text === "alla" || STATUSES.includes(text as CampaignStatus) ? (text as Filter) : DEFAULT_FILTER;
}

export const matches = (filter: Filter, status: CampaignStatus) =>
  filter === "alla" || (filter === "postade" ? POSTED.includes(status) : filter === status);

/** Raden hör till vald kampanj och passar sökningen på titel eller VOD. */
export function inScope({ clip }: Row, campaign: string, search: string): boolean {
  if (campaign && clip.profile !== campaign) return false;
  const needle = search.trim().toLowerCase();
  return !needle || clip.title.toLowerCase().includes(needle) || clip.vod_id.includes(needle);
}

// Ett steg bakåt, så att ett felklick går att ångra. Samma steg som servern tillåter (NEXT_STATUS i campaign/api.py).
const BACK: Partial<Record<CampaignStatus, CampaignStatus>> = {
  postad: "godkand",
  inskickad: "postad",
  analytics_inskickad: "inskickad",
  betald: "inskickad",
};
const BEFORE_PAID: PostStatus[] = ["inskickad", "analytics_inskickad"];

/** Statusen som Ångra går tillbaka till. Betald går tillbaka till statusen före Betald. null: inget Ångra. */
export function undoStatus({ clip, post }: Row): CampaignStatus | null {
  const prev = post?.status === "betald" ? post.prev_status : null;
  if (prev && BEFORE_PAID.includes(prev)) return prev;
  return BACK[post?.status ?? clip.status] ?? null;
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

/** Stegen som gäller en avstängd kampanj: bara visningarna. Skicka in och menyn visar den inte. */
const OFF_STEPS: Step[] = ["visningar", "visningar_igen"];

/** Att göra: ett steg per inlägg, det mest brådskande först. */
export function todoItems(clips: CampaignClip[], now: number): TodoItem[] {
  return rowsOf(clips)
    .map((row) => ({ row, step: stepFor(row, now) }))
    .filter((x): x is { row: Row; step: Step } => x.step !== null)
    .filter(({ row, step }) => !row.clip.campaign_off || OFF_STEPS.includes(step))
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
