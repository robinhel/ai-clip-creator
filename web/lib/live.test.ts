import { describe, expect, it } from "vitest";
import { newestFirst, statusText, timeText, watchState } from "./live";
import type { LiveWatch, Peak } from "./liveApi";

function watch(over: Partial<LiveWatch> = {}): LiveWatch {
  return { profile: "skiimasktimmy", channel: "skiimasktimmy", watching: false, live: false, vod_id: null, error: null, peaks: [], ...over };
}

function peak(over: Partial<Peak> = {}): Peak {
  return { n: 1, at: "2026-11-18T19:05:00Z", vod_s: 3600, status: "vantar", run_id: null, reason: null, ...over };
}

describe("statusText", () => {
  it("ger svensk text för varje status", () => {
    expect(statusText("vantar")).toBe("Väntar");
    expect(statusText("hamtar")).toBe("Hämtar delen");
    expect(statusText("ko")).toBe("I kö");
    expect(statusText("kor")).toBe("Gör klippen");
    expect(statusText("klar")).toBe("Klar");
    expect(statusText("fel")).toBe("Misslyckades");
    expect(statusText("hoppad")).toBe("Hoppades över");
  });
});

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

describe("watchState", () => {
  it("är av när bevakningen inte står på, även om kanalen är live", () => {
    expect(watchState(watch())).toBe("av");
    expect(watchState(watch({ live: true }))).toBe("av");
  });

  it("väntar när bevakningen står på men kanalen inte är live", () => {
    expect(watchState(watch({ watching: true }))).toBe("vantar");
  });

  it("är live när bevakningen står på och kanalen är live", () => {
    expect(watchState(watch({ watching: true, live: true }))).toBe("live");
  });
});

describe("newestFirst", () => {
  it("lägger den senaste toppen först utan att ändra listan", () => {
    const peaks = [peak({ n: 1 }), peak({ n: 3 }), peak({ n: 2 })];
    expect(newestFirst(peaks).map((p) => p.n)).toEqual([3, 2, 1]);
    expect(peaks.map((p) => p.n)).toEqual([1, 3, 2]);
  });
});
