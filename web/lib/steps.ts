import type { CampaignClip, CampaignStatus } from "./types";

/** Flikarna i Klippen, som de står i adressen (?tab=). */
export type ClipStep = "granska" | "posta" | "skicka-in";

/** Klippens status per flik. Underkända klipp ligger under Att granska: de ska rättas eller nekas. */
export const STEP_STATUSES: Record<ClipStep, CampaignStatus[]> = {
  granska: ["renderad", "underkand"],
  posta: ["godkand"],
  "skicka-in": ["postad"],
};

export const isStep = (value: string | null | undefined): value is ClipStep =>
  value === "granska" || value === "posta" || value === "skicka-in";

export function stepOf(status: CampaignStatus): ClipStep | null {
  return (Object.keys(STEP_STATUSES) as ClipStep[]).find((step) => STEP_STATUSES[step].includes(status)) ?? null;
}

/** Antal klipp i fliken, ur antalet per status (sammanfattningen) eller ur listan. */
export function stepCount(counts: Partial<Record<CampaignStatus, number>>, step: ClipStep): number {
  return STEP_STATUSES[step].reduce((sum, status) => sum + (counts[status] ?? 0), 0);
}

export function countByStatus(clips: CampaignClip[]): Partial<Record<CampaignStatus, number>> {
  const counts: Partial<Record<CampaignStatus, number>> = {};
  for (const clip of clips) counts[clip.status] = (counts[clip.status] ?? 0) + 1;
  return counts;
}

/** Skicka in först när något väntar på att skickas in: det har bara 30 minuter. */
export function stepOrder(counts: Partial<Record<CampaignStatus, number>>): ClipStep[] {
  return stepCount(counts, "skicka-in") > 0 ? ["skicka-in", "granska", "posta"] : ["granska", "posta", "skicka-in"];
}

/** Fliken utan ?tab: den första i ordningen som har klipp, annars Att granska. */
export function defaultStep(counts: Partial<Record<CampaignStatus, number>>): ClipStep {
  return stepOrder(counts).find((step) => stepCount(counts, step) > 0) ?? "granska";
}

/** Content Rewards tar bara emot ett inlägg inom 30 minuter. Appen räknar från att länken sparades. */
export const SUBMIT_WINDOW_MS = 30 * 60_000;

/** Den närmaste tiden som inte har gått ut, bland alla klipp. null: ingen. */
export function submitDeadline(clips: CampaignClip[], now = Date.now()): number | null {
  const times = clips.flatMap((clip) =>
    (clip.posts ?? []).filter((p) => p.status === "postad").map((p) => Date.parse(p.created) + SUBMIT_WINDOW_MS)
  );
  const coming = times.filter((t) => t > now);
  return coming.length ? Math.min(...coming) : null;
}

/** Ordningen i Skicka in: först klippen som hinner skickas in, med minst tid kvar överst. Sist de där tiden har gått ut. */
export function bySubmitTime(now = Date.now()) {
  const key = (clip: CampaignClip) => submitDeadline([clip], now) ?? Infinity;
  return (a: CampaignClip, b: CampaignClip) => key(a) - key(b) || 0;
}

// Texterna från serverns regelkontroll (rules.py och caption.py). Talen jämförs inte: gamla klipp har punkt, nya komma.
const isCaption = (p: string) => p.startsWith("Captionen ");
const isShort = (p: string) => p.includes("Kampanjen kräver minst");
const LONG = /Profilens tak är ([\d.,]+) s/;

/** Hur Robin rättar ett underkänt klipp. caption: felet är bara i captionen. kort: klippet kan inte bli längre, för
 * Redigera kortar bara. lang: korta klippet med Redigera till högst max sekunder, och rätta captionen om caption.
 * projekt: gör klippen kampanjklara igen på projektsidan. */
export type ClipFix = { kind: "caption" | "kort" | "projekt" } | { kind: "lang"; max: string; caption: boolean };

/** Längden, när den är det enda felet utöver captionen. */
function lengthFix(others: string[], caption: boolean): ClipFix | undefined {
  if (others.length !== 1) return undefined;
  const long = LONG.exec(others[0]);
  if (long) return { kind: "lang", max: long[1].replace(",", "."), caption };
  if (isShort(others[0])) return { kind: "kort" };
}

/** null: ingen rad, för klippet är inte underkänt, eller det är en egen video utan rad. */
export function rejectedFix(clip: Pick<CampaignClip, "status" | "vod_id" | "problems">): ClipFix | null {
  if (clip.status !== "underkand") return null;
  const others = clip.problems.filter((p) => !isCaption(p));
  if (clip.problems.length > 0 && others.length === 0) return { kind: "caption" };
  const fix = lengthFix(others, others.length < clip.problems.length) ?? { kind: "projekt" };
  // En egen video har ingen projektsida. Är den för kort, säger felet från servern redan
  // "Ladda upp en längre video eller tryck Nej." Redigera finns för den också.
  return clip.vod_id === "upload" && fix.kind !== "lang" ? null : fix;
}

/** Texten för ett fel som Robin rättar på raden. Taket kommer från felet, alltså från kampanjen. null: projektsidan. */
export function fixHint(fix: ClipFix): string | null {
  if (fix.kind === "lang") {
    const caption = fix.caption ? " Then fix the caption with Edit headline and caption." : "";
    return `The clip is too long. Press Edit and shorten it to at most ${fix.max} s.${caption}`;
  }
  return {
    caption: "The error is only in the caption. Press Edit headline and caption, fix the text and save.",
    // Gör klippen kampanjklara igen gör inte klippet längre, och Redigera trimmar bara inom råklippet.
    kort: "The clip is too short and cannot get longer here. Edit can only shorten it. Press No.",
    projekt: null,
  }[fix.kind];
}
