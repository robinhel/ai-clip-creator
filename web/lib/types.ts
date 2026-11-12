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
  /** Kampanjens tillåtna klipplängd i sekunder. */
  clip_min_s: number;
  clip_max_s: number;
}

export interface CampaignRunCreate {
  profile: string;
  vod_id: string;
  game?: string;
  start_s?: number;
  end_s?: number;
  focus?: string;
  min_s?: number;
  max_s?: number;
}

/** GET /campaign/vod/{id}: fakta om en Twitch-VOD innan körningen. */
export interface VodInfo {
  vod_id: string;
  title: string;
  channel: string;
  channel_name: string;
  game: string | null;
  duration_s: number;
  created_at: string;
  thumbnail_url: string | null;
  downloaded: boolean;
  estimate: {
    download_gb: number;
    disk_free_gb: number;
    fits: boolean;
    minutes: number;
    cost_usd: number;
  };
  /** Kampanjer vars kanal är VOD:ens kanal. */
  campaigns: string[];
}

/** En rad i GET /campaign/runs. Äldre körningar saknar titel, bild och längd. */
export interface CampaignRunInfo {
  run_id: number;
  vod_id: string;
  profile: string;
  game: string | null;
  focus: string | null;
  min_s: number | null;
  max_s: number | null;
  title: string | null;
  channel_name: string | null;
  thumbnail_url: string | null;
  duration_s: number | null;
  created: string;
  status: RunStatus;
  stage: string | null;
  pct: number | null;
  error: string | null;
  clip_counts: Partial<Record<CampaignStatus, number>>;
}

export type StepStatus = "waiting" | "running" | "done" | "failed";

export interface RunStep {
  id: string;
  label: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  estimate_s: number | null;
}

/** GET /campaign/runs/{id}. */
export interface CampaignRunDetail extends CampaignRunInfo {
  steps: RunStep[];
  eta_s: number | null;
  cost_estimate_usd: number | null;
}

export interface ClipTranscript {
  text: string;
  segments: { start: number; end: number; text: string }[];
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
  /** Claudes betyg 0–99, null för äldre klipp. */
  score: number | null;
  /** Claudes motivering, tom för äldre klipp. */
  reason: string;
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
  disk?: { free_gb: number; total_gb: number };
}
