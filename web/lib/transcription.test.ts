import { describe, expect, it } from "vitest";
import { chosenModel, costParts, FREE_TRANSCRIPTION, finishCost, transcriptionNote } from "./transcription";
import type { TranscriptionSetting } from "./types";

const base: TranscriptionSetting = {
  engine: "berget",
  model: "small",
  berget_key: true,
  models: [
    { id: "small", size_gb: 0.5, downloaded: true },
    { id: "medium", size_gb: 1.5, downloaded: false },
    { id: "large-v3", size_gb: 3, downloaded: false },
  ],
};

describe("transcription choice", () => {
  it("reads the size and download state of the chosen model from the server's list", () => {
    const medium = { ...base, engine: "local" as const, model: "medium" as const };
    expect(chosenModel(medium)).toEqual({ id: "medium", size_gb: 1.5, downloaded: false });
    expect(transcriptionNote(medium)).toMatch(/not downloaded yet.*1\.5 GB/);
    expect(transcriptionNote({ ...medium, model: "small" })).toMatch(/small is downloaded/);
  });

  it("says when Berget is chosen without a key", () => {
    expect(transcriptionNote({ ...base, berget_key: false })).toMatch(/key is missing.*choose Local/);
    expect(transcriptionNote(base)).toMatch(/cloud/);
  });

  it("leaves out Berget from the cost when the transcription is local", () => {
    expect(costParts({ berget_usd: 0.14, claude_usd: 0.48, usd: 0.62 })).toBe("Berget $0.14, Claude $0.48");
    expect(costParts({ berget_usd: 0, claude_usd: 0.48, usd: 0.48 })).toBe("Claude $0.48");
    expect(FREE_TRANSCRIPTION).toBe("Free transcription on this computer.");
  });
});

describe("cost to finish a VOD", () => {
  const cloud = { berget_usd: 0.14, claude_usd: 0.48, usd: 0.62 };
  const local = { berget_usd: 0, claude_usd: 0.48, usd: 0.48 };

  it("shows Berget only when it costs something", () => {
    expect(finishCost(0.62, cloud, false)).toBe(" and $0.62 to finish (Berget $0.14, Claude $0.48)");
    expect(finishCost(0.48, local, false)).toBe(" and $0.48 to finish (Claude $0.48)");
    expect(finishCost(null, null, false)).toBe(", unknown cost, to finish");
  });

  it("says that the fast mode with local transcription is free", () => {
    expect(finishCost(0.62, cloud, true)).toBe(" and $0.14 to finish (Berget only: the signals choose, without Claude)");
    expect(finishCost(0.48, local, true)).toBe(" to finish. Free transcription on this computer. The signals choose, without Claude");
  });
});
