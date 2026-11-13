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
  /** false = avstängd: kampanjen syns bara under Kampanjer, inte på Hem, Projekt, Att posta och Statistik. */
  active: boolean;
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
    /** Kostnaden delad i Berget och Claude. Saknas hos en äldre server. */
    cost?: CostParts | null;
  };
  /** Kampanjer vars kanal är VOD:ens kanal. */
  campaigns: string[];
  /** Streamen spelas in just nu. Saknas hos äldre servrar. */
  live?: boolean;
  /** Det som stoppar körningen, till exempel att streamen pågår eller att SSD:n saknas. Saknas hos äldre servrar. */
  problems?: string[];
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
  /** Senaste text för steget, annars null. */
  message?: string | null;
}

/** GET /campaign/runs/{id}. */
/** GET /campaign/setup: samma kontroller som python -m campaign check-setup. */
export interface SetupCheck {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

/** GET /campaign/keys: bara om nyckeln är sparad, aldrig värdet. */
export interface KeyGroup {
  id: string;
  label: string;
  keys: { name: string; label: string; set: boolean }[];
}

/** POST /campaign/keys/test/twitch, och test i svaret på PUT när en Twitch-nyckel ändrades. */
export interface KeyTest {
  ok: boolean;
  message: string;
}

/** GET /campaign/streams: senaste streams för kampanjernas kanaler, med senaste körningen. */
export interface StreamInfo {
  vod_id: string;
  channel: string;
  channel_name: string;
  title: string;
  game: string | null;
  duration_s: number;
  /** null när Twitch inte skickar datumet. */
  created_at: string | null;
  thumbnail_url: string | null;
  campaigns: string[];
  run_id: number | null;
  run_status: RunStatus | null;
  clip_count: number;
  /** Betyget för att klippa streamen. Saknas hos äldre servrar. */
  view_count?: number;
  viewer_clips?: number | null;
  live?: boolean;
  score?: number;
  score_parts?: { clips: number; views: number; game: number };
  /** Kort svensk text om signalerna som gav poäng. */
  reason?: string;
  recommended?: boolean;
}

export interface CampaignRunDetail extends CampaignRunInfo {
  /** Senaste händelsens text, till exempel "Del 3 av 8". */
  message?: string | null;
  /** Del av VOD:en i sekunder. 0 och 0 betyder hela VOD:en; end_s 0 betyder till slutet. */
  start_s?: number | null;
  end_s?: number | null;
  steps: RunStep[];
  eta_s: number | null;
  cost_estimate_usd: number | null;
  cost_estimate?: CostParts | null;
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
  /** Kampanjens minsta visningar. Under dem går statistiken inte att skicka in. */
  min_views?: number | null;
  video_url: string;
  /** JPEG ur klippet, samma ?v= som video_url. Saknas hos äldre servrar. */
  poster_url?: string | null;
  /** Claudes betyg 0–99, null för äldre klipp. */
  score: number | null;
  /** Claudes motivering, tom för äldre klipp. */
  reason: string;
  /** Ett inlägg per plattform. Klippets status, länk och siffror räknas fram ur dem. Saknas hos äldre servrar. */
  posts?: ClipPost[];
}

export type PostStatus = "postad" | "inskickad" | "analytics_inskickad" | "betald";

export interface ClipPost {
  id: number;
  clip_id: number;
  platform: Platform;
  url: string;
  status: PostStatus;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  /** Vad Whop faktiskt betalade för inlägget. */
  paid_usd: number | null;
  created: string;
  updated: string;
  engagement: number | null;
  estimate_usd: number | null;
}

export type ClipPostPatch = Partial<Pick<ClipPost, "status" | "url" | "views" | "likes" | "comments" | "shares" | "paid_usd">>;

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
  /** Saknas hos äldre servrar. */
  earnings?: {
    estimated_usd: number;
    paid_usd: number;
    by_month: { month: string; usd: number }[];
    per_clip: { id: number; views: number | null; usd: number }[];
  };
  /** connected är false när work/ eller out/ är en länk till en SSD som inte sitter i. Saknas hos äldre servrar. */
  disk?: { free_gb: number; total_gb: number; connected?: boolean; problem?: string | null };
}

/** Ett kopplat TikTok-konto. connected false: inloggningen har gått ut. */
export interface TikTokAccount {
  open_id: string;
  display_name: string | null;
  avatar_url: string | null;
  scopes: string[];
  connected: boolean;
  refresh_expires_at: string | null;
  pending_24h: number;
  /** Har scopet video.publish och kan publicera direkt. Saknas hos äldre servrar. */
  can_publish?: boolean;
}

export type TikTokPrivacy = "PUBLIC_TO_EVERYONE" | "MUTUAL_FOLLOW_FRIENDS" | "FOLLOWER_OF_CREATOR" | "SELF_ONLY";

/** GET /campaign/tiktok/accounts/{open_id}/creator_info, färskt från TikTok. */
export interface TikTokCreatorInfo {
  open_id: string;
  nickname: string | null;
  username: string | null;
  avatar_url: string | null;
  privacy_level_options: TikTokPrivacy[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
}

/** POST /campaign/clips/{id}/tiktok. Utan mode: utkast. */
export interface TikTokSendBody {
  open_id?: string;
  mode?: "draft" | "direct";
  title?: string;
  privacy_level?: TikTokPrivacy;
  allow_comment?: boolean;
  allow_duet?: boolean;
  allow_stitch?: boolean;
  brand_organic?: boolean;
  brand_content?: boolean;
}

/** GET /campaign/tiktok/status. Aldrig någon token. Fälten på toppnivå gäller det första kontot. */
export interface TikTokStatus {
  configured: boolean;
  connected: boolean;
  display_name: string | null;
  avatar_url: string | null;
  scopes: string[];
  refresh_expires_at: string | null;
  pending_24h: number;
  pending_limit: number;
  /** Kontona i den ordning de lades till. Saknas hos äldre servrar. */
  accounts?: TikTokAccount[];
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
  /** Kontot utkastet gick till. Saknas hos äldre servrar. */
  open_id?: string | null;
  display_name?: string | null;
  /** draft eller direct. Saknas hos äldre servrar, som bara gör utkast. */
  mode?: "draft" | "direct";
  privacy_level?: TikTokPrivacy | null;
  post_id?: string | null;
  /** Länken till det publicerade inlägget. null för privata inlägg. */
  post_url?: string | null;
}

/** En kopplad YouTube-kanal. needs_login: inloggningen har gått ut (efter 7 dagar i läget Testing). */
export interface YouTubeChannel {
  channel_id: string;
  title: string | null;
  handle: string | null;
  avatar_url: string | null;
  connected: boolean;
  needs_login: boolean;
}

/** GET /campaign/youtube/status. Aldrig någon token. */
export interface YouTubeStatus {
  configured: boolean;
  accounts: YouTubeChannel[];
  uploads_today: number;
}

export type YouTubePrivacy = "public" | "unlisted" | "private";

/** POST /campaign/clips/{id}/youtube. privacy och made_for_kids har inget förval. */
export interface YouTubeSendBody {
  channel_id?: string;
  title: string;
  description: string;
  privacy: YouTubePrivacy;
  made_for_kids: boolean;
  notify_subscribers: boolean;
}

/** Senaste uppladdningen till YouTube, med färsk status. */
export interface YouTubeUpload {
  /** null medan filen laddas upp, eller om uppladdningen misslyckades innan YouTube gav ett id. */
  video_id: string | null;
  url: string | null;
  status: "uploading" | "uploaded" | "processed" | "failed";
  error: string | null;
  privacy: YouTubePrivacy | null;
  channel_id: string | null;
  /** Kanalens namn, inte videons titel. */
  title: string | null;
  /** Bara i svaret på POST. */
  message?: string;
}

export type OverlayMode = "full" | "top" | "bottom";
export type TextPosition = "top" | "bottom";

/** GET /campaign/profiles/{id}: en rad i listan plus detaljerna. */
export interface CampaignProfileDetail extends CampaignProfile {
  /** other_rules i profilen, till exempel "Kommentarer och gilla-markeringar ska vara påslagna". */
  rules: string[];
  /** Hur klippen godkänns, ur profilens approval. */
  approval: string | null;
  /** https-länkar ur profilens links, annars null. */
  links: { content_rewards: string | null; analytics_form: string | null };
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
  /** Kamerans positioner, den första förvald. Saknas hos äldre servrar; då gäller webcam_rect. */
  webcam_rects?: Rect[] | null;
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
  /** null när råklippet saknas; då är editable false. */
  original: { duration_s: number; start_s: number | null; end_s: number | null; url: string } | null;
  trim: { in_s: number; out_s: number } | null;
  /** Kampanjens gränser för klipplängden. */
  min_s: number | null;
  max_s: number | null;
  editable: boolean;
  reason: string | null;
}

/** Ungefärlig kostnad för en körning: Berget transkriberar, Claude väljer och granskar. */
export interface CostParts {
  berget_usd: number;
  claude_usd: number;
  usd: number;
}
