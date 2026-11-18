import { describe, expect, it } from "vitest";
import { bySubmitTime, defaultStep, rejectedFix, stepCount, stepOf, stepOrder, submitDeadline } from "./steps";
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

  describe("rejectedFix: hur Robin rättar ett underkänt klipp", () => {
    const failed = (vod_id: string, problems: string[]) => ({ status: "underkand", vod_id, problems }) as CampaignClip;
    const caption = "Captionen saknar hashtaggen #ad.";
    const sound = "Klippet saknar ljud. Streamens ljud måste höras.";
    // Texterna från rules.py. Gamla klipp har punkt i talet, nya har komma.
    const short = "Klippet är 7,9 s. Kampanjen kräver minst 11 s.";
    const shortOld = "Klippet är 4.0 s. Kampanjen kräver minst 7 s.";
    const long = "Klippet är 72,4 s. Profilens tak är 60 s.";
    const longOld = "Klippet är 61.2 s. Profilens tak är 59.5 s.";

    it("fel bara i captionen rättas med captionen, också för en egen video", () => {
      expect(rejectedFix(failed("v123", [caption]))).toEqual({ kind: "caption" });
      expect(rejectedFix(failed("upload", [caption]))).toEqual({ kind: "caption" });
    });

    it("ett för kort klipp ur ett projekt kan inte bli längre: inte projektsidan, också med fel i captionen", () => {
      expect(rejectedFix(failed("Skyborne_WalkOnAir", [short]))).toEqual({ kind: "kort" });
      expect(rejectedFix(failed("v123", [shortOld]))).toEqual({ kind: "kort" });
      expect(rejectedFix(failed("v123", [caption, short]))).toEqual({ kind: "kort" });
    });

    it("ett för långt klipp kortas med Redigera till profilens tak, med komma i talet", () => {
      expect(rejectedFix(failed("v123", [long]))).toEqual({ kind: "lang", max: "60", caption: false });
      expect(rejectedFix(failed("v123", [longOld]))).toEqual({ kind: "lang", max: "59,5", caption: false });
      expect(rejectedFix(failed("v123", [long, caption]))).toEqual({ kind: "lang", max: "60", caption: true });
      // Redigera finns också för en egen video.
      expect(rejectedFix(failed("upload", [long]))).toEqual({ kind: "lang", max: "60", caption: false });
    });

    it("längd och ett annat fel: gör klippen kampanjklara igen på projektsidan, som förut", () => {
      expect(rejectedFix(failed("v123", [short, sound]))).toEqual({ kind: "projekt" });
      expect(rejectedFix(failed("v123", [long, sound]))).toEqual({ kind: "projekt" });
      expect(rejectedFix(failed("v123", [caption, sound]))).toEqual({ kind: "projekt" });
    });

    it("ingen rad för ett klipp som inte är underkänt, eller en egen video som är för kort", () => {
      // Servern lägger själv till "Ladda upp en längre video eller tryck Nej." på en egen video.
      expect(rejectedFix(failed("upload", [`${short} Ladda upp en längre video eller tryck Nej.`]))).toBeNull();
      expect(rejectedFix(failed("upload", [sound]))).toBeNull();
      expect(rejectedFix({ status: "renderad", vod_id: "v123", problems: [] } as unknown as CampaignClip)).toBeNull();
    });
  });
});
