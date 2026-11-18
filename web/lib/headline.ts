// Texten i bilden (headline) i Redigera och Ändra rubrik. Titeln är första raden i captionen.

/** Längsta text i bilden, som servern. */
export const HEADLINE_MAX = 80;

/** Titel och text i bilden som har ändrats, för POST /edit. Tomt objekt: inget är ändrat.
 * headline "" betyder att titeln står i bilden. */
export function textEdit(
  title: string,
  savedTitle: string,
  headline: string,
  savedHeadline: string | null | undefined
): { title?: string; headline?: string } {
  const edit: { title?: string; headline?: string } = {};
  if (title.trim() !== savedTitle) edit.title = title.trim();
  if (headline.trim() !== (savedHeadline ?? "")) edit.headline = headline.trim();
  return edit;
}
