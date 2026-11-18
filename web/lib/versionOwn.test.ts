import { describe, expect, it } from "vitest";
import { hasOwn, isNewStart, norm, startLabel } from "./versionOwn";

// Som options från servern: originalets hook och en version med en annan.
const base = { hook_text: true, hooks_used: ["Han missar ALLT", "Det här gick fel"], new_start_s: 3, start_cut_s: 0 };

describe("norm, som _norm i campaign/schedule.py", () => {
  it("bortser från versaler, blanksteg och hashtaggar först och sist", () => {
    expect(norm("  Han   missar ALLT ")).toBe(norm("han missar allt"));
    expect(norm("#clip Han missar allt #fyp #gaming")).toBe(norm("han missar allt"));
  });

  it("behåller hashtaggar mitt i texten", () => {
    expect(norm("han #missar allt")).not.toBe(norm("han missar allt"));
  });
});

describe("hasOwn: ny hook", () => {
  it("samma hook med andra versaler och extra blanksteg är inte ny", () => {
    expect(hasOwn({ ...base, hook: "  han  MISSAR allt " })).toBe(false);
  });

  it("en hashtag sist gör inte hooken ny", () => {
    expect(hasOwn({ ...base, hook: "Han missar ALLT #fyp" })).toBe(false);
  });

  it("en versions hook räknas också som använd", () => {
    expect(hasOwn({ ...base, hook: "det här gick FEL" })).toBe(false);
  });

  it("en annan hook är ny", () => {
    expect(hasOwn({ ...base, hook: "Ingen trodde att han skulle klara det" })).toBe(true);
  });

  it("tom hook eller bara blanksteg är inte ny", () => {
    expect(hasOwn({ ...base, hook: "" })).toBe(false);
    expect(hasOwn({ ...base, hook: "   " })).toBe(false);
  });

  it("kampanj utan hook i bilden räknar inte hooken", () => {
    expect(hasOwn({ ...base, hook_text: false, hook: "Ingen trodde att han skulle klara det" })).toBe(false);
  });
});

describe("hasOwn: ny startpunkt", () => {
  it("3 s tidigare eller senare är ny startpunkt", () => {
    expect(isNewStart(-3, 3)).toBe(true);
    expect(isNewStart(3, 3)).toBe(true);
    expect(hasOwn({ ...base, hook: "Han missar ALLT", start_cut_s: -3 })).toBe(true);
    expect(hasOwn({ ...base, hook: "Han missar ALLT", start_cut_s: 3 })).toBe(true);
  });

  it("2,9 s åt något håll är inte ny startpunkt", () => {
    expect(isNewStart(2.9, 3)).toBe(false);
    expect(isNewStart(-2.9, 3)).toBe(false);
    expect(hasOwn({ ...base, hook: "Han missar ALLT", start_cut_s: 2.9 })).toBe(false);
    expect(hasOwn({ ...base, hook: "Han missar ALLT", start_cut_s: -2.9 })).toBe(false);
  });

  it("räknas också i en kampanj utan hook i bilden", () => {
    expect(hasOwn({ ...base, hook_text: false, hook: "", start_cut_s: -3 })).toBe(true);
  });

  it("tål avrundning i reglaget", () => {
    expect(isNewStart(2.9999999, 3)).toBe(true);
    expect(isNewStart(-2.9999999, 3)).toBe(true);
  });
});

describe("startLabel", () => {
  it("säger riktningen med decimalkomma", () => {
    expect(startLabel(0)).toBe("Samma start som originalet");
    expect(startLabel(-2)).toBe("2 s tidigare");
    expect(startLabel(3.5)).toBe("3,5 s senare");
  });
});
