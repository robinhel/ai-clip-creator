import type {
  CampaignClip,
  CampaignClipPatch,
  CampaignExamples,
  CampaignProfile,
  CampaignProfileCreate,
  CampaignProfileDetail,
  CampaignRunCreate,
  CampaignRunDetail,
  CampaignRunInfo,
  CampaignStatus,
  CaptionWord,
  ClipAccounts,
  ClipVariant,
  VersionCreate,
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

// JSON API calls go through the Next.js rewrite proxy (/api/* → FastAPI).
// This avoids CORS and removes the need for NEXT_PUBLIC_API_BASE in the browser.
const PROXY = "/api";

// Inloggningen på TikTok öppnas direkt mot servern, inte via proxyn: TikTok leder tillbaka till :8000.
const DIRECT = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

const FIELD_LABEL: Record<string, string> = {
  views: "visningar",
  likes: "gilla",
  comments: "kommentarer",
  shares: "delningar",
  url: "länken",
  platform: "plattform",
  caption: "captionen",
  title: "titeln",
};

// shortcut: läst en gång per sidladdning och delad av alla klipprader; en ändring i en annan flik syns först efter omladdning.
let preferences: Promise<Preferences> | null = null;

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
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
      throw new Error("Servern svarar inte. Kör FastAPI på port 8000?");
    }
    if (typeof detail === "string") throw Object.assign(new Error(detail), { status: res.status });
    // FastAPI:s valideringsfel (422) har detail som en lista med fält och meddelande.
    if (Array.isArray(detail)) {
      const fields = detail.map((d) => d?.loc?.at?.(-1)).filter(Boolean).map((f) => FIELD_LABEL[f] ?? f);
      throw new Error(
        `Servern godtog inte värdet${fields.length ? ` i ${fields.join(", ")}` : ""}. Kontrollera fälten och försök igen.`
      );
    }
    throw new Error(`Servern svarade med fel ${res.status}. Försök igen.`);
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
        `${PROXY}/campaign/profiles/${id}/frame${vodId ? `?vod_id=${vodId}` : ""}`,
    },
    vod: (vodId: string) => apiFetch<VodInfo>(`/campaign/vod/${vodId}`),
    streams: () => apiFetch<StreamInfo[]>("/campaign/streams"),
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
    clips: {
      list: (filter: { status?: CampaignStatus; vod_id?: string; run_id?: number } = {}) => {
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
      edit: (id: number) => apiFetch<ClipEditInfo>(`/campaign/clips/${id}/edit`),
      saveEdit: (id: number, body: { in_s?: number; out_s?: number; title?: string; words?: CaptionWord[] }) =>
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
      connectUrl: `${DIRECT}/campaign/youtube/connect`,
    },
    tiktok: {
      status: () => apiFetch<TikTokStatus>("/campaign/tiktok/status"),
      disconnect: () => apiFetch<{ connected: false }>("/campaign/tiktok/disconnect", { method: "POST" }),
      creatorInfo: (openId: string) =>
        apiFetch<TikTokCreatorInfo>(`/campaign/tiktok/accounts/${encodeURIComponent(openId)}/creator_info`),
      disconnectAccount: (openId: string) =>
        apiFetch<unknown>(`/campaign/tiktok/accounts/${encodeURIComponent(openId)}/disconnect`, { method: "POST" }),
      connectUrl: `${DIRECT}/campaign/tiktok/connect`,
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
    downloadUrl: (clip: CampaignClip) =>
      `${PROXY}${clip.video_url}${clip.video_url.includes("?") ? "&" : "?"}download=1`,
    // --- Kampanjen som nav (kontrakt-chatt.md): material, kampanjens klipp och chatten. ---
    material: (id: string) => apiFetch<MaterialItem[]>(`/campaign/profiles/${id}/material`),
    /** Källans video med Range-stöd, för att spela en träff. */
    materialVideoUrl: (vodId: string) => `${PROXY}/campaign/material/${encodeURIComponent(vodId)}/video`,
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
  },
};
