import type {
  CampaignClip,
  CampaignClipPatch,
  CampaignProfile,
  CampaignProfileCreate,
  CampaignProfileDetail,
  CampaignRunCreate,
  CampaignRunDetail,
  CampaignRunInfo,
  CampaignStatus,
  CampaignSummary,
  KeyGroup,
  SetupCheck,
  KeyTest,
  ClipEditInfo,
  ClipPostPatch,
  ClipTranscript,
  Platform,
  Rect,
  ReframeInfo,
  Run,
  StreamInfo,
  TikTokStatus,
  TikTokUpload,
  VodInfo,
} from "./types";

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

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${PROXY}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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
      edit: (id: number) => apiFetch<ClipEditInfo>(`/campaign/clips/${id}/edit`),
      saveEdit: (id: number, body: { in_s?: number; out_s?: number; title?: string }) =>
        apiFetch<CampaignClip>(`/campaign/clips/${id}/edit`, { method: "POST", body: JSON.stringify(body) }),
      // Utan openId väljer servern det enda kopplade kontot.
      sendToTikTok: (id: number, openId?: string) =>
        apiFetch<TikTokUpload>(`/campaign/clips/${id}/tiktok`, {
          method: "POST",
          body: openId ? JSON.stringify({ open_id: openId }) : undefined,
        }),
    },
    tiktok: {
      status: () => apiFetch<TikTokStatus>("/campaign/tiktok/status"),
      disconnect: () => apiFetch<{ connected: false }>("/campaign/tiktok/disconnect", { method: "POST" }),
      disconnectAccount: (openId: string) =>
        apiFetch<unknown>(`/campaign/tiktok/accounts/${encodeURIComponent(openId)}/disconnect`, { method: "POST" }),
      connectUrl: `${DIRECT}/campaign/tiktok/connect`,
    },
    summary: () => apiFetch<CampaignSummary>("/campaign/summary"),
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
  },
};
