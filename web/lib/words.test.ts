import { describe, expect, it } from "vitest";
import { lineAt, nudge } from "./words";

const words = [
  { start: 1, end: 1.4, word: "Bro," },
  { start: 1.5, end: 1.8, word: "look" },
  { start: 2, end: 2.3, word: "at" },
];

describe("nudge", () => {
  it("flyttar start och slut lika mycket", () => {
    expect(nudge(words, 1, 0.1, 30)[1]).toEqual({ start: 1.6, end: 1.9, word: "look" });
    expect(nudge(words, 1, -0.1, 30)[1]).toEqual({ start: 1.4, end: 1.7, word: "look" });
  });

  it("håller starten mellan grannordens starter", () => {
    expect(nudge(words, 1, -1, 30)[1].start).toBe(1);
    expect(nudge(words, 1, 1, 30)[1].start).toBe(2);
    expect(nudge(words, 0, -5, 30)[0].start).toBe(0);
  });

  it("håller slutet efter starten och inom klippet", () => {
    const last = nudge(words, 2, 5, 2.4)[2];
    expect(last.start).toBeLessThan(last.end);
    expect(last.end).toBeLessThanOrEqual(2.4);
  });

  it("ändrar inte de andra orden", () => {
    const moved = nudge(words, 1, 0.1, 30);
    expect(moved[0]).toBe(words[0]);
    expect(moved[2]).toBe(words[2]);
  });
});

describe("lineAt", () => {
  it("ger raden som sägs, och null i en paus", () => {
    expect(lineAt(words, 1.6)?.map((w) => w.word)).toEqual(["Bro,", "look", "at"]);
    expect(lineAt(words, 0.5)).toBeNull();
    expect(lineAt(words, 3)).toBeNull();
  });

  it("delar efter 5 ord", () => {
    const many = Array.from({ length: 7 }, (_, i) => ({ start: i * 0.3, end: i * 0.3 + 0.2, word: "ord" }));
    expect(lineAt(many, 0)?.length).toBe(5);
    expect(lineAt(many, 1.6)?.length).toBe(2);
  });
});
