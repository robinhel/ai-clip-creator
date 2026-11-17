import { describe, expect, it } from "vitest";
import { bySubmitTime, defaultStep, stepCount, stepOf, stepOrder, submitDeadline } from "./steps";
import type { CampaignClip, ClipPost } from "./types";

const MIN = 60_000;
const NOW = Date.parse("2026-11-17T18:00:00Z");
const post = (status: ClipPost["status"], minutesAgo: number): ClipPost =>
  ({ status, created: new Date(NOW - minutesAgo * MIN).toISOString() }) as ClipPost;
const clip = (posts: ClipPost[]): CampaignClip => ({ status: "postad", posts }) as CampaignClip;

describe("stegen i Klippen", () => {
  it("underkända klipp hör till Att granska, nekade och inskickade till ingen flik", () => {
    expect(stepOf("underkand")).toBe("granska");
    expect(stepOf("godkand")).toBe("posta");
    expect(stepOf("postad")).toBe("skicka-in");
    expect(stepOf("nekad")).toBeNull();
    expect(stepOf("inskickad")).toBeNull();
    expect(stepCount({ renderad: 2, underkand: 1, godkand: 5 }, "granska")).toBe(3);
  });

  it("Skicka in kommer först och öppnas utan ?tab när något väntar", () => {
    expect(stepOrder({ renderad: 4, postad: 1 })).toEqual(["skicka-in", "granska", "posta"]);
    expect(defaultStep({ renderad: 4, postad: 1 })).toBe("skicka-in");
  });

  it("utan något att skicka in står Skicka in sist, och första fliken med klipp öppnas", () => {
    expect(stepOrder({ godkand: 2 })).toEqual(["granska", "posta", "skicka-in"]);
    expect(defaultStep({ godkand: 2, nekad: 9 })).toBe("posta");
    expect(defaultStep({})).toBe("granska");
  });
});

describe("submitDeadline", () => {
  it("tar den närmaste tiden bland inlägg som inte är inskickade", () => {
    const clips = [clip([post("postad", 5), post("inskickad", 25)]), clip([post("postad", 20)])];
    // 20 minuter sedan + 30 minuter: 10 minuter kvar.
    expect(submitDeadline(clips, NOW)).toBe(NOW + 10 * MIN);
  });

  it("hoppar över tider som har gått ut, och svarar null när inget väntar", () => {
    expect(submitDeadline([clip([post("postad", 45), post("postad", 2)])], NOW)).toBe(NOW + 28 * MIN);
    expect(submitDeadline([clip([post("postad", 31)])], NOW)).toBeNull();
    expect(submitDeadline([clip([post("inskickad", 1)])], NOW)).toBeNull();
  });

  it("Skicka in visar först klippen som hinner skickas in, minst tid kvar överst, och sist de där tiden har gått ut", () => {
    const late = { ...clip([post("postad", 40)]), id: 1 };
    const twentyLeft = { ...clip([post("postad", 10)]), id: 2 };
    const fiveLeft = { ...clip([post("postad", 25)]), id: 3 };
    // Ett gammalt inlägg har gått ut, men ett nytt på en annan plattform hinner: det nya räknas.
    const newPost = { ...clip([post("postad", 35), post("postad", 2)]), id: 4 };
    const order = [late, twentyLeft, fiveLeft, newPost].sort(bySubmitTime(NOW)).map((c) => c.id);
    expect(order).toEqual([3, 2, 4, 1]);
  });
});
