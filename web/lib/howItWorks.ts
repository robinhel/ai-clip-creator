import type { CampaignRunInfo, CampaignStatus, CampaignSummary } from "./types";
import { serverTime } from "./utils";

// Approved, and everything after it: a posted or paid clip was approved first.
const APPROVED: CampaignStatus[] = ["godkand", "postad", "postad_utan_lank", "inskickad", "analytics_inskickad", "betald"];

export interface PipelineStats {
  /** Finished VODs, each counted once even when it was run again. */
  vods: number;
  /** Hours of video in those VODs. */
  hours: number;
  clips: number;
  approved: number;
  /** This month's AI spend divided by the clips made this month. null without such clips or budget. */
  costPerClip: number | null;
  empty: boolean;
}

/** Made in the same calendar month as `now`, in UTC: the budget (campaign/budget.py) counts its month in UTC too. */
function thisMonth(created: string | null, now: Date): boolean {
  if (!created) return false;
  const when = new Date(serverTime(created));
  return when.getUTCFullYear() === now.getUTCFullYear() && when.getUTCMonth() === now.getUTCMonth();
}

/** The four numbers on How it works, from GET /campaign/runs, GET /campaign/summary and GET /campaign/clips. */
export function pipelineStats(
  runs: CampaignRunInfo[],
  summary: CampaignSummary | null,
  clipList: { created: string | null }[],
  now: Date
): PipelineStats {
  const done = new Map<string, number>();
  for (const run of runs) if (run.status === "done") done.set(run.vod_id, run.duration_s ?? 0);
  const counts = summary?.counts ?? {};
  const clips = Object.values(counts).reduce((sum: number, n) => sum + (n ?? 0), 0);
  const approved = APPROVED.reduce((sum, status) => sum + (counts[status] ?? 0), 0);
  const spent = summary?.budget?.month_usd;
  const clipsThisMonth = clipList.filter((c) => thisMonth(c.created, now)).length;
  return {
    vods: done.size,
    hours: Math.round(([...done.values()].reduce((a, b) => a + b, 0) / 3600) * 10) / 10,
    clips,
    approved,
    costPerClip: clipsThisMonth > 0 && spent != null ? spent / clipsThisMonth : null,
    empty: done.size === 0 && clips === 0,
  };
}
