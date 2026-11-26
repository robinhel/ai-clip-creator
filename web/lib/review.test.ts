import { describe, expect, it } from "vitest";
import { nextActive } from "./review";

describe("nextActive", () => {
  it("tar nästa klipp efter ja eller nej", () => {
    expect(nextActive([4, 7, 9], 4)).toBe(7);
    expect(nextActive([4, 7, 9], 7)).toBe(9);
  });

  it("tar föregående när det var sista klippet", () => {
    expect(nextActive([4, 7, 9], 9)).toBe(7);
  });

  it("ger null när listan blir tom", () => {
    expect(nextActive([4], 4)).toBeNull();
  });
});
