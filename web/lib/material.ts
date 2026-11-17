/** Veckonumret som i Sverige (ISO 8601): veckan börjar på måndag, och vecka 1 har årets första torsdag. */
export function isoWeek(date: Date): number {
  const thursday = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 3 - ((date.getDay() + 6) % 7));
  const jan1 = new Date(thursday.getFullYear(), 0, 1);
  // Avrundat till hela dagar: dygnet då sommartiden börjar är bara 23 timmar.
  return 1 + Math.floor(Math.round((thursday.getTime() - jan1.getTime()) / 86400000) / 7);
}

const OLD_MS = 28 * 86400000;

/** Materialet per vecka (i den ordning det kommer, nyast först), och det som är äldre än 4 veckor eller saknar datum för sig. */
export function groupByWeek<T extends { created: string | null }>(items: T[], now: number) {
  const recent: { key: string; week: number; start: Date; items: T[] }[] = [];
  const older: T[] = [];
  for (const item of items) {
    const created = item.created ? new Date(item.created) : null;
    if (!created || now - created.getTime() > OLD_MS) {
      older.push(item);
      continue;
    }
    const start = new Date(created.getFullYear(), created.getMonth(), created.getDate() - ((created.getDay() + 6) % 7));
    const key = start.toDateString();
    const group = recent.find((g) => g.key === key);
    if (group) group.items.push(item);
    else recent.push({ key, week: isoWeek(start), start, items: [item] });
  }
  return { recent, older };
}
