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
  /** Claudes delpoäng 0–25 vardera. null när Claude inte gav dem (äldre klipp, snabbläget). Saknas hos äldre servrar. */
  subscores?: ClipSubscores | null;
  /** Ett inlägg per plattform. Klippets status, länk och siffror räknas fram ur dem. Saknas hos äldre servrar. */
  posts?: ClipPost[];
  /** Kampanjens sida i Content Rewards (https), annars null. Saknas hos äldre servrar. */
  content_rewards_url?: string | null;
  /** Plattformarna kampanjen postar på. Saknas hos äldre servrar: alla. */
  platforms?: Platform[];
  /** Id för klippet som den här versionen gjordes av. null eller saknas: inte en version. */
  version_of?: number | null;
}

export type StickerPosition = "top_left" | "top_right" | "center" | "bottom_left" | "bottom_right";

/** Klistermärket i en ny version: en PNG ur stickers/, var och när den syns. */
export interface VersionSticker {
  file: string;
  position: StickerPosition;
  start_s: number;
  duration_s: number;
}

/** GET /campaign/clips/{id}/versions/options. defaults är kontots fasta stil eller kampanjens standard. */
export interface VersionOptions {
  allowed: boolean;
  reason: string | null;
  fonts: { name: string; label: string }[];
  stickers: { file: string; url: string }[];
  overlays: { file: string; url: string }[];
  /** Kampanjen kräver sin egen overlay; den går inte att byta. */
  overlay_locked: boolean;
  max_start_cut_s: number;
  speeds: number[];
  filters: string[];
  /** font null: originalets. overlay null: kampanjens overlay (eller ingen, om kampanjen inte har någon). */
  defaults: { font: string | null; overlay: string | null; sticker: VersionSticker | null; start_cut_s: number };
}

/** GET /campaign/uploads/options. Samma bilder som en ny version, efter kampanjens regler. */
export interface UploadOptions {
  fonts: { name: string; label: string }[];
  stickers: { file: string; url: string }[];
  overlays: { file: string; url: string }[];
  /** Kampanjen kräver sin egen overlay; den går inte att byta. */
  overlay_locked: boolean;
  max_start_cut_s: number;
  /** Kampanjens kortaste klipp i sekunder. */
  min_seconds: number;
  /** false: kampanjen bränner inte in rubriken, så fonten syns inte där. */
  hook_text: boolean;
  /** fill: fyll 9:16 och skär bort kanterna. fit: hela bilden på en suddig bakgrund. */
  framings: string[];
}

/** POST /campaign/clips/{id}/versions. Bara de här valen; inga filter, ingen spegling, ingen ändrad hastighet. */
export interface VersionCreate {
  platform: "tiktok" | "youtube";
  account: string;
  /** null: samma font som originalet. */
  font: string | null;
  start_cut_s: number;
  /** null: kampanjens overlay. */
  overlay: string | null;
  sticker: VersionSticker | null;
  mirror: boolean;
  speed: number;
  filter: string;
  /** -0,3–0,3, ovanpå filtret. */
  brightness: number;
  /** -0,5–0,5, ovanpå filtret. */
  saturation: number;
}

/** GET och PUT /campaign/preferences. */
export interface Preferences {
  /** Hjälpen att skicka in i Content Rewards. */
  submit_helper: boolean;
}

export interface ClipSubscores {
  hook: number;
  engagement: number;
  value: number;
  shareability: number;
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
  /** open_id eller channel_id. null: loggat för hand utan konto. Saknas hos äldre servrar. */
  account?: string | null;
}

export type ClipAccountState = "free" | "scheduled" | "posted";

/** Ett av kampanjens konton för klippet. Okopplat konto: account är profilens text och name null. */
export interface ClipAccount {
  account: string;
  name: string | null;
  state: ClipAccountState;
  at: string | null;
  text: string | null;
}

/** GET /campaign/clips/{id}/accounts. multi: kampanjen listar minst 2 konton på plattformen. */
export interface ClipAccounts {
  multi: boolean;
  max_accounts: number;
  accounts: ClipAccount[];
}

/** POST /campaign/clips/{id}/variant: en ny text som skiljer sig från klippets andra konton. */
export interface ClipVariant {
  title: string;
  caption: string;
  description: string | null;
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
  /** Omslaget, sekunder in i det färdiga klippet. Bara i direktläge; null: TikTok väljer. */
  cover_s?: number | null;
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

export type SchedulePlatform = "youtube" | "tiktok";
export type ScheduleStatus = "waiting" | "running" | "done" | "reminded" | "failed" | "cancelled";

/** En schemalagd uppladdning. at är UTC; account är channel_id eller open_id. */
export interface ScheduledPost {
  id: number;
  clip_id: number;
  clip_title: string | null;
  profile: string;
  platform: SchedulePlatform;
  account: string | null;
  at: string;
  fields: Record<string, unknown>;
  status: ScheduleStatus;
  error: string | null;
  /** Till exempel video_id och url efter en uppladdning. */
  result: Record<string, unknown> | null;
  /** Körd efter tiden, till exempel för att Macen sov. */
  late: boolean;
  /** Väntande post för nära ett annat inlägg på samma konto. Bara en varning: posten är sparad. */
  warning: string | null;
}

/** POST /campaign/clips/{id}/schedule. at har tidszon. fields är tom för en påminnelse om TikTok. */
export interface ScheduleCreate {
  platform: SchedulePlatform;
  at: string;
  account?: string;
  fields: YouTubeSendBody | Omit<TikTokSendBody, "open_id" | "mode"> | Record<string, never>;
}

/** En del av kampanjens betyg, med en kort svensk förklaring. */
export interface ScoutPart {
  key: string;
  label: string;
  points: number;
  max: number;
  fact: string;
}

/** En kampanj från ClippingAlpha med betyg 0–100. excluded: varför den sorterades bort, annars null. */
export interface ScoutCampaign {
  ref: string;
  title: string;
  brand: string | null;
  score: number;
  parts: ScoutPart[];
  excluded: string | null;
  age_hours: number | null;
  rate_usd: number | null;
  net_rate_usd: number | null;
  budget_left_usd: number | null;
  budget_total_usd: number | null;
  min_views: number | null;
  max_payout_usd: number | null;
  drain_days: number | null;
  platforms: string[];
  mentions_twitch: boolean;
  open_join: boolean | null;
  page_url: string | null;
  join_url: string | null;
  first_seen: string;
  is_new: boolean;
}

/** GET /campaign/scout. Kampanjerna är sorterade på betyg och utan dolda. */
export interface ScoutStatus {
  enabled: boolean;
  updated_at: string | null;
  error: string | null;
  calls_today: number;
  call_limit: number;
  median_views: number;
  median_source: "dina inlägg" | "standard";
  campaigns: ScoutCampaign[];
  excluded: ScoutCampaign[];
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
  caption: {
    template: string;
    hashtags: string[];
    must_include: string[];
    /** Egen rad efter texten, till exempel "#ad #blizzardpartner". Saknas hos äldre servrar. */
    tag_line?: string | null;
    max_extra_hashtags?: number | null;
  };
  payment: { cpm_usd: number | null; min_views: number | null; max_payout_per_clip_usd: number | null };
  clipsmith_config: string | null;
}

/** GET /campaign/profiles/{id}/examples: kampanjens bästa klipp och klippare från Content Rewards. */
export interface CampaignExamples {
  /** Länken till kampanjen i Content Rewards, eller null. */
  source: string | null;
  /** Varför inget kan visas, till exempel att länken saknas. */
  problem: string | null;
  clips: {
    rank: number;
    platform: string;
    views: number;
    name: string | null;
    /** Klipparens plats bland de tre som tjänat mest, annars null. */
    earner_rank: number | null;
    post_url: string | null;
    video_url: string | null;
    thumbnail_url: string | null;
    title: string;
  }[];
  earners: { rank: number; name: string; earned_usd: number; views: number; approved: number }[];
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
  /** Orden i undertexten, tider i sekunder från råklippets start. Saknas hos äldre servrar. */
  /** style: hur undertexten bränns in. Saknas: box. */
  subtitles?: { editable: boolean; reason: string | null; words: CaptionWord[]; style?: "box" | "outline" };
}

/** Ett ord i undertexten. */
export interface CaptionWord {
  start: number;
  end: number;
  word: string;
}

/** Ungefärlig kostnad för en körning: Berget transkriberar, Claude väljer och granskar. */
export interface CostParts {
  berget_usd: number;
  claude_usd: number;
  usd: number;
}

// --- Kampanjen som nav (kontrakt-chatt.md): material, chatten och träffarna. Eget block, så att sammanslagningar blir enkla. ---

/** Var chatten letar när Robin inte säger något: senaste materialet med transkript, eller allt. */
export type SearchScope = "latest" | "all";

// Läggs ihop med CampaignProfileDetail ovan. Saknas hos äldre servrar: latest.
export interface CampaignProfileDetail {
  search_default_scope?: SearchScope;
}

/** GET /campaign/profiles/{id}/material: kampanjens källor, nyast först. */
export interface MaterialItem {
  vod_id: string;
  title: string | null;
  /** twitch när vod_id är bara siffror, annars en egen video. */
  kind: "twitch" | "local";
  created: string | null;
  duration_s: number | null;
  has_video: boolean;
  has_transcript: boolean;
  /** Antal klipp i klipploggen från källan. */
  clips: number;
}

/** GET /campaign/profiles/{id}/chats. title: början på första meddelandet. */
export interface ChatSummary {
  id: number;
  title: string;
  created: string;
  updated: string;
  hits: number;
}

/** En sökning över 0,25 dollar väntar på {confirm: true}. */
export interface ChatConfirm {
  cost_usd: number;
  sources: string[];
  minutes: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  created: string;
  sources?: string[];
  cost_usd?: number | null;
  confirm?: ChatConfirm | null;
}

export interface ChatHit {
  id: number;
  vod_id: string;
  source_title: string | null;
  start_s: number;
  end_s: number;
  title: string;
  reason: string;
  /** 1–10. */
  grade: number;
  quote: string;
  status: "ny" | "vald" | "gjord";
  clip_id: number | null;
  problem: string | null;
}

/** GET /campaign/chats/{id}, och svaret på varje POST i chatten. */
export interface CampaignChat {
  id: number;
  profile: string;
  default_scope: SearchScope;
  messages: ChatMessage[];
  hits: ChatHit[];
}

// --- Ställ in, konton och egna videor (flode-kampanjer). Eget block, så att sammanslagningar blir enkla. ---

/** twitch: kampanjen klipper streams från en Twitch-kanal. local: bara egna videor, channel är tom. */
export type CampaignSource = "twitch" | "local";

// Läggs ihop med CampaignProfile ovan. Saknas hos äldre servrar.
export interface CampaignProfile {
  source?: CampaignSource;
  /** Det kampanjen tillåter. clip_min_s och clip_max_s är det appen klipper. */
  allowed_min_s?: number;
  allowed_max_s?: number;
  /** Nycklar som saknas. De står också i problems, men visas en gång överst. */
  key_problems?: string[];
  /** Kampanjens läge i ord, utan nycklarna. Samma text som Kontroll under Inställningar. */
  state?: string;
  facecam?: { required: boolean; camera: boolean; rects_set: boolean };
  /** Kampanjen i Bevakning som den lades in från. */
  scout_ref?: string | null;
}

/** Ett konto som kampanjen postar från. name är null när kontot inte är kopplat i appen. */
export interface CampaignAccountRef {
  id: string;
  name: string | null;
}

export type AccountPlatform = "tiktok" | "youtube_shorts";

// Läggs ihop med CampaignProfileDetail ovan. Saknas hos äldre servrar.
export interface CampaignProfileDetail {
  material?: { allowed: string | null; banned: string[] };
  platforms?: Platform[];
  /** Tom lista: alla kopplade konton på plattformen får posta. */
  accounts?: Record<AccountPlatform, CampaignAccountRef[]>;
  posting?: { max_per_day: number; min_gap_minutes: number };
  engagement?: { views_up_to: number | null; min: number }[];
  /** Varför chatten inte kan fråga Claude, eller null. */
  chat_problem?: string | null;
}

/** GET /campaign/accounts: kopplade konton med namn och kampanjerna som har valt dem. */
export interface ConnectedAccount {
  id: string;
  name: string;
  connected: boolean;
  used_by: { name: string; streamer: string }[];
}
export type ConnectedAccounts = Record<AccountPlatform, ConnectedAccount[]>;

/** PUT /campaign/profiles/{id}/settings. Fält som inte skickas rörs inte. */
export interface CampaignSettingsBody {
  streamer?: string;
  campaign?: string;
  language?: string;
  links?: { content_rewards: string | null; analytics_form: string | null };
  caption_template?: string;
  hashtags?: string[];
  must_include?: string[];
  on_screen_text?: string;
  text_position?: TextPosition;
  allowed_min_s?: number;
  allowed_max_s?: number;
  clip_min_s?: number;
  clip_max_s?: number;
  cpm_usd?: number | null;
  min_views?: number | null;
  max_payout_per_clip_usd?: number | null;
  formats?: { id?: string; name: string; description: string }[];
  platforms?: Platform[];
  accounts?: Partial<Record<AccountPlatform, string[]>>;
}

// Läggs ihop med CampaignProfileCreate ovan.
export interface CampaignProfileCreate {
  source?: CampaignSource;
  /** Kampanjens id när källan är egna videor. */
  name?: string;
  content_rewards_url?: string;
  analytics_form_url?: string;
  platforms?: Platform[];
  scout_ref?: string;
}

// Läggs ihop med SetupCheck ovan: länken till stället där raden rättas.
export interface SetupCheck {
  href?: string;
}

// Läggs ihop med TikTokStatus och YouTubeStatus ovan: adressen som servern använder.
export interface TikTokStatus {
  redirect_uri?: string;
}
export interface YouTubeStatus {
  redirect_uri?: string;
}

// Läggs ihop med MaterialItem ovan: senaste projektet med källan. Saknas hos äldre servrar.
export interface MaterialItem {
  run_id?: number;
}
