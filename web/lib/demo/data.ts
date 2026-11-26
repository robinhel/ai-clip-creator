// Sample data for the demo mode (NEXT_PUBLIC_DEMO=1). Everything here is made up: the channels, the VODs, the
// clips, the numbers and the pictures. The same data every time, so screenshots and tests do not change.
import type {
  CampaignClip,
  CampaignProfile,
  CampaignProfileDetail,
  CampaignProject,
  CampaignRunDetail,
  CampaignRunInfo,
  CampaignStatus,
  ClipPost,
  KeyGroup,
  ScoutStatus,
  SetupCheck,
  StreamInfo,
} from "../types";
import type { Timeline, TimelineCandidate } from "../timeline";

const DAY = "2026-11-";
/** "Today" in the sample data: the clips and the budget are from this month. */
export const DEMO_NOW = `${DAY}24T12:00:00Z`;

/** A gradient picture as an SVG data URL, so that no picture file and no server is needed. */
export function picture(seed: number, label: string, ratio: "16:9" | "9:16" = "16:9"): string {
  const [w, h] = ratio === "16:9" ? [320, 180] : [180, 320];
  const hues = [262, 199, 330, 38, 152];
  const a = hues[seed % hues.length];
  const b = hues[(seed + 2) % hues.length];
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${a} 70% 45%)"/>` +
    `<stop offset="1" stop-color="hsl(${b} 60% 18%)"/></linearGradient></defs>` +
    `<rect width="${w}" height="${h}" fill="url(#g)"/>` +
    `<text x="50%" y="52%" text-anchor="middle" font-family="sans-serif" font-size="${ratio === "16:9" ? 18 : 15}" font-weight="700" fill="#fff" opacity=".85">${label}</text>` +
    `</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** A small seeded random generator (mulberry32): the same numbers for the same seed. */
export function random(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const FORMATS = [
  { id: "clutch", name: "Clutch", description: "A fight won against the odds." },
  { id: "funny", name: "Funny", description: "A fail, a joke or a reaction." },
  { id: "extraction", name: "Extraction", description: "Getting out with the loot at the last second." },
];

export const PROFILES: CampaignProfile[] = [
  {
    name: "nova_plays",
    streamer: "Nova",
    campaign: "Nova Plays Clipping",
    language: "en",
    channel: "nova_plays",
    formats: FORMATS,
    problems: [],
    ready: true,
    active: true,
    clip_min_s: 20,
    clip_max_s: 60,
    source: "twitch",
    allowed_min_s: 10,
    allowed_max_s: 90,
    key_problems: [],
    state: "Ready",
    facecam: { required: true, camera: true, rects_set: true },
  },
  {
    name: "lumen_quest",
    streamer: "Lumen",
    campaign: "Lumen Quest Highlights",
    language: "en",
    channel: "lumenquest_live",
    formats: FORMATS.slice(0, 2),
    problems: [],
    ready: true,
    active: true,
    clip_min_s: 15,
    clip_max_s: 45,
    source: "twitch",
    allowed_min_s: 10,
    allowed_max_s: 60,
    key_problems: [],
    state: "Ready",
    facecam: { required: false, camera: true, rects_set: true },
  },
];

export function profileDetail(p: CampaignProfile): CampaignProfileDetail {
  return {
    ...p,
    rules: ["Comments and likes must be on.", "Only clips from the streamer's own streams."],
    approval: "A person says yes to every clip before it is posted.",
    links: { content_rewards: null, analytics_form: null },
    overlay: { file: null, required: false, mode: "top", exists: false, url: "", width: null, height: null },
    on_screen_text: { text: "", position: "top", required: true, hook_style: "sans" },
    safe_zone: { top: 0.12, bottom: 0.2 },
    caption: {
      template: "{title} #{channel}",
      hashtags: ["gaming", "clips"],
      must_include: [`@${p.channel}`],
      tag_line: "#ad",
      max_extra_hashtags: 3,
    },
    payment: { cpm_usd: 1.5, min_views: 2000, max_payout_per_clip_usd: 150 },
    clipsmith_config: null,
    search_default_scope: "latest",
    material: { allowed: "Only the streamer's own Twitch streams.", banned: [] },
    platforms: ["tiktok", "youtube_shorts"],
    accounts: { tiktok: [{ id: "demo-tiktok", name: "Sample TikTok account" }], youtube_shorts: [] },
    posting: { max_per_day: 3, min_gap_minutes: 120 },
    engagement: [{ views_up_to: null, min: 1 }],
    chat_problem: "Demo mode: the chat needs the app server.",
  };
}

interface RunSeed {
  id: number;
  profile: string;
  title: string;
  game: string;
  duration_s: number;
  day: number;
  status: CampaignRunInfo["status"];
  /** An own file in the campaign's project for own files: no Twitch chat, and no card of its own. */
  project?: number;
}

const RUN_SEEDS: RunSeed[] = [
  { id: 104, profile: "nova_plays", title: "Ranked grind to Diamond, then chill", game: "Arena Tactics", duration_s: 10_380, day: 22, status: "done" },
  { id: 103, profile: "lumen_quest", title: "Blind run, first boss tonight", game: "Hollow Lanterns", duration_s: 8_160, day: 20, status: "done" },
  { id: 102, profile: "nova_plays", title: "Extraction nights with chat", game: "Deadzone Run", duration_s: 15_420, day: 17, status: "done" },
  { id: 101, profile: "nova_plays", title: "Solo vs. squads all night", game: "Deadzone Run", duration_s: 13_950, day: 14, status: "done" },
  { id: 100, profile: "lumen_quest", title: "Speedrun practice", game: "Hollow Lanterns", duration_s: 5_400, day: 11, status: "failed" },
  { id: 106, profile: "lumen_quest", title: "boss-attempts-recording.mp4", game: "Hollow Lanterns", duration_s: 2_700, day: 23, status: "done", project: 1 },
];

const STEP_LABELS: [string, string][] = [
  ["download", "Downloading the VOD"],
  ["transcribe", "Transcribing"],
  ["moments", "Finding moments"],
  ["select", "Choosing clips"],
  ["clip", "Cutting"],
  ["finish", "Making campaign-ready"],
];

const vodOf = (id: number) => `demo-${id}`;
const iso = (day: number, hour: number, minute = 0) =>
  `${DAY}${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`;

interface ClipSeed {
  run: number;
  title: string;
  start_s: number;
  length: number;
  status: CampaignStatus;
  format: string;
  score: number;
  signals: string[];
  views?: number;
}

const CLIP_SEEDS: ClipSeed[] = [
  { run: 101, title: "1v3 with a pistol", start_s: 2470, length: 42, status: "betald", format: "clutch", score: 94, signals: ["existing_clip", "chat_density", "audio_energy"], views: 18_400 },
  { run: 101, title: "Full bag, 4 HP", start_s: 5560, length: 38, status: "postad", format: "extraction", score: 88, signals: ["chat_density", "transcript_hype"], views: 9_750 },
  { run: 101, title: "Self-revenge fail", start_s: 9965, length: 31, status: "postad", format: "funny", score: 81, signals: ["clip_command", "chat_density"], views: 3_120 },
  { run: 101, title: "Jump scare in the vault", start_s: 12_492, length: 27, status: "nekad", format: "funny", score: 63, signals: ["audio_energy", "transcript_hype"] },
  { run: 101, title: "Last-second extract", start_s: 13_880, length: 45, status: "godkand", format: "extraction", score: 86, signals: ["existing_clip", "chat_density"] },
  { run: 102, title: "Chat called it", start_s: 4210, length: 35, status: "renderad", format: "funny", score: 79, signals: ["chat_density", "transcript_hype"] },
  { run: 102, title: "Sniper from across the map", start_s: 9180, length: 29, status: "renderad", format: "clutch", score: 90, signals: ["existing_clip", "audio_energy"] },
  { run: 102, title: "The door was locked", start_s: 12_640, length: 48, status: "underkand", format: "funny", score: 72, signals: ["chat_density"] },
  { run: 103, title: "First try on the boss", start_s: 6120, length: 40, status: "renderad", format: "clutch", score: 84, signals: ["chat_density", "audio_energy", "transcript_hype"] },
  { run: 103, title: "Lantern trick nobody knew", start_s: 2380, length: 33, status: "godkand", format: "funny", score: 77, signals: ["clip_command", "transcript_hype"] },
  { run: 104, title: "Diamond on the last game", start_s: 9920, length: 44, status: "renderad", format: "clutch", score: 92, signals: ["existing_clip", "chat_density", "audio_energy"] },
  { run: 106, title: "Parry chain on the last phase", start_s: 1_520, length: 34, status: "renderad", format: "clutch", score: 83, signals: ["audio_energy", "transcript_hype"] },
  { run: 106, title: "Lantern goes out mid-fight", start_s: 610, length: 26, status: "godkand", format: "funny", score: 74, signals: ["transcript_hype", "scene_change"] },
];

const SEED_RUN = Object.fromEntries(RUN_SEEDS.map((r) => [r.id, r]));

function makePost(id: number, clipId: number, day: number, views: number, paid: boolean): ClipPost {
  const likes = Math.round(views * 0.06);
  return {
    id,
    clip_id: clipId,
    platform: "tiktok",
    url: `https://example.com/demo/post/${id}`,
    status: paid ? "betald" : "postad",
    views,
    likes,
    comments: Math.round(views * 0.004),
    shares: Math.round(views * 0.002),
    paid_usd: paid ? Math.round((views / 1000) * 1.5 * 100) / 100 : null,
    created: iso(day, 18),
    updated: iso(day + 1, 18),
    engagement: Math.round(((likes + views * 0.006) / views) * 1000) / 10,
    estimate_usd: Math.round((views / 1000) * 1.5 * 100) / 100,
    account: "demo-tiktok",
    prev_status: paid ? "postad" : null,
  };
}

export function makeClips(): CampaignClip[] {
  return CLIP_SEEDS.map((c, i) => {
    const id = 501 + i;
    const run = SEED_RUN[c.run];
    const posted = c.views != null;
    const posts = posted ? [makePost(900 + i, id, run.day + 1, c.views ?? 0, c.status === "betald")] : [];
    const profile = PROFILES.find((p) => p.name === run.profile) ?? PROFILES[0];
    const hook = c.score >= 85 ? 22 : 18;
    return {
      id,
      vod_id: vodOf(c.run),
      profile: run.profile,
      start_s: c.start_s,
      end_s: c.start_s + c.length,
      title: c.title,
      format: c.format,
      signals: c.signals,
      filename: `clip_${String(i + 1).padStart(2, "0")}.mp4`,
      caption: `${c.title} @${profile.channel} #gaming #clips\n#ad`,
      status: c.status,
      problems: c.status === "underkand" ? ["The clip is 48 s, but the campaign allows at most 45 s."] : [],
      warnings: [],
      review: null,
      platform: posted ? "tiktok" : null,
      url: posts[0]?.url ?? null,
      views: c.views ?? null,
      likes: posts[0]?.likes ?? null,
      comments: posts[0]?.comments ?? null,
      shares: posts[0]?.shares ?? null,
      note: null,
      created: iso(run.day, 23, i),
      updated: iso(run.day, 23, i),
      engagement: posts[0]?.engagement ?? null,
      engagement_min: 1,
      min_views: 2000,
      video_url: "/demo/clip.mp4",
      poster_url: picture(i, c.title.split(" ").slice(0, 2).join(" "), "9:16"),
      score: c.score,
      reason: `${run.project != null ? "Loud audio and hype words" : "Chat and audio"} peak together here, and the moment ends with a clear payoff. A strong ${c.format} clip.`,
      subscores: { hook, engagement: Math.round(c.score / 4) - 1, value: 20, shareability: Math.round(c.score / 4) - 3 },
      posts,
      content_rewards_url: null,
      platforms: ["tiktok", "youtube_shorts"],
      version_of: null,
      headline: c.title,
      length_s: c.length,
      audience_countries: "Tier 1 countries",
      campaign_off: false,
    };
  });
}

export function makeRuns(clips: CampaignClip[]): CampaignRunInfo[] {
  return RUN_SEEDS.map((r) => {
    const counts: Partial<Record<CampaignStatus, number>> = {};
    for (const c of clips) if (c.vod_id === vodOf(r.id)) counts[c.status] = (counts[c.status] ?? 0) + 1;
    const profile = PROFILES.find((p) => p.name === r.profile) ?? PROFILES[0];
    return {
      run_id: r.id,
      vod_id: vodOf(r.id),
      profile: r.profile,
      game: r.game,
      focus: null,
      min_s: profile.clip_min_s,
      max_s: profile.clip_max_s,
      title: r.title,
      channel_name: profile.streamer,
      thumbnail_url: picture(r.id, r.game),
      duration_s: r.duration_s,
      created: iso(r.day, 22),
      status: r.status,
      stage: r.status === "failed" ? "transcribe" : null,
      pct: r.status === "done" ? 100 : null,
      error: r.status === "failed" ? "The transcription service did not answer. Run again continues where it stopped." : null,
      clip_counts: counts,
      project_id: r.project ?? null,
      origin: null,
      poster_url: null,
      cancelled: false,
      cancellable: false,
      vod_bytes: r.status === "done" ? 3_200_000_000 + r.id * 10_000_000 : null,
    };
  });
}

export function runDetail(run: CampaignRunInfo): CampaignRunDetail {
  const seed = SEED_RUN[run.run_id];
  const failedAt = run.status === "failed" ? 1 : STEP_LABELS.length;
  let minute = 0;
  return {
    ...run,
    message: null,
    start_s: 0,
    end_s: 0,
    steps: STEP_LABELS.map(([id, label], i) => {
      const status = i < failedAt ? "done" : i === failedAt ? "failed" : "waiting";
      const started = iso(seed.day, 22, minute);
      minute += [6, 18, 2, 4, 5, 3][i];
      return {
        id,
        label,
        status,
        started_at: status === "waiting" ? null : started,
        finished_at: status === "done" ? iso(seed.day, 22, minute) : null,
        estimate_s: null,
        message: null,
      };
    }),
    eta_s: null,
    cost_estimate_usd: 0.62,
    cost_estimate: { berget_usd: 0.14, claude_usd: 0.48, usd: 0.62 },
    retry_problem: null,
    fast: false,
  };
}

/** The signals of a run, made from its clips: peaks where the clips are, and some noise between them. */
export function makeTimeline(run: CampaignRunInfo, clips: CampaignClip[]): Timeline {
  const d = run.duration_s ?? 3600;
  const rand = random(run.run_id * 7919);
  const own = clips.filter((c) => c.vod_id === run.vod_id && c.start_s != null && c.end_s != null);
  const peaks = own.map((c) => ({ t: ((c.start_s ?? 0) + (c.end_s ?? 0)) / 2, h: 0.55 + (c.score ?? 50) / 220 }));
  for (let i = 0; i < 3; i++) peaks.push({ t: rand() * d, h: 0.25 + rand() * 0.3 });
  const at = (t: number, width: number) => peaks.reduce((s, p) => s + p.h * Math.exp(-((t - p.t) ** 2) / (2 * width ** 2)), 0);

  const N = 200;
  const rawChat = Array.from({ length: N }, (_, i) => {
    const t = ((i + 0.5) / N) * d;
    return [Math.round(t), 0.1 + at(t, d / 90) + rand() * 0.07] as [number, number];
  });
  const top = Math.max(...rawChat.map(([, v]) => v));
  // An own file has no Twitch chat.
  const chat_density = run.project_id != null ? [] : rawChat.map(([t, v]) => [t, Math.round((v / top) * 1000) / 1000] as [number, number]);

  const A = 300;
  const rawAudio = Array.from({ length: A }, (_, i) => {
    const t = ((i + 0.5) / A) * d;
    return [Math.round(t), 0.3 + at(t, d / 120) * 0.6 + rand() * 0.09] as [number, number];
  });
  const lo = Math.min(...rawAudio.map(([, v]) => v));
  const hi = Math.max(...rawAudio.map(([, v]) => v));
  const audio_energy = rawAudio.map(([t, v]) => [t, Math.round(((v - lo) / (hi - lo)) * 1000) / 1000] as [number, number]);

  const hype = own.filter((c) => c.signals.includes("transcript_hype")).map((c) => (c.start_s ?? 0) + 8);
  for (let i = 0; i < 4; i++) hype.push(Math.round(rand() * d));
  const viewer_clips = own
    .filter((c) => c.signals.includes("existing_clip") || c.signals.includes("clip_command"))
    .map((c) => (c.start_s ?? 0) + 12);

  const candidates: TimelineCandidate[] = own.map((c) => {
    const center = ((c.start_s ?? 0) + (c.end_s ?? 0)) / 2;
    return {
      start_s: Math.max(0, center - 15),
      end_s: Math.min(d, center + 15),
      score: c.score ?? 50,
      sources: c.signals,
      breakdown: Object.fromEntries(c.signals.map((s, i) => [s, s === "chat_density" ? 3 : i + 1])),
    };
  });
  const filler = run.project_id != null ? "audio_energy" : "chat_density";
  for (let i = 0; i < 4; i++) {
    const center = Math.round(rand() * d);
    candidates.push({
      start_s: Math.max(0, center - 15),
      end_s: Math.min(d, center + 15),
      score: Math.round(10 + rand() * 40),
      sources: [filler],
      breakdown: { [filler]: 1 },
    });
  }

  return {
    duration_s: d,
    chat_density,
    audio_energy,
    hype: hype.sort((a, b) => a - b),
    viewer_clips: viewer_clips.sort((a, b) => a - b),
    candidates,
    picks: own.map((c) => ({ start_s: c.start_s ?? 0, end_s: c.end_s ?? 0, title: c.title })),
  };
}

/** The campaign's project for own files: one card with its files and clips, as GET /campaign/projects gives it. */
export function makeProjects(runs: CampaignRunInfo[], clips: CampaignClip[]): CampaignProject[] {
  const ids = [...new Set(runs.flatMap((r) => (r.project_id != null ? [r.project_id] : [])))];
  return ids.map((id) => {
    const files = runs.filter((r) => r.project_id === id);
    const vods = new Set(files.map((r) => r.vod_id));
    const own = clips.filter((c) => vods.has(c.vod_id));
    const counts: Partial<Record<CampaignStatus, number>> = {};
    for (const c of own) counts[c.status] = (counts[c.status] ?? 0) + 1;
    const profile = PROFILES.find((p) => p.name === files[0].profile) ?? PROFILES[0];
    return {
      id,
      profile: profile.name,
      streamer: profile.streamer,
      title: "",
      created: files.at(-1)?.created ?? null,
      updated: own.at(-1)?.updated ?? files[0].created,
      active: false,
      pct: null,
      poster_url: own[0]?.poster_url ?? null,
      clip_counts: counts,
      files: files.map((r) => ({
        run_id: r.run_id,
        vod_id: r.vod_id,
        title: r.title,
        status: r.status,
        stage: r.stage,
        pct: r.pct,
        error: r.error,
        duration_s: r.duration_s,
        vod_bytes: r.vod_bytes ?? null,
        created: r.created,
      })),
    };
  });
}

export const STREAMS = (runs: CampaignRunInfo[]): StreamInfo[] => [
  {
    vod_id: "demo-105",
    channel: "nova_plays",
    channel_name: "Nova",
    title: "Late night squads, viewer games",
    game: "Deadzone Run",
    duration_s: 12_600,
    created_at: iso(23, 20),
    thumbnail_url: picture(105, "Deadzone Run"),
    campaigns: ["nova_plays"],
    run_id: null,
    run_status: null,
    clip_count: 0,
    view_count: 4_200,
    viewer_clips: 9,
    live: false,
    score: 86,
    score_parts: { clips: 40, views: 26, game: 20 },
    reason: "9 viewer clips and a game the campaign likes.",
    recommended: true,
  },
  ...runs
    .filter((r) => r.status === "done" && r.project_id == null)
    .slice(0, 3)
    .map(
      (r): StreamInfo => ({
        vod_id: r.vod_id,
        channel: PROFILES.find((p) => p.name === r.profile)?.channel ?? "",
        channel_name: r.channel_name ?? "",
        title: r.title ?? "",
        game: r.game,
        duration_s: r.duration_s ?? 0,
        created_at: r.created,
        thumbnail_url: r.thumbnail_url,
        campaigns: [r.profile],
        run_id: r.run_id,
        run_status: r.status,
        clip_count: Object.values(r.clip_counts).reduce((a, b) => a + (b ?? 0), 0),
        view_count: 3_000,
        viewer_clips: 4,
        live: false,
        score: 60,
        score_parts: { clips: 24, views: 18, game: 18 },
        reason: "Clipped already.",
        recommended: false,
      })
    ),
];

export const SCOUT: ScoutStatus = {
  enabled: true,
  updated_at: iso(24, 8),
  error: null,
  calls_today: 2,
  call_limit: 20,
  median_views: 4_000,
  median_source: "standard",
  campaigns: [],
  excluded: [],
};

export const KEY_GROUPS: KeyGroup[] = [
  { id: "anthropic", label: "Claude (Anthropic)", keys: [{ name: "ANTHROPIC_API_KEY", label: "API key", set: true }] },
  { id: "berget", label: "Berget (transcription)", keys: [{ name: "BERGET_API_KEY", label: "API key", set: true }] },
  {
    id: "twitch",
    label: "Twitch",
    keys: [
      { name: "TWITCH_CLIENT_ID", label: "Client ID", set: true },
      { name: "TWITCH_CLIENT_SECRET", label: "Client secret", set: true },
    ],
  },
  {
    id: "tiktok",
    label: "TikTok",
    keys: [
      { name: "TIKTOK_CLIENT_KEY", label: "Client key", set: true },
      { name: "TIKTOK_CLIENT_SECRET", label: "Client secret", set: true },
    ],
  },
  {
    id: "youtube",
    label: "YouTube",
    keys: [
      { name: "YOUTUBE_CLIENT_ID", label: "Client ID", set: false },
      { name: "YOUTUBE_CLIENT_SECRET", label: "Client secret", set: false },
    ],
  },
];

export const SETUP: SetupCheck[] = [
  { id: "ffmpeg", label: "ffmpeg", ok: true, detail: "Sample: installed." },
  { id: "keys", label: "Keys", ok: true, detail: "Sample: Claude, Berget and Twitch keys are saved." },
  { id: "profile", label: "Campaigns", ok: true, detail: "Sample: 2 campaigns can be read." },
  { id: "disk", label: "Disk", ok: true, detail: "Sample: 312 GB free." },
];
