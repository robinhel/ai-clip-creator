import type { CampaignRunInfo, CampaignStatus, CampaignSummary } from "./types";

// Approved, and everything after it: a posted or paid clip was approved first.
const APPROVED: CampaignStatus[] = ["godkand", "postad", "postad_utan_lank", "inskickad", "analytics_inskickad", "betald"];

export interface PipelineStats {
  /** Finished VODs, each counted once even when it was run again. */
  vods: number;
  /** Hours of video in those VODs. */
  hours: number;
  clips: number;
  approved: number;
  /** This month's AI spend divided by the clips. null without clips or budget. */
  costPerClip: number | null;
  empty: boolean;
}

/** The four numbers on How it works, from GET /campaign/runs and GET /campaign/summary. */
export function pipelineStats(runs: CampaignRunInfo[], summary: CampaignSummary | null): PipelineStats {
  const done = new Map<string, number>();
  for (const run of runs) if (run.status === "done") done.set(run.vod_id, run.duration_s ?? 0);
  const counts = summary?.counts ?? {};
  const clips = Object.values(counts).reduce((sum: number, n) => sum + (n ?? 0), 0);
  const approved = APPROVED.reduce((sum, status) => sum + (counts[status] ?? 0), 0);
  const spent = summary?.budget?.month_usd;
  return {
    vods: done.size,
    hours: Math.round(([...done.values()].reduce((a, b) => a + b, 0) / 3600) * 10) / 10,
    clips,
    approved,
    costPerClip: clips > 0 && spent != null ? spent / clips : null,
    empty: done.size === 0 && clips === 0,
  };
}
