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
  /** Kampanjens tillåtna klipplängd i sekunder. null om inställningsfilen saknas. */
  clip_min_s: number | null;
  clip_max_s: number | null;
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
    cost_usd: number | null;
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
  created: string | null;
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

/** GET /campaign/tiktok/status. Aldrig någon token. */
export interface TikTokStatus {
  configured: boolean;
  connected: boolean;
  display_name: string | null;
  avatar_url: string | null;
  scopes: string[];
  refresh_expires_at: string | null;
  pending_24h: number;
  pending_limit: number;
}

/** Senaste uppladdningen av ett klipp till TikTok. status: uploading, uploaded, failed eller TikToks egen. */
export interface TikTokUpload {
  clip_id: number;
  publish_id: string;
  status: string;
  error: string | null;
  created: string;
  /** Bara i svaret på POST. */
  message?: string;
}

export type OverlayMode = "full" | "top" | "bottom";
export type TextPosition = "top" | "bottom";

/** GET /campaign/profiles/{id}: en rad i listan plus detaljerna. */
export interface CampaignProfileDetail extends CampaignProfile {
  overlay: {
    file: string | null;
    required: boolean;
    mode: OverlayMode;
    exists: boolean;
    url: string;
    width: number | null;
    height: number | null;
  };
  on_screen_text: { text: string; position: TextPosition; required: boolean };
  safe_zone: { top: number; bottom: number };
  caption: { template: string; hashtags: string[]; must_include: string[] };
  payment: { cpm_usd: number | null; min_views: number | null; max_payout_per_clip_usd: number | null };
  clipsmith_config: string | null;
}

/** [x, y, bredd, höjd] i källbildens pixlar (1920x1080). */
export type Rect = [number, number, number, number];

export interface ReframeInfo {
  mode: string;
  split_ratio: number;
  webcam_rect: Rect | null;
  gameplay_rect: Rect | null;
  source_width: number;
  source_height: number;
  webcam_aspect: number;
  gameplay_aspect: number;
}

/** POST /campaign/profiles. Bara channel och streamer krävs. */
export interface CampaignProfileCreate {
  channel: string;
  streamer: string;
  campaign?: string;
  language?: string;
  clip_min_s?: number;
  clip_max_s?: number;
  on_screen_text?: string;
  text_position?: TextPosition;
  overlay_required?: boolean;
  overlay_mode?: OverlayMode;
  facecam_required?: boolean;
  must_include?: string[];
  hashtags?: string[];
  caption_template?: string;
  formats?: { name: string; description: string }[];
  cpm_usd?: number;
  min_views?: number;
  max_payout_per_clip_usd?: number;
  engagement_min_percent?: number;
}

/** GET /campaign/clips/{id}/edit. Tider i sekunder inne i råklippet. */
export interface ClipEditInfo {
  clip_id: number;
  title: string;
  original: { duration_s: number; start_s: number | null; end_s: number | null; url: string };
  trim: { in_s: number; out_s: number };
  /** Kampanjens gränser för klipplängden. */
  min_s: number | null;
  max_s: number | null;
  editable: boolean;
  reason: string | null;
}
