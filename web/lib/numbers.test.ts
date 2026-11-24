import { describe, expect, it } from "vitest";
import { numberText, parseNumber } from "./numbers";

describe("parseNumber: numbers in English format", () => {
  it("reads a comma as a thousands separator", () => {
    expect(parseNumber("10,000")).toBe(10000);
    expect(parseNumber("1,000.50")).toBe(1000.5);
    expect(parseNumber("2,500")).toBe(2500);
    expect(parseNumber("1,234,567")).toBe(1234567);
  });

  it("reads a point as the decimal point, and spaces are ignored", () => {
    expect(parseNumber("1.5")).toBe(1.5);
    expect(parseNumber(" 150 ")).toBe(150);
    expect(parseNumber("")).toBeNull();
  });

  it("a comma that is not a thousands separator is an error, not a decimal comma", () => {
    expect(parseNumber("1,5")).toBeNaN();
    expect(parseNumber("10,00")).toBeNaN();
    expect(parseNumber("1,000,5")).toBeNaN();
    expect(parseNumber("abc")).toBeNaN();
  });
});

describe("numberText", () => {
  it("shows a decimal with a point", () => {
    expect(numberText(1.5)).toBe("1.5");
    expect(numberText(10000)).toBe("10000");
    expect(numberText(null)).toBe("");
  });

  it("gives back the same number when it is read again", () => {
    for (const value of [0.8, 1.5, 150, 10000]) expect(parseNumber(numberText(value))).toBe(value);
  });
});
