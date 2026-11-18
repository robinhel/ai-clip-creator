import { describe, expect, it } from "vitest";
import { hasOwn, startLabel, startReach } from "./versionOwn";

// Som options från servern: originalets hook och en version med en annan.
const base = { hook_text: true, hooks_used: ["Han missar ALLT", "Det här gick fel"], new_start_s: 3, start_cut_s: 0 };

describe("hasOwn: ny hook, jämförd som _norm i campaign/schedule.py", () => {
  it("samma hook med andra versaler och extra blanksteg är inte ny", () => {
    expect(hasOwn({ ...base, hook: "  han  MISSAR allt " })).toBe(false);
  });

  it("hashtaggar först och sist gör inte hooken ny", () => {
    expect(hasOwn({ ...base, hook: "#clip Han missar ALLT" })).toBe(false);
    expect(hasOwn({ ...base, hook: "Han missar ALLT #fyp #gaming" })).toBe(false);
  });

  it("en hashtag mitt i texten gör hooken ny", () => {
    expect(hasOwn({ ...base, hook: "Han #missar ALLT" })).toBe(true);
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
  const same = { ...base, hook: "Han missar ALLT" };

  it("3 s tidigare eller senare är ny startpunkt", () => {
    expect(hasOwn({ ...same, start_cut_s: -3 })).toBe(true);
    expect(hasOwn({ ...same, start_cut_s: 3 })).toBe(true);
  });

  it("2,9 s åt något håll är inte ny startpunkt", () => {
    expect(hasOwn({ ...same, start_cut_s: 2.9 })).toBe(false);
    expect(hasOwn({ ...same, start_cut_s: -2.9 })).toBe(false);
  });

  it("gränsen kommer från servern: med new_start_s 5 räcker inte 3 s", () => {
    expect(hasOwn({ ...same, new_start_s: 5, start_cut_s: 3 })).toBe(false);
    expect(hasOwn({ ...same, new_start_s: 5, start_cut_s: -4.9 })).toBe(false);
    expect(hasOwn({ ...same, new_start_s: 5, start_cut_s: -5 })).toBe(true);
  });

  it("räknas också i en kampanj utan hook i bilden", () => {
    expect(hasOwn({ ...base, hook_text: false, hook: "", start_cut_s: -3 })).toBe(true);
  });
});

describe("startLabel", () => {
  it("säger riktningen med decimalkomma", () => {
    expect(startLabel(0)).toBe("Samma start som originalet");
    expect(startLabel(-2)).toBe("2 s tidigare");
    expect(startLabel(3.5)).toBe("3,5 s senare");
  });
});

describe("startReach: hur långt startpunkten går att flytta", () => {
  it("nämner båda hållen när båda går", () => {
    expect(startReach(-4.5, 2)).toBe("Högst 4,5 s tidigare och 2 s senare");
  });

  it("nämner bara det håll som går", () => {
    expect(startReach(0, 6)).toBe("Högst 6 s senare");
    expect(startReach(-3, 0)).toBe("Högst 3 s tidigare");
  });

  it("är tom när startpunkten inte går att flytta", () => {
    expect(startReach(0, 0)).toBe("");
  });
});
