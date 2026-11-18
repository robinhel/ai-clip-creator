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

describe("speedNote", () => {
  it("säger hastigheten med decimalkomma, och inget i vanlig hastighet", () => {
    expect(speedNote(1.1)).toBe(" Längden gäller med hastigheten 1,1x.");
    expect(speedNote(1)).toBe("");
    expect(speedNote(undefined)).toBe("");
  });
});
