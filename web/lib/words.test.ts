import { describe, expect, it } from "vitest";
import { inRange, lineAt, nudge } from "./words";

const words = [
  { start: 1, end: 1.4, word: "Bro," },
  { start: 1.5, end: 1.8, word: "look" },
  { start: 2, end: 2.3, word: "at" },
];
const w = (start: number, word: string, len = 0.2) => ({ start, end: start + len, word });
const text = (line: { word: string }[] | null) => line?.map((x) => x.word).join(" ");

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

describe("inRange", () => {
  it("tar samma ord som servern bränner in: start i intervallet och slut högst 0,5 s efter ut", () => {
    const list = [w(0.5, "före"), w(1, "start"), w(5, "mitt"), { start: 9.8, end: 10.5, word: "över" }, { start: 9.9, end: 10.6, word: "för långt" }, w(10, "vid ut")];
    expect(list.filter((x) => inRange(x, 1, 10)).map((x) => x.word)).toEqual(["start", "mitt", "över"]);
  });
});

describe("lineAt, box", () => {
  it("är standard och har högst 3 ord per rad", () => {
    const many = [0, 0.3, 0.6, 0.9, 1.2].map((s) => w(s, "ord"));
    expect(lineAt(many, 0)?.length).toBe(3);
    expect(lineAt(many, 1)?.length).toBe(2);
    expect(lineAt(many, 0, "box")?.length).toBe(3);
  });

  it("ett ord som inte ryms på 18 tecken startar en ny rad", () => {
    const list = [w(0, "Absolutely"), w(0.3, "insane"), w(0.6, "play")];
    expect(text(lineAt(list, 0))).toBe("Absolutely insane");
    expect(text(lineAt(list, 0.6))).toBe("play");
  });

  it("ger null i en paus", () => {
    expect(lineAt(words, 0.5)).toBeNull();
    expect(lineAt(words, 3)).toBeNull();
  });
});

describe("lineAt, outline", () => {
  it("har högst 5 ord per rad", () => {
    const many = Array.from({ length: 7 }, (_, i) => w(i * 0.3, "ord"));
    expect(lineAt(many, 0, "outline")?.length).toBe(5);
    expect(lineAt(many, 1.6, "outline")?.length).toBe(2);
  });

  it("delar när raden når 28 tecken", () => {
    const list = [w(0, "aaaaaaaaaaaaaa"), w(0.3, "bbbbbbbbbbbbb"), w(0.6, "c")];
    expect(text(lineAt(list, 0, "outline"))).toBe("aaaaaaaaaaaaaa bbbbbbbbbbbbb");
    expect(text(lineAt(list, 0.6, "outline"))).toBe("c");
  });
});

describe("pauser", () => {
  it("räknar ordets slut som högst 1 s efter starten", () => {
    // Whisper drog ut ordet till 3 s; det sades till 1 s, så 1,7 s är en paus på 0,7 s.
    const list = [{ start: 0, end: 3, word: "Yo" }, w(1.7, "bro")];
    expect(text(lineAt(list, 0, "outline"))).toBe("Yo");
    expect(text(lineAt(list, 1.7, "outline"))).toBe("bro");
  });
});
