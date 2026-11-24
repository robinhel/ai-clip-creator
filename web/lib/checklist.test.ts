import { describe, expect, it } from "vitest";
import { checklist } from "./checklist";
import type { CampaignProfileDetail, ConnectedAccounts } from "./types";

function profile(over: Partial<CampaignProfileDetail> = {}): CampaignProfileDetail {
  return {
    name: "timmy",
    streamer: "Timmy",
    campaign: "Kampanj",
    language: "en",
    channel: "timmy",
    formats: [],
    problems: [],
    ready: true,
    active: true,
    clip_min_s: 15,
    clip_max_s: 45,
    rules: [],
    approval: null,
    links: { content_rewards: "https://contentrewards.com/c/campaigns/x", analytics_form: null },
    overlay: { file: null, required: false, mode: "full", exists: false, url: "", width: null, height: null },
    on_screen_text: { text: "", position: "top", required: false },
    safe_zone: { top: 0.1, bottom: 0.2 },
    caption: { template: "{title}", hashtags: [], must_include: [] },
    payment: { cpm_usd: null, min_views: null, max_payout_per_clip_usd: null },
    clipsmith_config: "config.timmy.yaml",
    facecam: { required: true, camera: true, rects_set: true },
    platforms: ["tiktok", "youtube_shorts"],
    accounts: { tiktok: [], youtube_shorts: [] },
    ...over,
  };
}

const acc = (id: string, connected = true) => ({ id, name: id, connected, used_by: [] });
const both: ConnectedAccounts = { tiktok: [acc("tt1")], youtube_shorts: [acc("yt1")] };
const item = (items: ReturnType<typeof checklist>, id: string) => items.find((i) => i.id === id);

describe("checklist", () => {
  it("visar facecam bara när kampanjen har kamera, och overlay bara när den krävs", () => {
    expect(checklist(profile(), both).map((i) => i.id)).toEqual(["lank", "facecam", "konton"]);
    const zoom = profile({
      facecam: { required: false, camera: false, rects_set: false },
      overlay: { file: null, required: true, mode: "full", exists: false, url: "", width: null, height: null },
    });
    expect(checklist(zoom, both).map((i) => i.id)).toEqual(["lank", "overlay", "konton"]);
  });

  it("länken saknas: punkten pekar på fältet i Ställ in", () => {
    const items = checklist(profile({ links: { content_rewards: null, analytics_form: null } }), both);
    expect(item(items, "lank")).toMatchObject({ done: false, href: "/campaigns/timmy?tab=installningar&del=oversikt#lankar" });
  });

  it("konton: klart när varje plattform har ett inloggat konto, och inget är valt", () => {
    expect(item(checklist(profile(), both), "konton")?.done).toBe(true);
    const noYoutube = checklist(profile(), { tiktok: [acc("tt1")], youtube_shorts: [] });
    expect(item(noYoutube, "konton")).toMatchObject({ done: false, todo: "Connect or choose a YouTube channel." });
  });

  it("konton: ett valt konto som inte är kopplat räknas inte, fast ett annat är kopplat", () => {
    const p = profile({ platforms: ["tiktok"], accounts: { tiktok: [{ id: "borta", name: null }], youtube_shorts: [] } });
    expect(item(checklist(p, both), "konton")?.done).toBe(false);
    const expired = profile({ platforms: ["tiktok"], accounts: { tiktok: [{ id: "tt1", name: "tt1" }], youtube_shorts: [] } });
    expect(item(checklist(expired, { tiktok: [acc("tt1", false)], youtube_shorts: [] }), "konton")?.done).toBe(false);
    expect(item(checklist(expired, both), "konton")?.done).toBe(true);
  });

  it("konton: bara plattformar som appen postar på, och inte klart innan kontona är lästa", () => {
    expect(item(checklist(profile({ platforms: ["instagram_reels"] }), both), "konton")).toBeUndefined();
    expect(item(checklist(profile(), null), "konton")?.done).toBe(false);
  });
});
