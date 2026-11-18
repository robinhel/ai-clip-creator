// Live-bevakningen per kampanj (/campaign/live). Egen liten fetch mot proxyn, som apiFetch i api.ts.

export type PeakStatus = "vantar" | "hamtar" | "ko" | "kor" | "klar" | "fel" | "hoppad";

/** En topp i chatten under en live-stream. */
export type Peak = {
  n: number;
  /** ISO-tid för toppen. */
  at: string;
  /** Sekunder in i VOD:en. */
  vod_s: number;
  status: PeakStatus;
  run_id: number | null;
  reason: string | null;
};

/** Bevakningen för en kampanj som har en kanal. */
export type LiveWatch = {
  profile: string;
  channel: string;
  watching: boolean;
  live: boolean;
  vod_id: string | null;
  error: string | null;
  peaks: Peak[];
};

async function liveFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...init.headers } });
  if (res.ok) return res.json() as Promise<T>;
  let detail: unknown;
  try {
    detail = JSON.parse(await res.text()).detail;
  } catch {
    detail = undefined;
  }
  if (res.status === 503 && detail === "Backend unavailable") {
    throw new Error("Appens server är inte igång. Starta den och tryck Försök igen.");
  }
  if (typeof detail === "string") throw new Error(detail);
  throw new Error(`Servern svarade med fel ${res.status}. Försök igen.`);
}

export const liveApi = {
  list: () => liveFetch<LiveWatch[]>("/campaign/live"),
  setWatching: (profile: string, watching: boolean) =>
    liveFetch<LiveWatch>(`/campaign/live/${encodeURIComponent(profile)}`, {
      method: "PUT",
      body: JSON.stringify({ watching }),
    }),
};
