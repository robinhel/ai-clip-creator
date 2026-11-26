/** Klippet som blir aktivt efter Ja eller Nej: nästa i listan, eller föregående om det var sista. */
export function nextActive(ids: number[], done: number): number | null {
  const i = ids.indexOf(done);
  return ids[i + 1] ?? ids[i - 1] ?? null;
}
