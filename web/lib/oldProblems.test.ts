import { describe, expect, it } from "vitest";
import { englishProblem } from "./oldProblems";
import { fixHint, rejectedFix } from "./steps";
import type { CampaignClip } from "./types";

const failed = (vod_id: string, problems: string[]) => ({ status: "underkand", vod_id, problems }) as CampaignClip;

describe("old Swedish and new English rule check texts", () => {
  // Klipp från före översättningen har svensk text i klipp.db. Nya klipp har engelsk.
  const old = {
    caption: "Captionen saknar hashtaggen #ad.",
    short: "Klippet är 4,0 s. Kampanjen kräver minst 7 s.",
    long: "Klippet är 72,4 s. Profilens tak är 60 s.",
  };
  const current = {
    caption: "Caption is missing the hashtag #ad.",
    short: "The clip is 4.0 s. The campaign requires at least 7 s.",
    long: "The clip is 72.4 s. The campaign allows at most 60 s.",
  };

  it.each([
    ["old Swedish", old],
    ["new English", current],
  ])("recognizes %s texts in the fix for a rejected clip", (_, texts) => {
    expect(rejectedFix(failed("v123", [texts.caption]))).toEqual({ kind: "caption" });
    expect(rejectedFix(failed("v123", [texts.short]))).toEqual({ kind: "kort" });
    expect(rejectedFix(failed("v123", [texts.long, texts.caption]))).toEqual({ kind: "lang", max: "60", caption: true });
    expect(fixHint(rejectedFix(failed("v123", [texts.long]))!)).toContain("at most 60 s.");
  });

  it("shows an old Swedish problem in English when it is known", () => {
    expect(englishProblem(old.caption)).toBe(current.caption);
    expect(englishProblem(old.short)).toBe(current.short);
    expect(englishProblem(old.long)).toBe(current.long);
    expect(englishProblem("Klippet är 1,7 s. Kampanjen kräver minst 7 s. Ladda upp en längre video eller tryck Nej.")).toBe(
      "The clip is 1.7 s. The campaign requires at least 7 s. Upload a longer video or press No."
    );
    expect(englishProblem("Captionen saknar 'twitch.tv/skiimasktimmy' på första raden.")).toBe(
      "Caption is missing 'twitch.tv/skiimasktimmy' on the first line."
    );
    expect(englishProblem("Klippet saknar ljud. Streamens ljud måste höras.")).toBe(
      "The clip has no sound. The stream's sound must be heard."
    );
  });

  it("keeps new English and unknown texts as they are", () => {
    expect(englishProblem(current.long)).toBe(current.long);
    expect(englishProblem("Något helt annat.")).toBe("Något helt annat.");
  });
});
