import { describe, expect, it } from "vitest";
import { campaignLabel, hasCampaign, NO_CAMPAIGN } from "./campaignName";

describe("campaignLabel", () => {
  it("visar den dolda kampanjen som Utan kampanj", () => {
    expect(campaignLabel(NO_CAMPAIGN)).toBe("No campaign");
    expect(NO_CAMPAIGN).toBe("_ingen"); // samma som NO_CAMPAIGN i campaign/rules.py
  });

  it("visar en riktig kampanj med sitt namn", () => {
    expect(campaignLabel("skiimasktimmy")).toBe("skiimasktimmy");
  });
});

describe("hasCampaign", () => {
  it("är falskt bara för den dolda kampanjen, som inte har någon profil att hämta", () => {
    expect(hasCampaign(NO_CAMPAIGN)).toBe(false);
    expect(hasCampaign("skiimasktimmy")).toBe(true);
  });
});
