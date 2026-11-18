import { describe, expect, it } from "vitest";
import { newestFirst, timeText } from "./live";
import type { Peak } from "./liveApi";

function peak(over: Partial<Peak> = {}): Peak {
  return { n: 1, at: "2026-11-18T19:05:00Z", vod_s: 3600, status: "vantar", run_id: null, reason: null, ...over };
}

describe("timeText", () => {
  it("visar tiden som HH:MM i svensk tid", () => {
    // Vintertid: UTC+1.
    expect(timeText("2026-11-18T19:05:00Z")).toBe("20:05");
    // Sommartid: UTC+2.
    expect(timeText("2026-07-01T08:30:00Z")).toBe("10:30");
  });

  it("ger tom text för en tid som inte går att läsa", () => {
    expect(timeText("inte en tid")).toBe("");
  });
});

describe("newestFirst", () => {
  it("lägger den senaste toppen först utan att ändra listan", () => {
    const peaks = [peak({ n: 1 }), peak({ n: 3 }), peak({ n: 2 })];
    expect(newestFirst(peaks).map((p) => p.n)).toEqual([3, 2, 1]);
    expect(peaks.map((p) => p.n)).toEqual([1, 3, 2]);
  });
});
