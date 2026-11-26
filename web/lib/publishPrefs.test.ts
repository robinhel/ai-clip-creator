import { describe, expect, it } from "vitest";
import { readPrefs } from "./publishPrefs";

const future = (local: string) => local >= "2026-11-27";

describe("readPrefs", () => {
  it("läser plattform, sätt, när och tid", () => {
    const raw = JSON.stringify({ platform: "tiktok", way: "direkt", when: "senare", local: "2026-11-28T18:00" });
    expect(readPrefs(raw, future)).toEqual({ platform: "tiktok", way: "direkt", when: "senare", local: "2026-11-28T18:00" });
  });

  it("struntar i en tid som har passerat, och då blir det Nu", () => {
    const raw = JSON.stringify({ platform: "tiktok", way: "direkt", when: "senare", local: "2026-11-01T18:00" });
    expect(readPrefs(raw, future)).toEqual({ platform: "tiktok", way: "direkt" });
  });

  it("struntar i okända värden och trasig JSON", () => {
    expect(readPrefs(JSON.stringify({ platform: "myspace", way: "fax", when: "igår" }), future)).toEqual({});
    expect(readPrefs("{inte json", future)).toEqual({});
    expect(readPrefs(null, future)).toEqual({});
    expect(readPrefs("[1]", future)).toEqual({});
  });
});
