// Texten i bilden (headline) i Redigera och Ändra rubrik. Titeln är första raden i captionen.

/** Längsta text i bilden, som servern. */
export const HEADLINE_MAX = 80;

/** Det som ska skickas som headline, eller undefined när den är oförändrad. "" betyder att titeln står i bilden. */
export function headlineChange(value: string, saved: string | null | undefined): string | undefined {
  const next = value.trim();
  return next === (saved ?? "") ? undefined : next;
}

/** Titel och text i bilden som har ändrats, för POST /edit. Tomt objekt: inget är ändrat. */
export function textEdit(
  title: string,
  savedTitle: string,
  headline: string,
  savedHeadline: string | null | undefined
): { title?: string; headline?: string } {
  const edit: { title?: string; headline?: string } = {};
  if (title.trim() !== savedTitle) edit.title = title.trim();
  const next = headlineChange(headline, savedHeadline);
  if (next !== undefined) edit.headline = next;
  return edit;
}
