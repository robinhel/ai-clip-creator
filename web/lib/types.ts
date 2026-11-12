export type RunStatus = "pending" | "running" | "done" | "failed";

export interface Run {
  id: number;
  vod_id: string;
  channel: string;
  status: RunStatus;
  stage: string | null;
  error: string | null;
  prompt_version: string;
  created_at: string;
  updated_at: string;
  clip_count: number;
}

export interface Clip {
  id: number;
  run_id: number;
  filename: string;
  title: string;
  start_s: number;
  end_s: number;
  score: number;
  approved: boolean | null;
  published_url: string | null;
  signal_breakdown: Record<string, number> | null;
  created_at: string;
}

export interface PipelineEvent {
  id: number;
  run_id: number;
  stage: string;
  pct: number;
  message: string;
  created_at: string;
}


// Kampanjlagret (campaign/api.py)

export type CampaignStatus =
  | "renderad"
  | "underkand"
  | "godkand"
  | "nekad"
  | "postad"
  | "inskickad"
  | "analytics_inskickad"
  | "betald";

export type Platform = "tiktok" | "youtube_shorts" | "instagram_reels";

export interface CampaignFormat {
  id: string;
  name: string;
  description: string;
}

export interface CampaignProfile {
  name: string;
  streamer: string;
  campaign: string;
  language: string;
  channel: string;
  formats: CampaignFormat[];
  problems: string[];
  ready: boolean;
}

export interface CampaignRunCreate {
  profile: string;
  vod_id: string;
  game?: string;
  start_s?: number;
  end_s?: number;
}

export interface CampaignRunInfo {
  run_id: number;
  profile: string;
  game: string | null;
  vod_id: string;
}

export interface CampaignClip {
  id: number;
  vod_id: string;
  profile: string;
  start_s: number | null;
  end_s: number | null;
  title: string;
  format: string | null;
  signals: string[];
  filename: string;
  caption: string;
  status: CampaignStatus;
  problems: string[];
  warnings: string[];
  review: Record<string, unknown> | null;
  platform: Platform | null;
  url: string | null;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  note: string | null;
  created: string;
  updated: string;
  engagement: number | null;
  engagement_min: number | null;
  video_url: string;
}

export type CampaignClipPatch = Partial<
  Pick<
    CampaignClip,
    | "status"
    | "caption"
    | "title"
    | "format"
    | "platform"
    | "url"
    | "views"
    | "likes"
    | "comments"
    | "shares"
    | "note"
  >
>;

export interface CampaignSummary {
  budget: {
    month_usd: number;
    month_cap_usd: number;
    today_usd: number;
    day_cap_usd: number;
    by_purpose: Record<string, { calls: number; usd: number }>;
  };
  counts: Partial<Record<CampaignStatus, number>>;
  todo: string[];
}
