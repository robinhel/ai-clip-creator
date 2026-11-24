import { describe, expect, it } from "vitest";
import { pipelineStats } from "./howItWorks";
import type { CampaignRunInfo, CampaignSummary } from "./types";

const run = (vod_id: string, status: CampaignRunInfo["status"], duration_s: number | null): CampaignRunInfo => ({
  run_id: Number(vod_id),
  vod_id,
  profile: "p",
  game: null,
  focus: null,
  min_s: null,
  max_s: null,
  title: null,
  channel_name: null,
  thumbnail_url: null,
  duration_s,
  created: null,
  status,
  stage: null,
  pct: null,
  error: null,
  clip_counts: {},
});

const summary = (counts: CampaignSummary["counts"], monthUsd: number, byPurpose: Record<string, number> = {}): CampaignSummary => ({
  budget: {
    month_usd: monthUsd,
    month_cap_usd: 200,
    today_usd: 0,
    day_cap_usd: 10,
    by_purpose: Object.fromEntries(Object.entries(byPurpose).map(([k, usd]) => [k, { calls: 1, usd }])),
  },
  counts,
  todo: [],
});

describe("pipelineStats", () => {
  it("counts each finished VOD once, with its hours", () => {
    const stats = pipelineStats(
      [run("1", "done", 7200), run("1", "done", 7200), run("2", "done", 3600), run("3", "failed", 9000), run("4", "running", 100)],
      summary({}, 0)
    );
    expect(stats.vods).toBe(2);
    expect(stats.hours).toBe(3);
  });

  it("counts every clip made, and everything from approved onwards as approved", () => {
    const stats = pipelineStats([], summary({ renderad: 4, underkand: 2, nekad: 3, godkand: 1, postad: 2, betald: 1 }, 0));
    expect(stats.clips).toBe(13);
    expect(stats.approved).toBe(4);
  });

  it("gives the AI cost per clip from this month's spend, and null without clips", () => {
    expect(pipelineStats([], summary({ renderad: 4 }, 2)).costPerClip).toBe(0.5);
    expect(pipelineStats([], summary({}, 2)).costPerClip).toBeNull();
    expect(pipelineStats([], null).costPerClip).toBeNull();
  });

  it("is empty when nothing has run", () => {
    expect(pipelineStats([], null).empty).toBe(true);
    expect(pipelineStats([run("1", "done", 60)], null).empty).toBe(false);
  });
});
