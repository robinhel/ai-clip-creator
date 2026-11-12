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
  ClipTranscript,
  Rect,
  ReframeInfo,
  Run,
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
    if (typeof detail === "string") throw new Error(detail);
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
      yaml: (id: string) => apiFetch<{ yaml: string }>(`/campaign/profiles/${id}/yaml`),
      saveYaml: (id: string, yaml: string) =>
        apiFetch<CampaignProfileDetail>(`/campaign/profiles/${id}/yaml`, {
          method: "PUT",
          body: JSON.stringify({ yaml }),
        }),
      reframe: (id: string) => apiFetch<ReframeInfo>(`/campaign/profiles/${id}/reframe`),
      saveReframe: (id: string, body: { webcam_rect: Rect; gameplay_rect: Rect; split_ratio: number }) =>
        apiFetch<ReframeInfo>(`/campaign/profiles/${id}/reframe`, { method: "PUT", body: JSON.stringify(body) }),
      frameUrl: (id: string, vodId?: string) =>
        `${PROXY}/campaign/profiles/${id}/frame${vodId ? `?vod_id=${vodId}` : ""}`,
    },
    vod: (vodId: string) => apiFetch<VodInfo>(`/campaign/vod/${vodId}`),
    runs: {
      list: () => apiFetch<CampaignRunInfo[]>("/campaign/runs"),
      get: (runId: number) => apiFetch<CampaignRunDetail>(`/campaign/runs/${runId}`),
      create: (body: CampaignRunCreate) =>
        apiFetch<{ id: number }>("/campaign/runs", { method: "POST", body: JSON.stringify(body) }),
      finish: (runId: number) =>
        apiFetch<{ started: boolean }>(`/campaign/runs/${runId}/finish`, { method: "POST" }),
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
      sendToTikTok: (id: number) =>
        apiFetch<TikTokUpload>(`/campaign/clips/${id}/tiktok`, { method: "POST" }),
    },
    tiktok: {
      status: () => apiFetch<TikTokStatus>("/campaign/tiktok/status"),
      disconnect: () => apiFetch<{ connected: false }>("/campaign/tiktok/disconnect", { method: "POST" }),
      connectUrl: `${DIRECT}/campaign/tiktok/connect`,
    },
    summary: () => apiFetch<CampaignSummary>("/campaign/summary"),
    /** En sökväg från servern (till exempel overlay.url) via proxyn. */
    proxied: (path: string) => `${PROXY}${path}`,
    videoUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}`,
    downloadUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}?download=1`,
  },
};
