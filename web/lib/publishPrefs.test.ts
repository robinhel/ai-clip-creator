import { describe, expect, it } from "vitest";
import { accountCheck, chooseOption, readPrefs } from "./publishPrefs";
import type { TikTokAccount, TikTokStatus } from "./types";

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

describe("chooseOption", () => {
  const options = [
    { way: "utkast", off: "No TikTok account is connected." },
    { way: "sjalv", off: null },
    { way: "direkt", off: null },
  ];

  it("tar det sparade sättet när det går att använda", () => {
    expect(chooseOption(options, "direkt")?.way).toBe("direkt");
  });

  it("faller tillbaka till standard när det sparade inte går nu", () => {
    expect(chooseOption(options, "utkast")?.way).toBe("sjalv");
    expect(chooseOption(options, null)?.way).toBe("sjalv");
    expect(chooseOption([], "direkt")).toBeUndefined();
  });
});

describe("accountCheck", () => {
  const account = (connected: boolean): TikTokAccount => ({
    open_id: "a",
    display_name: "A",
    avatar_url: null,
    scopes: [],
    connected,
    refresh_expires_at: null,
    pending_24h: 0,
  });
  const status = (accounts: TikTokAccount[] | undefined, connected = false): TikTokStatus => ({
    configured: true,
    connected,
    display_name: null,
    avatar_url: null,
    scopes: [],
    refresh_expires_at: null,
    pending_24h: 0,
    pending_limit: 5,
    accounts,
  });

  it("håller isär okänd status och inget kopplat konto", () => {
    expect(accountCheck(null, true)).toBe("unknown");
    expect(accountCheck(status([]), false)).toBe("missing");
    expect(accountCheck(status([account(false)]), false)).toBe("missing");
  });

  it("väntar medan statusen hämtas", () => {
    expect(accountCheck(null, false)).toBe("checking");
  });

  it("ser kopplade konton, även hos äldre servrar utan lista", () => {
    expect(accountCheck(status([account(true)]), false)).toBe("connected");
    expect(accountCheck(status(undefined, true), false)).toBe("connected");
    expect(accountCheck(status(undefined, false), false)).toBe("missing");
  });
});
