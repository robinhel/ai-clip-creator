import { describe, expect, it } from "vitest";
import { speedNote, trimLength } from "./editLength";

// Taket 60 s, som servern: längden får vara högst max_s.
const fits = (length: number) => length <= 60;

describe("trimLength i Redigera", () => {
  it("delar råtiden med hastigheten, som servern", () => {
    expect(trimLength(0, 63, 1.1)).toBe(57.273);
    expect(fits(trimLength(0, 63, 1.1))).toBe(true);
  });

  it("för lång även med hastigheten", () => {
    expect(trimLength(0, 67, 1.1)).toBe(60.909);
    expect(fits(trimLength(0, 67, 1.1))).toBe(false);
  });

  it("utan hastighet som förut", () => {
    expect(trimLength(2, 63)).toBe(61);
    expect(trimLength(2, 63, undefined)).toBe(61);
    expect(fits(trimLength(0, 63))).toBe(false);
    expect(fits(trimLength(0, 60))).toBe(true);
  });
});

describe("trimLength med length_extra_s", () => {
  // Kampanjens tak 45 s. Servern räknar round((ut − in) / hastighet + extra, 3) och godkänner längder upp till taket.
  const fits45 = (length: number) => length <= 45;

  it("lägger till serverns extra tid, som servern", () => {
    expect(trimLength(0, 49.39, 1.1, 0.1)).toBe(45);
    expect(fits45(trimLength(0, 49.39, 1.1, 0.1))).toBe(true);
  });

  it("över taket med extra tid nekas", () => {
    expect(trimLength(0, 49.5, 1.1, 0.1)).toBe(45.1);
    expect(fits45(trimLength(0, 49.5, 1.1, 0.1))).toBe(false);
    // 49,4 / 1,1 = 44,909, plus 0,1 blir 45,009: över taket, som hos servern.
    expect(trimLength(0, 49.4, 1.1, 0.1)).toBe(45.009);
    expect(fits45(trimLength(0, 49.4, 1.1, 0.1))).toBe(false);
  });

  it("utan extra tid som förut", () => {
    expect(trimLength(0, 49.4, 1.1)).toBe(44.909);
    expect(trimLength(0, 49.4, 1.1, 0)).toBe(44.909);
    expect(trimLength(0, 49.4, 1.1, undefined)).toBe(44.909);
  });
});

describe("speedNote", () => {
  it("säger hastigheten med decimalkomma, och inget i vanlig hastighet", () => {
    expect(speedNote(1.1)).toBe(" The length applies at speed 1.1x.");
    expect(speedNote(1)).toBe("");
    expect(speedNote(undefined)).toBe("");
  });
});
