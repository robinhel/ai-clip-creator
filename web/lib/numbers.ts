/** Ett tal som Robin skrev, med komma eller punkt. Tomt blir null, fel blir NaN. */
export function parseNumber(value: string): number | null {
  const text = value.trim().replace(/\s/g, "").replace(",", ".");
  if (!text) return null;
  return /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
}

/** Talet med svenskt decimalkomma, för ett fält. */
export const numberText = (value: number | null | undefined) => (value == null ? "" : String(value).replace(".", ","));
