import type {
  CampaignClip,
  CampaignClipPatch,
  CampaignExamples,
  CampaignProfile,
  CampaignProfileCreate,
  CampaignProfileDetail,
  CampaignRunCreate,
  CampaignRunDetail,
  CampaignProject,
  CampaignRunInfo,
  CampaignStatus,
  CaptionWord,
  ClipAccounts,
  ClipVariant,
  VersionCreate,
  UploadOptions,
  VersionOptions,
  CampaignSummary,
  KeyGroup,
  SetupCheck,
  KeyTest,
  ClipEditInfo,
  ClipPostPatch,
  ClipTranscript,
  Platform,
  Preferences,
  Rect,
  ReframeInfo,
  Run,
  ScheduleCreate,
  ScheduledPost,
  ScoutStatus,
  StreamInfo,
  TikTokCreatorInfo,
  TikTokSendBody,
  TikTokStatus,
  TikTokUpload,
  VodInfo,
  YouTubeSendBody,
  YouTubeStatus,
  YouTubeUpload,
} from "./types";
// Kampanjen som nav (kontrakt-chatt.md).
import type { CampaignChat, ChatSummary, MaterialItem } from "./types";
// Ställ in och konton (flode-kampanjer).
import type { CampaignSettingsBody, ConnectedAccounts } from "./types";
// Ta bort VOD-filen på ett projekt (projekt-ta-bort).
import type { FreedSpace } from "./types";
import type { Timeline } from "./timeline";
// Demoläget (NEXT_PUBLIC_DEMO=1): påhittad data ur lib/demo i stället för API:t. Växlas bara här och i de andra fetch-hjälparna.
import { DEMO, DEMO_OFF, demoFetch, picture } from "./demo";

// JSON API calls go through the Next.js rewrite proxy (/api/* → FastAPI).
// This avoids CORS and removes the need for NEXT_PUBLIC_API_BASE in the browser.
// In the demo mode the server's paths (video_url, overlay.url and so on) point at files in public/, served from the root.
const PROXY = DEMO ? "" : "/api";

// Inloggningen på TikTok öppnas direkt mot servern, inte via proxyn: TikTok leder tillbaka till :8000.
const DIRECT = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

const FIELD_LABEL: Record<string, string> = {
  views: "views",
  likes: "likes",
  comments: "comments",
  shares: "shares",
  url: "the link",
  platform: "platform",
  caption: "the caption",
  title: "the title",
};

// shortcut: läst en gång per sidladdning och delad av alla klipprader; en ändring i en annan flik syns först efter omladdning.
let preferences: Promise<Preferences> | null = null;

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (DEMO) return demoFetch<T>(path, init); // proxyn och servern används aldrig i demoläget
  const res = await fetch(`${PROXY}${path}`, {
    ...init,
    headers: {
      // FormData: webbläsaren sätter typen med gränsen mellan delarna själv.
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    // Servern skriver felet för användaren i detail (svensk text i kampanjlagret).
    let detail: unknown;
    try {
      detail = JSON.parse(text).detail;
    } catch {
      detail = undefined;
    }
    if (res.status === 503 && detail === "Backend unavailable") {
      throw new Error("The app server is not running. Start it and press Try again.");
    }
    if (typeof detail === "string") throw Object.assign(new Error(detail), { status: res.status });
    // FastAPI:s valideringsfel (422) har detail som en lista med fält och meddelande.
    if (Array.isArray(detail)) {
      const fields = detail.map((d) => d?.loc?.at?.(-1)).filter(Boolean).map((f) => FIELD_LABEL[f] ?? f);
      throw new Error(
        `The server did not accept the value${fields.length ? ` in ${fields.join(", ")}` : ""}. Check the fields and try again.`
      );
    }
    throw new Error(`The server answered with error ${res.status}. Try again.`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  runs: {
    // clipsmiths körning; projektsidan läser updated_at för att se när klippen gjorts klara igen.
    get: (id: number) => apiFetch<Run>(`/runs/${id}`),
  },
  campaign: {
    profiles: () => apiFetch<CampaignProfile[]>("/campaign/profiles"),
    profile: {
      get: (id: string) => apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}`),
      create: (body: CampaignProfileCreate) =>
        apiFetch<CampaignProfileDetail>("/campaign/profiles", { method: "POST", body: JSON.stringify(body) }),
      // Själva PNG-filen som body, inget multipart.
      uploadOverlay: (id: string, file: Blob) =>
        apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/overlay`, {
          method: "PUT",
          body: file,
          headers: { "Content-Type": "image/png" },
        }),
      setActive: (id: string, active: boolean) =>
        apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/active`, {
          method: "PUT",
          body: JSON.stringify({ active }),
        }),
      yaml: (id: string) => apiFetch<{ yaml: string }>(`/campaign/profiles/${id}/yaml`),
      saveYaml: (id: string, yaml: string) =>
        apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/yaml`, {
          method: "PUT",
          body: JSON.stringify({ yaml }),
        }),
      examples: (id: string) => apiFetch<CampaignExamples>(`/campaign/profiles/${id}/examples`),
      reframe: (id: string) => apiFetch<ReframeInfo>(`/campaign/profiles/${id}/reframe`),
      saveReframe: (id: string, body: { webcam_rects: Rect[]; gameplay_rect: Rect; split_ratio: number }) =>
        apiFetch<ReframeInfo>(`/campaign/profiles/${id}/reframe`, { method: "PUT", body: JSON.stringify(body) }),
      frameUrl: (id: string, vodId?: string) =>
        DEMO ? picture(0, "Sample frame") : `${PROXY}/campaign/profiles/${id}/frame${vodId ? `?vod_id=${vodId}` : ""}`,
    },
    vod: (vodId: string) => apiFetch<VodInfo>(`/campaign/vod/${vodId}`),
    streams: () => apiFetch<StreamInfo[]>("/campaign/streams"),
    // Kampanjens projekt för egna filer och uppladdningar, senaste först.
    projects: {
      list: () => apiFetch<CampaignProject[]>("/campaign/projects"),
      get: (id: number) => apiFetch<CampaignProject>(`/campaign/projects/${id}`),
    },
    runs: {
      list: () => apiFetch<CampaignRunInfo[]>("/campaign/runs"),
      get: (runId: number) => apiFetch<CampaignRunDetail>(`/campaign/runs/${runId}`),
      create: (body: CampaignRunCreate) =>
        apiFetch<{ id: number }>("/campaign/runs", { method: "POST", body: JSON.stringify(body) }),
      /** Ny körning med samma val. 201 med det nya id:t, 400 om körningen inte misslyckades, 429 om en körning pågår. */
      retry: (runId: number) =>
        apiFetch<{ id: number }>(`/campaign/runs/${runId}/retry`, { method: "POST" }),
      finish: (runId: number) =>
        apiFetch<{ started: boolean }>(`/campaign/runs/${runId}/finish`, { method: "POST" }),
      /** Signalerna i VOD:en och AI:ns val. 404 med text när kandidaterna inte är sparade än. */
      timeline: (runId: number) => apiFetch<Timeline>(`/campaign/runs/${runId}/timeline`),
    },
    // Inlägg per plattform. Svaret är hela klippet med alla dess inlägg.
    posts: {
      add: (clipId: number, body: { platform: Platform; url: string }) =>
        apiFetch<CampaignClip>(`/campaign/clips/${clipId}/posts`, { method: "POST", body: JSON.stringify(body) }),
      patch: (postId: number, body: ClipPostPatch) =>
        apiFetch<CampaignClip>(`/campaign/posts/${postId}`, { method: "PATCH", body: JSON.stringify(body) }),
      remove: (postId: number) => apiFetch<CampaignClip>(`/campaign/posts/${postId}`, { method: "DELETE" }),
    },
    /** En egen video görs om och blir ett klipp att granska. 201 med klippet, 400 med svensk text. */
    upload: (form: FormData) => apiFetch<CampaignClip>("/campaign/uploads", { method: "POST", body: form }),
    /** Font, overlay, klistermärke och bildläge för egna videor i kampanjen. */
    uploadOptions: (profile: string) =>
      apiFetch<UploadOptions>(`/campaign/uploads/options?profile=${encodeURIComponent(profile)}`),
    clips: {
      /** posted_off: också postade klipp från avstängda kampanjer, för Statistik. */
      list: (
        filter: { status?: CampaignStatus; vod_id?: string; run_id?: number; project_id?: number; posted_off?: boolean } = {}
      ) => {
        const query = new URLSearchParams(
          Object.entries(filter).map(([key, value]) => [key, String(value)])
        ).toString();
        return apiFetch<CampaignClip[]>(`/campaign/clips${query ? `?${query}` : ""}`);
      },
      patch: (id: number, body: CampaignClipPatch) =>
        apiFetch<CampaignClip>(`/campaign/clips/${id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),
      transcript: (id: number) => apiFetch<ClipTranscript>(`/campaign/clips/${id}/transcript`),
      tiktok: (id: number) => apiFetch<TikTokUpload | null>(`/campaign/clips/${id}/tiktok`),
      youtube: (id: number) => apiFetch<YouTubeUpload | null>(`/campaign/clips/${id}/youtube`),
      sendToYouTube: (id: number, body: YouTubeSendBody) =>
        apiFetch<YouTubeUpload>(`/campaign/clips/${id}/youtube`, { method: "POST", body: JSON.stringify(body) }),
      /** 201 med posten, 400 med svensk text, 409 om plattformen redan har en väntande post. */
      schedule: (id: number, body: ScheduleCreate) =>
        apiFetch<ScheduledPost>(`/campaign/clips/${id}/schedule`, { method: "POST", body: JSON.stringify(body) }),
      accounts: (id: number, platform: "tiktok" | "youtube") =>
        apiFetch<ClipAccounts>(`/campaign/clips/${id}/accounts?platform=${platform}`),
      // Claude skriver texten; budgeten gäller. 400 med svensk text vid saknad nyckel eller nådd budget.
      variant: (id: number, body: { platform: "tiktok" | "youtube"; account: string }) =>
        apiFetch<ClipVariant>(`/campaign/clips/${id}/variant`, { method: "POST", body: JSON.stringify(body) }),
      /** Servern kräver kontot: valen och förvalen gäller kontot. */
      versionOptions: (id: number, platform: "tiktok" | "youtube", account: string) =>
        apiFetch<VersionOptions>(`/campaign/clips/${id}/versions/options?platform=${platform}&account=${encodeURIComponent(account)}`),
      /** 201 med den nya versionen som ett eget klipp. Servern renderar först, så det kan ta en minut. */
      createVersion: (id: number, body: VersionCreate) =>
        apiFetch<CampaignClip>(`/campaign/clips/${id}/versions`, { method: "POST", body: JSON.stringify(body) }),
      /** Claude föreslår en ny hook för versionen (kostar lite budget). Fel 400/502 med svensk text. */
      suggestVersionHook: (id: number) =>
        apiFetch<{ hook: string }>(`/campaign/clips/${id}/versions/hook`, { method: "POST" }),
      edit: (id: number) => apiFetch<ClipEditInfo>(`/campaign/clips/${id}/edit`),
      /** headline utelämnad: oförändrad. "": ingen egen text, titeln står i bilden. */
      saveEdit: (id: number, body: { in_s?: number; out_s?: number; title?: string; headline?: string; words?: CaptionWord[] }) =>
        apiFetch<CampaignClip>(`/campaign/clips/${id}/edit`, { method: "POST", body: JSON.stringify(body) }),
      // Utan open_id väljer servern det enda kopplade kontot, och utan mode blir det ett utkast.
      sendToTikTok: (id: number, body?: TikTokSendBody) =>
        apiFetch<TikTokUpload>(`/campaign/clips/${id}/tiktok`, {
          method: "POST",
          body: body ? JSON.stringify(body) : undefined,
        }),
    },
    youtube: {
      status: () => apiFetch<YouTubeStatus>("/campaign/youtube/status"),
      disconnect: (channelId: string) =>
        apiFetch<unknown>(`/campaign/youtube/accounts/${encodeURIComponent(channelId)}/disconnect`, { method: "POST" }),
      // Direkt mot servern som TikTok: Google leder tillbaka till :8000.
      connectUrl: DEMO ? "#" : `${DIRECT}/campaign/youtube/connect`,
    },
    tiktok: {
      status: () => apiFetch<TikTokStatus>("/campaign/tiktok/status"),
      disconnect: () => apiFetch<{ connected: false }>("/campaign/tiktok/disconnect", { method: "POST" }),
      creatorInfo: (openId: string) =>
        apiFetch<TikTokCreatorInfo>(`/campaign/tiktok/accounts/${encodeURIComponent(openId)}/creator_info`),
      disconnectAccount: (openId: string) =>
        apiFetch<unknown>(`/campaign/tiktok/accounts/${encodeURIComponent(openId)}/disconnect`, { method: "POST" }),
      connectUrl: DEMO ? "#" : `${DIRECT}/campaign/tiktok/connect`,
    },
    schedule: {
      list: () => apiFetch<ScheduledPost[]>("/campaign/schedule"),
      // Bara väntande poster kan avbrytas, annars 409.
      cancel: (id: number) => apiFetch<unknown>(`/campaign/schedule/${id}`, { method: "DELETE" }),
    },
    preferences: {
      get: () =>
        (preferences ??= apiFetch<Preferences>("/campaign/preferences").catch((err) => {
          preferences = null; // försök igen nästa gång
          throw err;
        })),
      save: (body: Preferences) =>
        apiFetch<Preferences>("/campaign/preferences", { method: "PUT", body: JSON.stringify(body) }).then((saved) => {
          preferences = Promise.resolve(saved);
          return saved;
        }),
    },
    summary: () => apiFetch<CampaignSummary>("/campaign/summary"),
    scout: {
      status: () => apiFetch<ScoutStatus>("/campaign/scout"),
      refresh: () => apiFetch<unknown>("/campaign/scout/refresh", { method: "POST" }),
      // ref är "märke/serie"; snedstrecket ska stå kvar i sökvägen.
      setDismissed: (ref: string, dismissed: boolean) =>
        apiFetch<unknown>(`/campaign/scout/${ref.split("/").map(encodeURIComponent).join("/")}/${dismissed ? "dismiss" : "undismiss"}`, {
          method: "POST",
        }),
    },
    setup: () => apiFetch<{ checks: SetupCheck[] }>("/campaign/setup"),
    keys: {
      list: () => apiFetch<{ groups: KeyGroup[] }>("/campaign/keys"),
      // Värdena skickas bara i den här kroppen och sparas inte i webbläsaren.
      save: (values: Record<string, string>) =>
        apiFetch<{ groups: KeyGroup[]; test: KeyTest | null }>("/campaign/keys", { method: "PUT", body: JSON.stringify({ values }) }),
      testTwitch: () => apiFetch<KeyTest>("/campaign/keys/test/twitch", { method: "POST" }),
    },
    /** En sökväg från servern (till exempel overlay.url) via proxyn. */
    proxied: (path: string) => `${PROXY}${path}`,
    videoUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}`,
    posterUrl: (clip: CampaignClip) => (clip.poster_url ? `${PROXY}${clip.poster_url}` : undefined),
    // video_url kan redan ha en fråga (?v=… efter en redigering).
    downloadUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}${clip.video_url.includes("?") ? "&" : "?"}download=1`,
    // --- Kampanjen som nav (kontrakt-chatt.md): material, kampanjens klipp och chatten. ---
    material: (id: string) => apiFetch<MaterialItem[]>(`/campaign/profiles/${id}/material`),
    /** Källans video med Range-stöd, för att spela en träff. */
    materialVideoUrl: (vodId: string) => DEMO ? "/demo/clip.mp4" : `${PROXY}/campaign/material/${encodeURIComponent(vodId)}/video`,
    profileClips: (id: string) => apiFetch<CampaignClip[]>(`/campaign/clips?profile=${encodeURIComponent(id)}`),
    chats: {
      list: (id: string) => apiFetch<ChatSummary[]>(`/campaign/profiles/${id}/chats`),
      /** 201 med hela chatten. 402 när budgeten är slut, 409 medan en körning eller sökning pågår. */
      create: (id: string, message: string) =>
        apiFetch<CampaignChat>(`/campaign/profiles/${id}/chats`, { method: "POST", body: JSON.stringify({ message }) }),
      get: (chatId: number) => apiFetch<CampaignChat>(`/campaign/chats/${chatId}`),
      send: (chatId: number, body: { message: string } | { confirm: true }) =>
        apiFetch<CampaignChat>(`/campaign/chats/${chatId}/messages`, { method: "POST", body: JSON.stringify(body) }),
      /** 202: klippen görs i bakgrunden, en körning per video. run_ids saknas hos äldre servrar. */
      makeClips: (chatId: number, hitIds: number[]) =>
        apiFetch<{ run_id: number; run_ids?: number[] }>(`/campaign/chats/${chatId}/clips`, { method: "POST", body: JSON.stringify({ hit_ids: hitIds }) }),
    },
    // --- Ställ in och konton (flode-kampanjer). ---
    /** Formuläret Ställ in. Fält som inte skickas rörs inte, och resten av profilen står kvar. */
    saveSettings: (id: string, body: CampaignSettingsBody) =>
      apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/settings`, { method: "PUT", body: JSON.stringify(body) }),
    /** Ta bort overlay-bilden. Filens namn står kvar i profilen. */
    deleteOverlay: (id: string) => apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/overlay`, { method: "DELETE" }),
    /** Kopplade TikTok-konton och YouTube-kanaler, med kampanjerna som har valt dem. */
    accounts: () => apiFetch<ConnectedAccounts>("/campaign/accounts"),
    /** Ny kampanj. warning: till exempel att Twitch inte svarade, men kampanjen sparades. */
    createProfile: (body: CampaignProfileCreate) =>
      apiFetch<CampaignProfileDetail & { warning?: string | null }>("/campaign/profiles", { method: "POST", body: JSON.stringify(body) }),
  },
};

// --- Hem, Projekt och Egna videor (flode-hem) ---

/** Avbryt ett projekt som hämtar klipp. 202, eller 409 när inget pågår. */
export const cancelRun = (runId: number) =>
  apiFetch<{ cancelling: boolean }>(`/campaign/runs/${runId}/cancel`, { method: "POST" });

/** Samma som api.campaign.upload, men med förloppet för uppladdningen i procent (fetch kan inte visa det). */
export function uploadWithProgress(form: FormData, onProgress: (percent: number) => void): Promise<CampaignClip> {
  if (DEMO) return Promise.reject(new Error(DEMO_OFF));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${PROXY}/campaign/uploads`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve(JSON.parse(xhr.responseText) as CampaignClip);
      let detail: unknown;
      try {
        detail = JSON.parse(xhr.responseText).detail;
      } catch {
        detail = undefined;
      }
      if (xhr.status === 503 && detail === "Backend unavailable") return reject(new Error("The app server is not responding."));
      reject(new Error(typeof detail === "string" ? detail : `The server answered with error ${xhr.status}. Try again.`));
    };
    xhr.onerror = () => reject(new Error("The app server is not responding."));
    xhr.send(form);
  });
}

// --- Ta bort VOD-filen på ett projekt (projekt-ta-bort) ---

/** Ta bort projektets VOD-fil och delar. Klippen och klipploggen finns kvar. 409 medan projektet pågår. */
export const deleteRunVod = (runId: number) =>
  apiFetch<FreedSpace>(`/campaign/runs/${runId}/vod`, { method: "DELETE" });
