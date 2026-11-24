// Tal i engelskt format: punkt före decimalerna, komma mellan tusental (10,000 och 1,000.50).
const PLAIN = /^\d+(\.\d+)?$/;
const GROUPED = /^\d{1,3}(,\d{3})+(\.\d+)?$/;

/** Ett tal som Robin skrev: 6.4, 10,000 eller 1,000.50. Blanksteg räknas inte. Tomt blir null, fel blir NaN. */
export function parseNumber(value: string): number | null {
  const text = value.replace(/\s/g, "");
  if (!text) return null;
  return PLAIN.test(text) || GROUPED.test(text) ? Number(text.replaceAll(",", "")) : NaN;
}

/** Talet för ett fält, med punkt före decimalerna. */
export const numberText = (value: number | null | undefined) => (value == null ? "" : String(value));
