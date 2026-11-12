import type {
  CampaignClip,
  CampaignClipPatch,
  CampaignProfile,
  CampaignRunCreate,
  CampaignRunInfo,
  CampaignStatus,
  CampaignSummary,
  Clip,
  Run,
} from "./types";

// JSON API calls go through the Next.js rewrite proxy (/api/* → FastAPI).
// This avoids CORS and removes the need for NEXT_PUBLIC_API_BASE in the browser.
const PROXY = "/api";

// SSE and file downloads bypass the proxy — they need a persistent/streaming
// connection that the Next.js rewrite layer doesn't guarantee.
const DIRECT = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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
    list: () => apiFetch<Run[]>("/runs"),
    get: (id: number) => apiFetch<Run>(`/runs/${id}`),
    delete: (id: number) => apiFetch<void>(`/runs/${id}`, { method: "DELETE" }),
  },
  clips: {
    list: (runId: number) => apiFetch<Clip[]>(`/runs/${runId}/clips`),
    patch: (id: number, body: { approved?: boolean; title?: string }) =>
      apiFetch<Clip>(`/clips/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  },
  campaign: {
    profiles: () => apiFetch<CampaignProfile[]>("/campaign/profiles"),
    runs: {
      list: () => apiFetch<CampaignRunInfo[]>("/campaign/runs"),
      create: (body: CampaignRunCreate) =>
        apiFetch<Run>("/campaign/runs", { method: "POST", body: JSON.stringify(body) }),
      finish: (runId: number) =>
        apiFetch<{ started: boolean }>(`/campaign/runs/${runId}/finish`, { method: "POST" }),
    },
    clips: {
      list: (filter: { status?: CampaignStatus; vod_id?: string } = {}) => {
        const query = new URLSearchParams(filter).toString();
        return apiFetch<CampaignClip[]>(`/campaign/clips${query ? `?${query}` : ""}`);
      },
      patch: (id: number, body: CampaignClipPatch) =>
        apiFetch<CampaignClip>(`/campaign/clips/${id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),
    },
    summary: () => apiFetch<CampaignSummary>("/campaign/summary"),
    videoUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}`,
    downloadUrl: (clip: CampaignClip) => `${PROXY}${clip.video_url}?download=1`,
  },
  sseUrl: (runId: number) => `${DIRECT}/runs/${runId}/progress`,
  // File downloads go through the proxy (same origin) to avoid mixed-content
  // blocks when the dashboard is served over HTTPS but the API is plain HTTP.
  fileUrl: (runId: number, filename: string) =>
    `${PROXY}/runs/${runId}/clips/file/${encodeURIComponent(filename)}`,
};
