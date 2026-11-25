// The demo mode: with NEXT_PUBLIC_DEMO=1 the web reads sample data from here instead of the API.
// demoFetch answers like the server, from memory only. It never calls fetch, so a demo can never reach an API.
import type { CampaignClip, CampaignProfile, CampaignProject, CampaignRunInfo, CampaignStatus, CampaignSummary, ClipPost, TranscriptionSetting } from "../types";
import {
  DEMO_NOW,
  KEY_GROUPS,
  PROFILES,
  SCOUT,
  SETUP,
  STREAMS,
  makeClips,
  makeProjects,
  makeRuns,
  makeTimeline,
  picture,
  profileDetail,
  runDetail,
} from "./data";

/** Inlined at build time (see lib/api.ts). */
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";

/** The text for everything that needs the app server: keys, accounts, uploads and new projects. */
export const DEMO_OFF = "Demo mode: this needs the app server, so it is turned off in the demo.";

export { DEMO_NOW, picture };

class DemoError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);
  }
}

interface Store {
  clips: CampaignClip[];
  profiles: CampaignProfile[];
  submitHelper: boolean;
  /** Settings > Transcription. Only in memory: nothing is downloaded in the demo. */
  transcription: TranscriptionSetting;
}

const TRANSCRIPTION: TranscriptionSetting = {
  engine: "berget",
  model: "small",
  berget_key: true,
  models: [
    { id: "small", size_gb: 0.5, downloaded: false },
    { id: "medium", size_gb: 1.5, downloaded: false },
    { id: "large-v3", size_gb: 3, downloaded: false },
  ],
};

let store: Store | null = null;

function state(): Store {
  store ??= { clips: makeClips(), profiles: PROFILES.map((p) => ({ ...p })), submitHelper: true, transcription: TRANSCRIPTION };
  return store;
}

/** Back to the sample data as it was. For tests. */
export function resetDemo() {
  store = null;
}

const runs = () => makeRuns(state().clips);

function summary(): CampaignSummary {
  const counts: Partial<Record<CampaignStatus, number>> = {};
  for (const c of state().clips) counts[c.status] = (counts[c.status] ?? 0) + 1;
  const posts = state().clips.flatMap((c) => c.posts ?? []);
  const estimated = posts.reduce((s, p) => s + (p.estimate_usd ?? 0), 0);
  const paid = posts.reduce((s, p) => s + (p.paid_usd ?? 0), 0);
  return {
    budget: {
      month_usd: 4.62,
      month_cap_usd: 200,
      today_usd: 0.31,
      day_cap_usd: 10,
      by_purpose: {
        transkribering: { calls: 41, usd: 1.12 },
        valja: { calls: 28, usd: 2.04 },
        granska: { calls: 11, usd: 1.21 },
        kalibrera: { calls: 4, usd: 0.25 },
      },
    },
    counts,
    todo: [],
    earnings: {
      estimated_usd: Math.round(estimated * 100) / 100,
      paid_usd: Math.round(paid * 100) / 100,
      by_month: [
        { month: "2026-10", usd: 6.4 },
        { month: "2026-11", usd: Math.round(estimated * 100) / 100 },
      ],
      per_clip: state()
        .clips.filter((c) => c.posts?.length)
        .map((c) => ({ id: c.id, views: c.views, usd: c.posts?.[0]?.estimate_usd ?? 0 })),
    },
    disk: { free_gb: 312, total_gb: 500, connected: true, problem: null },
  };
}

const projects = () => makeProjects(runs(), state().clips);

function findProject(id: number): CampaignProject {
  const project = projects().find((p) => p.id === id);
  if (!project) throw new DemoError("The project does not exist.", 404);
  return project;
}

function clipList(query: URLSearchParams): CampaignClip[] {
  const runId = query.get("run_id");
  const vod = runId ? `demo-${runId}` : query.get("vod_id");
  const projectId = query.get("project_id");
  const projectVods = projectId ? new Set(findProject(Number(projectId)).files.map((f) => f.vod_id)) : null;
  return state().clips.filter(
    (c) =>
      (!query.get("status") || c.status === query.get("status")) &&
      (!vod || c.vod_id === vod) &&
      (!projectVods || projectVods.has(c.vod_id)) &&
      (!query.get("profile") || c.profile === query.get("profile"))
  );
}

function findClip(id: number): CampaignClip {
  const clip = state().clips.find((c) => c.id === id);
  if (!clip) throw new DemoError("The clip does not exist.", 404);
  return clip;
}

function replaceClip(next: CampaignClip): CampaignClip {
  state().clips = state().clips.map((c) => (c.id === next.id ? next : c));
  return next;
}

/** The clip's status, link and numbers follow its first post, as on the server. */
function withPosts(clip: CampaignClip, posts: ClipPost[]): CampaignClip {
  const first = posts[0];
  return {
    ...clip,
    posts,
    status: first ? first.status : clip.status === "godkand" || clip.status === "postad" ? "godkand" : clip.status,
    url: first?.url ?? null,
    platform: first?.platform ?? null,
    views: first?.views ?? null,
    likes: first?.likes ?? null,
    updated: new Date().toISOString(),
  };
}

function findRun(id: number): CampaignRunInfo {
  const run = runs().find((r) => r.run_id === id);
  if (!run) throw new DemoError("The project does not exist.", 404);
  return run;
}

function findProfile(name: string): CampaignProfile {
  const p = state().profiles.find((q) => q.name === name);
  if (!p) throw new DemoError(`The campaign ${name} cannot be read.`, 400);
  return p;
}

type Handler = (match: string[], query: URLSearchParams, body: unknown) => unknown;

const GET: [RegExp, Handler][] = [
  [/^\/campaign\/summary$/, () => summary()],
  [/^\/campaign\/profiles$/, () => state().profiles],
  [/^\/campaign\/profiles\/([^/]+)$/, ([name]) => profileDetail(findProfile(name))],
  [/^\/campaign\/profiles\/([^/]+)\/yaml$/, ([name]) => ({ yaml: `# Sample campaign (demo mode)\nname: ${name}\nchannel: ${findProfile(name).channel}\n` })],
  [/^\/campaign\/profiles\/([^/]+)\/examples$/, () => ({ source: null, problem: "Demo mode: no example clips in the sample data.", clips: [], earners: [] })],
  [/^\/campaign\/profiles\/([^/]+)\/material$/, ([name]) =>
    runs()
      .filter((r) => r.profile === name)
      .map((r) => ({ vod_id: r.vod_id, title: r.title, kind: r.project_id != null ? "local" : "twitch", created: r.created, duration_s: r.duration_s, has_video: false, has_transcript: r.status === "done", clips: Object.values(r.clip_counts).reduce((a, b) => a + (b ?? 0), 0), run_id: r.run_id }))],
  [/^\/campaign\/profiles\/([^/]+)\/chats$/, () => []],
  [/^\/campaign\/profiles\/([^/]+)\/reframe$/, () => ({ mode: "split", split_ratio: 0.36, webcam_rect: [1440, 0, 480, 270], webcam_rects: [[1440, 0, 480, 270]], gameplay_rect: [240, 0, 1440, 1080], source_width: 1920, source_height: 1080, webcam_aspect: 16 / 9, gameplay_aspect: 4 / 3 })],
  [/^\/campaign\/runs$/, () => runs()],
  [/^\/campaign\/runs\/(\d+)$/, ([id]) => runDetail(findRun(Number(id)))],
  [/^\/campaign\/runs\/(\d+)\/timeline$/, ([id]) => makeTimeline(findRun(Number(id)), state().clips)],
  [/^\/runs\/(\d+)$/, ([id]) => {
    const r = findRun(Number(id));
    return { id: r.run_id, vod_id: r.vod_id, channel: r.profile, status: r.status, stage: r.stage, error: r.error, prompt_version: "v2", created_at: r.created, updated_at: r.created, clip_count: 0 };
  }],
  [/^\/campaign\/projects$/, () => projects()],
  [/^\/campaign\/projects\/(\d+)$/, ([id]) => findProject(Number(id))],
  [/^\/campaign\/streams$/, () => STREAMS(runs())],
  [/^\/campaign\/vod\/([^/]+)$/, () => {
    throw new DemoError("Demo mode: looking up a Twitch VOD needs the app server. Open a sample project below instead.", 403);
  }],
  [/^\/campaign\/clips$/, (_, query) => clipList(query)],
  [/^\/campaign\/clips\/(\d+)\/transcript$/, ([id]) => ({ text: findClip(Number(id)).title, segments: [] })],
  [/^\/campaign\/clips\/(\d+)\/(tiktok|youtube)$/, () => null],
  [/^\/campaign\/clips\/(\d+)\/accounts$/, () => ({ multi: false, max_accounts: 1, accounts: [{ account: "demo-tiktok", name: "Sample TikTok account", state: "free", at: null, text: null }] })],
  [/^\/campaign\/clips\/(\d+)\/edit$/, ([id]) => ({ clip_id: Number(id), title: findClip(Number(id)).title, original: null, trim: null, min_s: 20, max_s: 60, editable: false, reason: DEMO_OFF })],
  [/^\/campaign\/schedule$/, () => []],
  [/^\/campaign\/preferences$/, () => ({ submit_helper: state().submitHelper })],
  [/^\/campaign\/scout$/, () => SCOUT],
  [/^\/campaign\/scout\/country$/, () => ({ country: "SE", choices: [{ code: "SE", name: "Sweden" }, { code: "US", name: "United States" }, { code: "GB", name: "United Kingdom" }] })],
  [/^\/campaign\/setup$/, () => ({ checks: SETUP })],
  [/^\/campaign\/keys$/, () => ({ groups: KEY_GROUPS })],
  [/^\/campaign\/transcription$/, () => state().transcription],
  [/^\/campaign\/accounts$/, () => ({
    tiktok: [{ id: "demo-tiktok", name: "Sample TikTok account", connected: true, used_by: [{ name: "nova_plays", streamer: "Nova" }] }],
    youtube_shorts: [],
  })],
  [/^\/campaign\/tiktok\/status$/, () => ({
    configured: false, connected: false, display_name: null, avatar_url: null, scopes: [], refresh_expires_at: null, pending_24h: 0, pending_limit: 5, accounts: [],
  })],
  [/^\/campaign\/youtube\/status$/, () => ({ configured: false, accounts: [], uploads_today: 0 })],
  [/^\/campaign\/live$/, () => []],
  [/^\/campaign\/uploads\/options$/, () => ({ fonts: [], stickers: [], overlays: [], overlay_locked: false, max_start_cut_s: 0, min_seconds: 10, hook_text: true, framings: ["fill"], music: [] })],
];

let nextPost = 2000;

const WRITE: [string, RegExp, Handler][] = [
  ["PATCH", /^\/campaign\/clips\/(\d+)$/, ([id], _, body) => {
    const patch = (body ?? {}) as Partial<CampaignClip>;
    return replaceClip({ ...findClip(Number(id)), ...patch, updated: new Date().toISOString() });
  }],
  ["POST", /^\/campaign\/clips\/(\d+)\/posts$/, ([id], _, body) => {
    const clip = findClip(Number(id));
    const { platform, url } = body as { platform: ClipPost["platform"]; url: string };
    const now = new Date().toISOString();
    const post: ClipPost = {
      id: nextPost++, clip_id: clip.id, platform, url, status: "postad", views: null, likes: null, comments: null, shares: null,
      paid_usd: null, created: now, updated: now, engagement: null, estimate_usd: null, account: null, prev_status: null,
    };
    return replaceClip(withPosts(clip, [...(clip.posts ?? []), post]));
  }],
  ["PATCH", /^\/campaign\/posts\/(\d+)$/, ([id], _, body) => {
    const clip = state().clips.find((c) => c.posts?.some((p) => p.id === Number(id)));
    if (!clip) throw new DemoError("The post does not exist.", 404);
    const posts = (clip.posts ?? []).map((p) => (p.id === Number(id) ? { ...p, ...(body as Partial<ClipPost>), updated: new Date().toISOString() } : p));
    return replaceClip(withPosts(clip, posts));
  }],
  ["DELETE", /^\/campaign\/posts\/(\d+)$/, ([id]) => {
    const clip = state().clips.find((c) => c.posts?.some((p) => p.id === Number(id)));
    if (!clip) throw new DemoError("The post does not exist.", 404);
    return replaceClip(withPosts(clip, (clip.posts ?? []).filter((p) => p.id !== Number(id))));
  }],
  ["PUT", /^\/campaign\/preferences$/, (_, __, body) => {
    state().submitHelper = !!(body as { submit_helper?: boolean })?.submit_helper;
    return { submit_helper: state().submitHelper };
  }],
  ["PUT", /^\/campaign\/transcription$/, (_, __, body) => {
    // Samma kontroll som servern: ett objekt med engine och/eller model, inget annat.
    const change = (body && typeof body === "object" && !Array.isArray(body) ? body : {}) as { engine?: unknown; model?: unknown };
    const { engine, model } = change;
    if ((engine !== undefined && engine !== "berget" && engine !== "local") ||
        (model !== undefined && !TRANSCRIPTION.models.some((m) => m.id === model)) || (engine === undefined && model === undefined) ||
        Object.keys(change).some((key) => key !== "engine" && key !== "model")) {
      throw new DemoError("Choose berget or local, and small, medium or large-v3.", 400);
    }
    state().transcription = { ...state().transcription, ...(change as Partial<TranscriptionSetting>) };
    return state().transcription;
  }],
  ["PUT", /^\/campaign\/profiles\/([^/]+)\/active$/, ([name], _, body) => {
    const active = !!(body as { active?: boolean })?.active;
    state().profiles = state().profiles.map((p) => (p.name === name ? { ...p, active } : p));
    return profileDetail(findProfile(name));
  }],
  ["POST", /^\/campaign\/scout\/[^?]+\/(dismiss|undismiss)$/, () => ({})],
];

function parseBody(body: RequestInit["body"]): unknown {
  if (typeof body !== "string") return undefined;
  try {
    return JSON.parse(body);
  } catch {
    return undefined;
  }
}

/** Answers a call to the API (the path after /api) from the sample data. A write changes only the memory. */
export async function demoFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = new URL(path, "http://demo.invalid");
  const method = (init.method ?? "GET").toUpperCase();
  const body = parseBody(init.body);
  // A copy, so that a page that changes what it got does not change the sample data.
  const answer = (value: unknown) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value))) as T;

  if (method === "GET" || method === "HEAD") {
    for (const [pattern, handler] of GET) {
      const match = url.pathname.match(pattern);
      if (match) return answer(handler(match.slice(1), url.searchParams, body));
    }
    throw new DemoError("Demo mode: there is no sample data for this part.", 404);
  }
  for (const [m, pattern, handler] of WRITE) {
    const match = url.pathname.match(pattern);
    if (m === method && match) return answer(handler(match.slice(1), url.searchParams, body));
  }
  throw new DemoError(DEMO_OFF, 403);
}
