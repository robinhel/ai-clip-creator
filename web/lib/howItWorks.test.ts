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
      summary({}, 0),
      [],
      new Date()
    );
    expect(stats.vods).toBe(2);
    expect(stats.hours).toBe(3);
  });

  it("counts every clip made, and everything from approved onwards as approved", () => {
    const stats = pipelineStats([], summary({ renderad: 4, underkand: 2, nekad: 3, godkand: 1, postad: 2, betald: 1 }, 0), [], new Date());
    expect(stats.clips).toBe(13);
    expect(stats.approved).toBe(4);
  });

  const NOW = new Date("2026-11-24T12:00:00Z");
  const made = (...created: (string | null)[]) => created.map((c) => ({ created: c }));

  it("gives the AI cost per clip from this month's spend and this month's clips", () => {
    const clips = made("2026-11-01T00:00:00Z", "2026-11-20T10:00:00", "2026-11-24T08:00:00+00:00", "2026-11-03T09:00:00Z");
    expect(pipelineStats([], summary({ renderad: 4 }, 2), clips, NOW).costPerClip).toBe(0.5);
    expect(pipelineStats([], null, clips, NOW).costPerClip).toBeNull();
  });

  it("does not count clips from another month, in UTC like the budget", () => {
    // 23:30 on 31 Oct in UTC is 1 Nov in Stockholm, but the budget counts that as October.
    const clips = made("2026-10-31T23:30:00Z", "2026-10-15T12:00:00Z", "2025-11-10T12:00:00Z", null, "2026-11-02T12:00:00Z");
    expect(pipelineStats([], summary({ renderad: 5 }, 3), clips, NOW).costPerClip).toBe(3);
  });

  it("gives no number, not Infinity or NaN, without clips this month", () => {
    const stats = pipelineStats([], summary({ renderad: 2 }, 2), made("2026-10-20T12:00:00Z", "2026-10-21T12:00:00Z"), NOW);
    expect(stats.costPerClip).toBeNull();
    expect(pipelineStats([], summary({}, 0), [], NOW).costPerClip).toBeNull();
  });

  it("is empty when nothing has run", () => {
    expect(pipelineStats([], null, [], new Date()).empty).toBe(true);
    expect(pipelineStats([run("1", "done", 60)], null, [], new Date()).empty).toBe(false);
  });
});
