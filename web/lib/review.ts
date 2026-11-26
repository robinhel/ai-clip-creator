/** Klippet som blir aktivt efter Ja eller Nej: nästa i listan, eller föregående om det var sista. */
export function nextActive(ids: number[], done: number): number | null {
  const i = ids.indexOf(done);
  if (i === -1) return ids[0] ?? null;
  return ids[i + 1] ?? ids[i - 1] ?? null;
}

/** Siffran 1–n väljer format n på det aktiva klippet. null för andra tangenter. */
export function formatForKey(key: string, formats: { id: string }[]): string | null {
  if (!/^[1-9]$/.test(key)) return null;
  return formats[Number(key) - 1]?.id ?? null;
}
