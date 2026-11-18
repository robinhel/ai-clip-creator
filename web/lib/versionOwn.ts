/** Eget bidrag i en ny version: samma regler som campaign/versions.py, så att dialogen varnar som servern. */

const sec = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

/** Samma text som NO_OWN i campaign/versions.py. */
export const NO_OWN =
  "Versionen har ingen ny hook och ingen ny startpunkt. Bara spegling, hastighet, filter eller font kan ge strike på YouTube och stoppa klippet från För dig på TikTok.";

/** Som _norm i campaign/schedule.py: gemener, utan blanksteg och utan hashtaggar först och sist. */
export function norm(text: string): string {
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

/** "Samma start som originalet", "2 s tidigare" eller "3,5 s senare". */
export function startLabel(startCutS: number): string {
  if (Math.abs(startCutS) < 0.05) return "Samma start som originalet";
  return `${sec.format(Math.abs(startCutS))} s ${startCutS < 0 ? "tidigare" : "senare"}`;
}
