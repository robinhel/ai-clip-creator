import type { CampaignClip, CampaignSummary } from "./types";
import { DEMO, demoFetch } from "./demo";

// Bara för serverkomponenter: samma bas och nyckel som proxyn i app/api/[...path]/route.ts.
const API_BASE =
  process.env.API_BASE ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** GET mot FastAPI från servern. null om servern inte svarar, så att sidan kan hämta i webbläsaren i stället. */
async function serverGet<T>(path: string): Promise<T | null> {
  if (DEMO) return demoFetch<T>(path).catch(() => null);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: process.env.API_KEY ? { "x-api-key": process.env.API_KEY } : undefined,
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export const serverApi = {
  summary: () => serverGet<CampaignSummary>("/campaign/summary"),
  /** postedOff: också postade klipp från avstängda kampanjer, för Statistik. */
  clips: (postedOff = false) => serverGet<CampaignClip[]>(`/campaign/clips${postedOff ? "?posted_off=true" : ""}`),
};
