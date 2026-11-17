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

/** Hur Robin rättar ett underkänt klipp. caption: felet är bara i captionen. projekt: gör klippen kampanjklara
 * igen på projektsidan. null: ingen rad, för klippet är inte underkänt, eller det är en egen video utan den knappen. */
export function rejectedFix(clip: Pick<CampaignClip, "status" | "vod_id" | "problems">): "caption" | "projekt" | null {
  if (clip.status !== "underkand") return null;
  // Alla fel från serverns kontroll av captionen börjar med "Captionen ".
  if (clip.problems.length > 0 && clip.problems.every((p) => p.startsWith("Captionen "))) return "caption";
  return clip.vod_id === "upload" ? null : "projekt";
}
