import { describe, expect, it } from "vitest";
import { choose, defaultEngine, transcriptionNote } from "./transcription";
import type { TranscriptionSetting } from "./types";

const base: TranscriptionSetting = {
  engine: "berget",
  chosen: false,
  model: "small",
  downloaded: true,
  size_gb: 0.5,
  berget_key: true,
  models: [
    { id: "small", size_gb: 0.5, downloaded: true },
    { id: "medium", size_gb: 1.5, downloaded: false },
    { id: "large-v3", size_gb: 3, downloaded: false },
  ],
};

describe("transcription choice", () => {
  it("defaults to Berget with a key and Local without", () => {
    expect(defaultEngine(true)).toBe("berget");
    expect(defaultEngine(false)).toBe("local");
  });

  it("changing the model shows its size and whether it is downloaded", () => {
    const medium = choose({ ...base, engine: "local" }, { model: "medium" });
    expect(medium).toMatchObject({ engine: "local", model: "medium", size_gb: 1.5, downloaded: false, chosen: true });
    expect(transcriptionNote(medium)).toMatch(/not downloaded yet.*1\.5 GB/);
    expect(transcriptionNote(choose(medium, { model: "small" }))).toMatch(/small is downloaded/);
  });

  it("says when Berget is chosen without a key", () => {
    expect(transcriptionNote({ ...base, berget_key: false })).toMatch(/key is missing.*choose Local/);
    expect(transcriptionNote(base)).toMatch(/cloud/);
  });
});
