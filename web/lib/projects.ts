import { duration, serverTime } from "./utils";
import type { CampaignProject, CampaignRunInfo, CampaignStatus, ProjectFile, RunStatus } from "./types";

type Counts = Partial<Record<CampaignStatus, number>>;

// Samma namn som stegen på projektsidan (GET /campaign/runs/{id}). Korten vet inte om kampanjen väljer med
// Claude eller med signalerna, så steget heter bara Väljer klipp här.
export const STAGE_LABEL: Record<string, string> = {
  download: "Downloading the VOD",
  transcribe: "Transcribing",
  moments: "Finding moments",
  select: "Choosing clips",
  clip: "Cutting",
  finish: "Making campaign-ready",
};

export const countClips = (counts: Counts | undefined) =>
  Object.values(counts ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);

export const isRunning = (status: RunStatus) => status === "pending" || status === "running";

/** Klipp att granska och att rätta: det som räknas till Att granska. */
export const toReview = (counts: Counts) => (counts.renderad ?? 0) + (counts.underkand ?? 0);

/** "2 att granska, 1 att rätta": det som väntar på Robin. null när inget väntar. */
export function waitingText(counts: Counts): string | null {
  const parts = [
    counts.renderad ? `${counts.renderad} to review` : null,
    counts.underkand ? `${counts.underkand} to fix` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

/** Felets första rad, så att Robin ser varför utan att öppna sidan. */
export const firstLine = (error: string | null | undefined) => error?.split("\n")[0] || null;

/** "Transkriberar 42 %", eller "Startar" innan servern har sagt steget. */
export function stageText(stage: string | null, pct: number | null | undefined): string {
  const label = stage ? (STAGE_LABEL[stage] ?? stage) : "Starting";
  return pct != null ? `${label} ${Math.round(pct)} %` : label;
}

/** Läget för en fil i ett projekt. */
export function fileState(file: ProjectFile): string {
  if (file.status === "pending") return "Queued";
  if (file.status === "running") return stageText(file.stage, file.pct);
  return file.status === "failed" ? "Failed" : "Done";
}

/** "1 fil, 3 klipp". Ett projekt med bara uppladdningar har inga filer: "3 klipp". */
export function projectSize(project: CampaignProject): string {
  const n = project.files.length;
  const clips = `${countClips(project.clip_counts)} clips`;
  return n ? `${n} ${n === 1 ? "file" : "files"}, ${clips}` : clips;
}

/** Filen som körs just nu, eller den som står i kö. */
export const activeFile = (project: CampaignProject) =>
  project.files.find((f) => f.status === "running") ?? project.files.find((f) => f.status === "pending");

/** Den senaste filen med fel. */
export const failedFile = (project: CampaignProject) =>
  [...project.files].reverse().find((f) => f.status === "failed" && f.error) ?? null;

export const runTime = (r: CampaignRunInfo) => (r.created ? serverTime(r.created) : 0);
/** Ett projekt sorteras efter när något senast hände i det. */
export const projectTime = (p: CampaignProject) => {
  const when = p.updated ?? p.created;
  return when ? serverTime(when) : 0;
};

export type ProjectEntry =
  | { kind: "run"; key: string; time: number; run: CampaignRunInfo }
  | { kind: "project"; key: string; time: number; project: CampaignProject };

/**
 * Korten på Hem och Projekt, senaste först. En körning i ett projekt (en egen fil) får inget eget kort:
 * den syns i projektets kort. Twitch-körningar har inget projekt och blir ett kort var.
 */
export function projectEntries(runs: CampaignRunInfo[], projects: CampaignProject[]): ProjectEntry[] {
  return [
    ...runs
      .filter((run) => run.project_id == null)
      .map((run) => ({ kind: "run" as const, key: `run-${run.run_id}`, time: runTime(run), run })),
    ...projects.map((project) => ({ kind: "project" as const, key: `project-${project.id}`, time: projectTime(project), project })),
  ].sort((a, b) => b.time - a.time);
}

/**
 * Det senaste försöket med samma stream och kampanj. En körning från kampanjens chatt är inget försök:
 * den väljs inte, och den ersätts inte av ett nytt försök.
 */
export function latestAttempt(list: CampaignRunInfo[], run: Pick<CampaignRunInfo, "vod_id" | "profile" | "origin">): number | null {
  if (run.origin === "chat") return null;
  const ids = list
    .filter((r) => r.vod_id === run.vod_id && r.profile === run.profile && r.origin !== "chat")
    .map((r) => r.run_id);
  return ids.length ? Math.max(...ids) : null;
}

const day = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short" });
const dayOf = (iso: string | null | undefined) => (iso ? day.format(serverTime(iso)) : null);

/** "4 min, 17 nov.": filens längd och när den kom. Tom när servern inte vet. */
export function fileMeta(file: ProjectFile): string {
  return [file.duration_s ? duration(file.duration_s) : null, dayOf(file.created)].filter(Boolean).join(", ");
}

/** "Egna filer, senast 17 nov." under kortets rubrik. Medan en fil körs står filerna och klippen här, inte i märket. */
export function projectMeta(project: CampaignProject): string {
  const when = dayOf(project.updated ?? project.created);
  return [project.active ? projectSize(project) : "Own files", when && `last ${when}`].filter(Boolean).join(", ");
}

/** "klipp.mp4: Berget svarade inte": den senaste filen med fel och felets första rad. */
export function projectError(project: CampaignProject): string | null {
  const file = failedFile(project);
  return file ? `${file.title || file.vod_id}: ${firstLine(file.error)}` : null;
}
