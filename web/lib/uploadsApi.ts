// Anropen för utkast på Egna videor. Allt går via proxyn /api, som lägger på API-nyckeln på servern.
import type { CampaignClip, MusicFile, UploadOptions } from "./types";

const PROXY = "/api";
const BASE = "/campaign/uploads";

/** Valen för egna videor, med kampanjens tak och TikToks säkra zon (andelar av höjden). */
export type DraftOptions = UploadOptions & {
  max_seconds?: number | null;
  safe_zone?: { top: number; bottom: number } | null;
};

/** POST /campaign/uploads/drafts: videon på servern, utan kampanj. */
export interface Draft {
  draft_id: string;
  name: string;
  duration_s: number;
  width: number;
  height: number;
  has_audio: boolean;
}

/** POST /campaign/uploads/drafts/{id}/render: hela klippet, som det blir. */
export interface DraftRender {
  video_url: string;
  length_s: number;
  problems: string[];
  warnings: string[];
  caption: string;
  choices_key: string;
}

const DOWN = "Appens server svarar inte. Starta den och försök igen.";

/** Felet för Robin ur serverns svar: detail på svenska när den finns. */
function errorFor(status: number, body: string): Error {
  let detail: unknown;
  try {
    detail = JSON.parse(body).detail;
  } catch {
    detail = undefined;
  }
  if (status === 503 && detail === "Backend unavailable") return new Error(DOWN);
  if (typeof detail === "string") return new Error(detail);
  if (status === 422) return new Error("Servern godtog inte valen. Kontrollera fälten och försök igen.");
  return new Error(`Servern svarade med fel ${status}. Försök igen.`);
}

async function send<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${PROXY}${path}`, init);
  } catch {
    throw new Error(DOWN);
  }
  if (!res.ok) throw errorFor(res.status, await res.text().catch(() => ""));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const draftPath = (id: string) => `${BASE}/drafts/${encodeURIComponent(id)}`;

export class UploadAborted extends Error {
  constructor() {
    super("Uppladdningen avbröts.");
  }
}

/** Laddar upp ett formulär med förloppet i procent. abort avbryter och ger UploadAborted. */
function uploadForm<T>(path: string, form: FormData, onProgress: (percent: number) => void): { done: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const done = new Promise<T>((resolve, reject) => {
    xhr.open("POST", `${PROXY}${path}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300 ? resolve(JSON.parse(xhr.responseText) as T) : reject(errorFor(xhr.status, xhr.responseText));
    xhr.onerror = () => reject(new Error(DOWN));
    xhr.onabort = () => reject(new UploadAborted());
    xhr.send(form);
  });
  return { done, abort: () => xhr.abort() };
}

/** Laddar upp videon som ett utkast, med förloppet i procent. abort avbryter och ger UploadAborted. */
export function uploadDraft(file: File, onProgress: (percent: number) => void): { done: Promise<Draft>; abort: () => void } {
  const form = new FormData();
  form.append("file", file);
  return uploadForm<Draft>(`${BASE}/drafts`, form, onProgress);
}

/** POST /campaign/uploads/music: en ljudfil eller video till kampanjens mapp musik/<kampanj>/. */
export function uploadMusic(file: File, profile: string, onProgress: (percent: number) => void): { done: Promise<MusicFile>; abort: () => void } {
  const form = new FormData();
  form.append("file", file);
  form.append("profile", profile);
  return uploadForm<MusicFile>(`${BASE}/music`, form, onProgress);
}

export const uploadsApi = {
  options: (profile: string) => send<DraftOptions>(`${BASE}/options?profile=${encodeURIComponent(profile)}`),
  /** Bilden ur det färdiga klippet. query kommer från frameQuery i lib/uploads.ts. */
  frameUrl: (id: string, query: string) => `${PROXY}${draftPath(id)}/frame?${query}`,
  /** Samma bild som en Blob, så att serverns fel går att visa. */
  frame: async (url: string, signal: AbortSignal): Promise<Blob> => {
    const res = await fetch(url, { signal });
    if (!res.ok) throw errorFor(res.status, await res.text().catch(() => ""));
    return res.blob();
  },
  render: (id: string, form: FormData) => send<DraftRender>(`${draftPath(id)}/render`, { method: "POST", body: form }),
  /** video_url från render är en serverväg; uppspelningen går via proxyn. */
  videoUrl: (render: DraftRender) => `${PROXY}${render.video_url}`,
  save: (id: string, form: FormData) => send<CampaignClip>(`${draftPath(id)}/save`, { method: "POST", body: form }),
  remove: (id: string) => send<void>(draftPath(id), { method: "DELETE" }),
};
