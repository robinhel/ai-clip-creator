// Settings > Transcription: Berget i molnet eller en lokal Whisper-modell. Rena funktioner, testas i transcription.test.ts.
import { usd } from "./utils";
import type { CostParts, TranscribeEngine, TranscriptionSetting } from "./types";

export const ENGINES: { id: TranscribeEngine; label: string; hint: string }[] = [
  { id: "berget", label: "Berget", hint: "cloud, fast, needs a key" },
  { id: "local", label: "Local", hint: "free, on this computer, slower" },
];

/** The chosen local model from the server's list: its size and whether it is downloaded. */
export function chosenModel(t: TranscriptionSetting): TranscriptionSetting["models"][number] | undefined {
  return t.models.find((m) => m.id === t.model);
}

/** Is the chosen local model already in the Hugging Face cache? */
export function modelDownloaded(t: TranscriptionSetting): boolean {
  return chosenModel(t)?.downloaded ?? false;
}

/** The line under the switch: what happens with the current choice. */
export function transcriptionNote(t: TranscriptionSetting): string {
  if (t.engine === "berget") {
    return t.berget_key
      ? "Berget transcribes in the cloud. A 4 h VOD takes about 8 minutes and costs about US$0.50."
      : "Berget is chosen, but the Berget key is missing. Paste it in the Berget box, or choose Local.";
  }
  const size = `${chosenModel(t)?.size_gb ?? "?"} GB`;
  return modelDownloaded(t)
    ? `Whisper ${t.model} is downloaded (about ${size}). Transcription is free and runs on this computer.`
    : `Whisper ${t.model} is not downloaded yet. The first run downloads about ${size}, then it is reused.`;
}

/** Local transcription costs nothing; the cost texts say this instead of "Berget $0.00". */
export const FREE_TRANSCRIPTION = "Free transcription on this computer.";

/** "Berget $0.14, Claude $0.48", or only Claude when the transcription is local (Berget costs 0). */
export function costParts(cost: CostParts): string {
  const claude = `Claude ${usd.format(cost.claude_usd)}`;
  return cost.berget_usd === 0 ? claude : `Berget ${usd.format(cost.berget_usd)}, ${claude}`;
}

/** The start page's text after "About N min", up to the full stop: what finishing the VOD costs. In the fast mode only
 * the transcription costs, and nothing when it is local. */
export function finishCost(costUsd: number | null, cost: CostParts | null | undefined, fast: boolean): string {
  if (fast && cost) {
    return cost.berget_usd === 0
      ? ` to finish. ${FREE_TRANSCRIPTION} The signals choose, without Claude`
      : ` and ${usd.format(cost.berget_usd)} to finish (Berget only: the signals choose, without Claude)`;
  }
  const total = costUsd != null ? ` and ${usd.format(costUsd)}` : ", unknown cost,";
  return `${total} to finish${cost ? ` (${costParts(cost)})` : ""}`;
}
