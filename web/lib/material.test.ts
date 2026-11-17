import { describe, expect, it } from "vitest";
import { groupByWeek, isoWeek } from "./material";

const at = (y: number, m: number, d: number, h = 12) => new Date(y, m - 1, d, h).toISOString();

describe("isoWeek", () => {
  it("räknar veckor som i Sverige, med måndag först", () => {
    expect(isoWeek(new Date(2026, 10, 16))).toBe(47); // måndag
    expect(isoWeek(new Date(2026, 10, 22))).toBe(47); // söndag
    expect(isoWeek(new Date(2026, 10, 23))).toBe(48);
    expect(isoWeek(new Date(2027, 0, 1))).toBe(53); // fredag 1 jan hör till förra årets sista vecka
    expect(isoWeek(new Date(2024, 11, 30))).toBe(1); // måndag 30 dec hör till nästa års vecka 1
    expect(isoWeek(new Date(2026, 3, 9))).toBe(15); // efter omställningen till sommartid
  });
});

describe("groupByWeek", () => {
  const now = new Date(2026, 10, 17, 9).getTime(); // tisdag
  const items = [
    { id: "a", created: at(2026, 11, 16) },
    { id: "b", created: at(2026, 11, 15, 23) }, // söndag kväll: veckan före
    { id: "c", created: at(2026, 11, 9) },
    { id: "d", created: at(2026, 10, 1) }, // äldre än 4 veckor
    { id: "e", created: null },
  ];

  it("delar per vecka, nyast först, och lägger det äldre än 4 veckor för sig", () => {
    const { recent, older } = groupByWeek(items, now);
    expect(recent.map((g) => [g.week, g.items.map((x) => x.id)])).toEqual([
      [47, ["a"]],
      [46, ["b", "c"]],
    ]);
    expect(older.map((x) => x.id)).toEqual(["d", "e"]);
  });

  it("veckans måndag är gruppens start", () => {
    const { recent } = groupByWeek(items, now);
    expect(recent[1].start.getDate()).toBe(9);
  });
});
