// Settings > Transcription: Berget i molnet eller en lokal Whisper-modell. Rena funktioner, testas i transcription.test.ts.
import type { LocalWhisperModel, TranscribeEngine, TranscriptionSetting } from "./types";

export const ENGINES: { id: TranscribeEngine; label: string; hint: string }[] = [
  { id: "berget", label: "Berget", hint: "cloud, fast, needs a key" },
  { id: "local", label: "Local", hint: "free, on this computer, slower" },
];

export const LOCAL_MODELS: { id: LocalWhisperModel; size_gb: number }[] = [
  { id: "small", size_gb: 0.5 },
  { id: "medium", size_gb: 1.5 },
  { id: "large-v3", size_gb: 3 },
];

/** The default when nothing is saved: Berget with a key, otherwise Local. Same rule as the server. */
export function defaultEngine(bergetKey: boolean): TranscribeEngine {
  return bergetKey ? "berget" : "local";
}

/** The line under the switch: what happens with the current choice. */
export function transcriptionNote(t: TranscriptionSetting): string {
  if (t.engine === "berget") {
    return t.berget_key
      ? "Berget transcribes in the cloud. A 4 h VOD takes about 8 minutes and costs about US$0.50."
      : "Berget is chosen, but the Berget key is missing. Paste it in the Berget box, or choose Local.";
  }
  const size = `${t.size_gb} GB`;
  return t.downloaded
    ? `Whisper ${t.model} is downloaded (about ${size}). Transcription is free and runs on this computer.`
    : `Whisper ${t.model} is not downloaded yet. The first run downloads about ${size}, then it is reused.`;
}

/** Choosing an engine or a model: the setting after the change, as shown before the server answers. */
export function choose(t: TranscriptionSetting, change: { engine?: TranscribeEngine; model?: LocalWhisperModel }): TranscriptionSetting {
  const model = change.model ?? t.model;
  const info = t.models.find((m) => m.id === model);
  return {
    ...t,
    ...change,
    chosen: true,
    model,
    size_gb: info?.size_gb ?? t.size_gb,
    downloaded: info?.downloaded ?? false,
  };
}
