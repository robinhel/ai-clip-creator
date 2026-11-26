import { describe, expect, it } from "vitest";
import { formatForKey, nextActive } from "./review";

describe("nextActive", () => {
  it("tar nästa klipp efter ja eller nej", () => {
    expect(nextActive([4, 7, 9], 4)).toBe(7);
    expect(nextActive([4, 7, 9], 7)).toBe(9);
  });

  it("tar föregående när det var sista klippet", () => {
    expect(nextActive([4, 7, 9], 9)).toBe(7);
  });

  it("ger null när listan blir tom eller klippet inte finns", () => {
    expect(nextActive([4], 4)).toBeNull();
    expect(nextActive([], 4)).toBeNull();
    expect(nextActive([4, 7], 5)).toBe(4);
  });
});

describe("formatForKey", () => {
  const formats = [{ id: "reaction" }, { id: "clutch" }, { id: "fail" }];

  it("1 är första formatet", () => {
    expect(formatForKey("1", formats)).toBe("reaction");
    expect(formatForKey("3", formats)).toBe("fail");
  });

  it("ger null för siffror utanför listan och andra tangenter", () => {
    expect(formatForKey("0", formats)).toBeNull();
    expect(formatForKey("4", formats)).toBeNull();
    expect(formatForKey("y", formats)).toBeNull();
    expect(formatForKey("1", [])).toBeNull();
  });
});
