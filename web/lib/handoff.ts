// Filer som Robin drar in på Hem lämnas över till Egna videor. Webbläsaren behåller dem medan appen byter
// sida. Laddas sidan om är de borta, och då väljer Robin filen igen.
let files: File[] = [];

export function handOverFiles(list: File[]): void {
  files = list;
}

/** Filerna från Hem, en gång. */
export function takeHandedOverFiles(): File[] {
  const taken = files;
  files = [];
  return taken;
}
