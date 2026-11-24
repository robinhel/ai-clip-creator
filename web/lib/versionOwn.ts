/** Eget bidrag i en ny version: samma regler som campaign/versions.py, så att dialogen varnar som servern. */

/** Sekunder med högst en decimal och decimalkomma. */
export const sec = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** Samma text som NO_OWN i campaign/versions.py. */
export const NO_OWN =
  "The version has no new hook and no new start point. Only mirroring, speed, filter or font can get a strike on YouTube and keep the clip out of For You on TikTok.";

/** Som _norm i campaign/schedule.py: gemener, utan blanksteg och utan hashtaggar först och sist. */
function norm(text: string): string {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  while (words.length && words[0].startsWith("#")) words.shift();
  while (words.length && words[words.length - 1].startsWith("#")) words.pop();
  return words.join("");
}

/** Startpunkten är flyttad minst new_start_s, tidigare eller senare. */
export function isNewStart(startCutS: number, newStartS: number): boolean {
  return Math.abs(startCutS) >= newStartS - 1e-6;
}

/** Versionen har en ny hook eller en ny startpunkt. Visar kampanjen ingen hook räknas bara startpunkten. */
export function hasOwn(v: { hook_text: boolean; hook: string; hooks_used: string[]; new_start_s: number; start_cut_s: number }): boolean {
  if (isNewStart(v.start_cut_s, v.new_start_s)) return true;
  if (!v.hook_text || !v.hook.trim()) return false;
  const used = new Set(v.hooks_used.map(norm));
  return !used.has(norm(v.hook));
}

/** "Same start as the original", "2 s tidigare" eller "3,5 s senare". */
export function startLabel(startCutS: number): string {
  if (Math.abs(startCutS) < 0.05) return "Same start as the original";
  return `${sec.format(Math.abs(startCutS))} s ${startCutS < 0 ? "earlier" : "later"}`;
}

/** Hur långt reglaget går, bara åt de håll som går: "Högst 4,5 s tidigare och 2 s senare". Tom när inget håll går. */
export function startReach(lo: number, hi: number): string {
  const parts = [lo < 0 ? `${sec.format(-lo)} s earlier` : "", hi > 0 ? `${sec.format(hi)} s later` : ""].filter(Boolean);
  return parts.length ? `At most ${parts.join(" and ")}` : "";
}
