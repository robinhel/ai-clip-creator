"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, Keyboard, Loader2, RotateCcw, Upload } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { ClipResultRow, ignoreKey } from "@/components/ClipResultRow";
import { DeleteVod } from "@/components/DeleteVod";
import { Progress } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import { campaignLabel } from "@/lib/campaignName";
import { fileMeta, fileState, firstLine, isRunning, projectSize, waitingText } from "@/lib/projects";
import { cn } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignProject, CampaignStatus, ProjectFile } from "@/lib/types";

const FILTERS: { id: string; label: string; statuses?: CampaignStatus[] }[] = [
  { id: "alla", label: "All" },
  { id: "granska", label: "To review", statuses: ["renderad"] },
  { id: "godkanda", label: "Approved", statuses: ["godkand"] },
  // Allt efter Markera som postad, så att ett postat klipp inte försvinner ur flikarna.
  { id: "postade", label: "Posted", statuses: ["postad", "postad_utan_lank", "inskickad", "analytics_inskickad", "betald"] },
  { id: "nekade", label: "Rejected", statuses: ["nekad"] },
  { id: "underkanda", label: "Failed check", statuses: ["underkand"] },
];

type Sort = "score" | "time";

/** Betyg först, klipp utan betyg sist. Tidsordning efter start i VOD:en. */
function sortClips(clips: CampaignClip[], sort: Sort): CampaignClip[] {
  const key = (c: CampaignClip) => (sort === "score" ? -(c.score ?? -1) : (c.start_s ?? Infinity));
  return [...clips].sort((a, b) => key(a) - key(b) || a.id - b.id);
}

const KEYS: [string, string][] = [
  ["J", "next clip"],
  ["K", "previous clip"],
  ["Y", "yes"],
  ["N", "no"],
  ["E", "edit"],
  ["C", "copy caption"],
];

function KeysHelp() {
  return (
    <details className="relative">
      <summary className="flex cursor-pointer items-center gap-1.5 rounded-[9px] border border-line px-3 py-1.5 text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
        <Keyboard className="size-3.5" aria-hidden />
        Keys
      </summary>
      <dl className="absolute right-0 z-10 mt-1.5 grid w-56 grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 rounded-xl border border-line bg-panel p-3 shadow-lg">
        {KEYS.map(([key, label]) => (
          <div key={key} className="contents">
            <dt>
              <kbd className="inline-grid min-w-6 place-items-center rounded-md border border-line bg-background px-1.5 font-mono text-xs">{key}</kbd>
            </dt>
            <dd className="text-muted-foreground">{label}</dd>
          </div>
        ))}
        <p className="col-span-2 mt-1 text-xs text-muted-foreground">Not while you type in a field.</p>
      </dl>
    </details>
  );
}

const filterCount = (f: (typeof FILTERS)[number], clips: CampaignClip[]) =>
  f.statuses ? clips.filter((c) => f.statuses?.includes(c.status)).length : clips.length;

/** Flikarna, tangenterna och sorteringen ovanför klippen. */
function ClipToolbar({
  clips,
  filter,
  onFilter,
  sort,
  onSort,
}: {
  clips: CampaignClip[];
  filter: string;
  onFilter: (id: string) => void;
  sort: Sort;
  onSort: (s: Sort) => void;
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-3 text-[13px]">
      {/* flex-wrap: på en smal skärm går flikarna på två rader i stället för att skäras av. */}
      <div role="tablist" aria-label="Show clips" className="flex max-w-full flex-wrap overflow-hidden rounded-[9px] border border-line">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={filter === f.id}
            onClick={() => onFilter(f.id)}
            className="px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
          >
            {f.label} <span className="tabular-nums">{filterCount(f, clips)}</span>
          </button>
        ))}
      </div>
      <span className="flex-1" />
      <KeysHelp />
      <span className="flex items-center gap-3">
        <span className="text-muted-foreground">Sort:</span>
        <div className="flex overflow-hidden rounded-[9px] border border-line">
          {(
            [
              ["score", "Score"],
              ["time", "Time order"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              aria-pressed={sort === id}
              onClick={() => onSort(id)}
              className="px-3 py-1.5 text-muted-foreground hover:text-foreground aria-pressed:bg-panel aria-pressed:text-foreground"
            >
              {label}
            </button>
          ))}
        </div>
      </span>
    </div>
  );
}

/** "j" eller "k" när tangenten ska flytta mellan klippen, annars null. */
function moveKey(e: KeyboardEvent, count: number): "j" | "k" | null {
  const key = e.key.toLowerCase();
  if (key !== "j" && key !== "k") return null;
  return ignoreKey(e) || count === 0 ? null : key;
}

/** J och K flyttar mellan klippen i listan som den visas, rullar dit och markerar raden. */
function useClipKeys(ids: number[], setActiveId: (update: (current: number | null) => number | null) => void) {
  const order = useRef<number[]>([]);
  order.current = ids;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const key = moveKey(e, order.current.length);
      if (!key) return;
      e.preventDefault();
      setActiveId((current) => {
        const i = current == null ? -1 : order.current.indexOf(current);
        const next = key === "j" ? Math.min(i + 1, order.current.length - 1) : Math.max(i - 1, 0);
        const id = order.current[next];
        document.querySelector(`[data-clip-id="${id}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
        return id;
      });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setActiveId]);
}

/** Fliken i adressen (?filter=granska), så att andra sidor kan länka till rätt flik. */
function useFilterInUrl(): [string, (id: string) => void] {
  const [filter, setFilter] = useState("alla");
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("filter");
    if (wanted && FILTERS.some((f) => f.id === wanted)) setFilter(wanted);
  }, []);
  function chooseFilter(id: string) {
    setFilter(id);
    const url = new URL(window.location.href);
    if (id === "alla") url.searchParams.delete("filter");
    else url.searchParams.set("filter", id);
    window.history.replaceState(null, "", url);
  }
  return [filter, chooseFilter];
}

/**
 * Klippen med flikarna Att granska, Godkända, Postade och de andra. De syns medan projektet pågår: de kommer
 * ett i taget i sista steget. Samma lista på en fils sida och på projektets sida.
 */
export function ClipsSection({
  running,
  clips,
  formats,
  onUpdate,
  onReload,
}: {
  /** Projektet hämtar klipp just nu, så fler kan komma. */
  running: boolean;
  clips: CampaignClip[];
  formats: CampaignProfile["formats"];
  onUpdate: (clip: CampaignClip) => void;
  onReload: () => void;
}) {
  const [filter, chooseFilter] = useFilterInUrl();
  const [sort, setSort] = useState<Sort>("score");
  const [activeId, setActiveId] = useState<number | null>(null);
  const sorted = useMemo(() => sortClips(clips, sort), [clips, sort]);
  const active = FILTERS.find((f) => f.id === filter) ?? FILTERS[0];
  const visible = sorted.filter((c) => !active.statuses || active.statuses.includes(c.status));
  useClipKeys(
    visible.map((c) => c.id),
    setActiveId
  );
  return (
    <section className="mt-8">
      {running && (
        <p className="mb-3 text-sm text-muted-foreground">
          {clips.length === 1 ? "1 clip is done." : `${clips.length} clips are done.`} More can come while the project runs.
        </p>
      )}
      {clips.length > 0 && <ClipToolbar clips={clips} filter={filter} onFilter={chooseFilter} sort={sort} onSort={setSort} />}
      {visible.length === 0 ? (
        <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          {clips.length === 0 ? "No clips in this project." : "No clips with that status."}
        </p>
      ) : (
        visible.map((clip) => (
          <ClipResultRow
            // Ny nyckel när servern har sparat, så att fälten visar det sparade.
            key={`${clip.id}-${clip.updated}`}
            clip={clip}
            number={sorted.indexOf(clip) + 1}
            formats={formats}
            active={clip.id === activeId}
            onUpdate={onUpdate}
            onReload={onReload}
          />
        ))
      )}
    </section>
  );
}

const POLL_MS = 5000;
const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** Kör igen för en fil som stoppade. Sidan läser om projektet när servern har tagit emot det. */
function RetryFile({ file, onStarted }: { file: ProjectFile; onStarted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function retry() {
    setBusy(true);
    setError(null);
    try {
      await api.campaign.runs.retry(file.run_id);
      onStarted();
    } catch (err) {
      setError(errorText(err, "Could not run again"));
    }
    setBusy(false);
  }
  return (
    <>
      <Button className="h-7 text-[13px]" onClick={retry} disabled={busy}>
        {busy ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCcw aria-hidden />}
        {busy ? "Starting…" : "Run again"}
      </Button>
      {error && <p className="basis-full text-[13px] text-[#fca5a5]">{error}</p>}
    </>
  );
}

function ErrorLine({ error }: { error: string | null }) {
  const line = firstLine(error);
  return line ? <p className="mt-0.5 line-clamp-2 text-[13px] break-words text-[#fca5a5]">{line}</p> : null;
}

/** En fil i projektet: läget, felet och knapparna. Filens sida har stegen och bara filens klipp. */
function FileRow({ file, onChanged }: { file: ProjectFile; onChanged: () => void }) {
  const active = isRunning(file.status);
  const failed = file.status === "failed";
  const meta = fileMeta(file);
  return (
    <li data-file={file.run_id} className="border-b border-line py-3 last:border-b-0">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-[1_1_16rem]">
          <p className="truncate font-medium">{file.title || file.vod_id}</p>
          <p className="text-[13px] text-muted-foreground">
            {meta && `${meta}, `}
            <span className={cn(active && "text-brand", failed && "font-medium text-[#fca5a5]")}>{fileState(file)}</span>
          </p>
          {failed && <ErrorLine error={file.error} />}
          {active && <Progress pct={file.pct} />}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {failed && <RetryFile file={file} onStarted={onChanged} />}
          <DeleteVod run={file} small onDeleted={onChanged} />
          <Link href={`/runs/${file.run_id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
            Open the file's page
            <ChevronRight aria-hidden />
          </Link>
        </div>
      </div>
    </li>
  );
}

function Files({ project, onChanged }: { project: CampaignProject; onChanged: () => void }) {
  return (
    <section aria-label="Files" className="mt-7 max-w-[860px]">
      <h2 className="mb-1 font-semibold">Files</h2>
      {project.files.length === 0 ? (
        <p className="text-sm text-muted-foreground">No own files yet. The clips below are uploaded videos.</p>
      ) : (
        <ul className="rounded-2xl border border-line px-3.5">
          {project.files.map((file) => (
            <FileRow key={file.run_id} file={file} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PageMessage({ error }: { error: string | null }) {
  return (
    <main className="w-full max-w-[1180px] space-y-4 px-6 py-9 md:px-11">
      {error ? (
        <>
          <p role="alert" className="text-bad">
            {error}
          </p>
          <Link href="/projects" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
            To all projects
          </Link>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Loading the project…</p>
      )}
    </main>
  );
}

/** Läser om var femte sekund medan `on` gäller, som Hem gör. */
function usePolling(on: boolean, reload: () => Promise<void>) {
  // Räknas upp efter varje omläsning, så att nästa startar också när anropet misslyckades.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = setTimeout(() => reload().finally(() => setTick((t) => t + 1)), POLL_MS);
    return () => clearTimeout(timer);
  }, [on, tick, reload]);
}

/** Kampanjens format, för raderna med klipp. */
function useFormats(profile: string | undefined): CampaignProfile["formats"] {
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  useEffect(() => {
    if (profile) api.campaign.profiles().then(setProfiles).catch(() => {});
  }, [profile]);
  return profiles.find((p) => p.name === profile)?.formats ?? [];
}

/** Projektet och klippen. Läser om var femte sekund medan en fil körs. */
function useProject(id: number) {
  const validId = Number.isInteger(id) && id > 0;
  const [project, setProject] = useState<CampaignProject | null>(null);
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [error, setError] = useState<string | null>(validId ? null : "The project does not exist. Check the link.");
  const fetchClips = useCallback(async () => {
    try {
      setClips(await api.campaign.clips.list({ project_id: id }));
    } catch {
      // Felet för projektet visas redan; klippen hämtas igen vid nästa läsning.
    }
  }, [id]);

  const reload = useCallback(async () => {
    if (!validId) return;
    try {
      setProject(await api.campaign.projects.get(id));
      setError(null);
    } catch (err) {
      setError(errorText(err, "Could not load the project"));
    }
    await fetchClips();
  }, [id, validId, fetchClips]);

  useEffect(() => {
    reload();
  }, [reload]);

  usePolling(project?.active ?? false, reload);
  return { project, clips, setClips, error, reload, fetchClips };
}

function ProjectHeader({ project }: { project: CampaignProject }) {
  const todo = waitingText(project.clip_counts);
  return (
    <div className="mt-2 flex flex-wrap items-start gap-4">
      <div className="min-w-0 flex-[1_1_20rem]">
        <h1 className="text-3xl font-bold tracking-tight break-words">{project.title || `Own files for ${project.streamer}`}</h1>
        <p className="mt-1.5 text-muted-foreground">
          {projectSize(project)}
          {todo && <span className="font-medium text-brand">, {todo}</span>}
        </p>
        <p className="text-sm text-muted-foreground">Campaign: {campaignLabel(project.profile)}</p>
      </div>
      <Link href={`/uploads?kampanj=${encodeURIComponent(project.profile)}`} className={buttonVariants({ variant: "outline" })}>
        <Upload aria-hidden />
        Upload more
      </Link>
    </div>
  );
}

/** Kampanjens projekt för egna filer: filerna med Kör igen och Ta bort VOD-filen, och alla klippen. */
export function ProjectClient({ id }: { id: number }) {
  const { project, clips, setClips, error, reload, fetchClips } = useProject(id);
  const formats = useFormats(project?.profile);

  if (!project) return <PageMessage error={error} />;

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
  }

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <Link href="/projects" className="text-sm text-muted-foreground hover:text-foreground">
        ← All projects
      </Link>
      <ProjectHeader project={project} />
      {error && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Could not reload the project: {error}
          {project.active ? " Trying again in a few seconds." : ""}
        </p>
      )}
      <Files project={project} onChanged={reload} />
      {clips && <ClipsSection running={project.active} clips={clips} formats={formats} onUpdate={handleUpdate} onReload={fetchClips} />}
    </main>
  );
}
