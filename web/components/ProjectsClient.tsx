"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { EntryCard, isActive } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import { campaignLabel } from "@/lib/campaignName";
import { projectEntries, toReview } from "@/lib/projects";
import type { CampaignProfile, CampaignProject, CampaignRunInfo } from "@/lib/types";

const POLL_MS = 5000;

// Filtret på status. Ett projekt räknas till Att granska när det har klipp att granska eller rätta.
const STATUS_FILTERS = [
  { id: "alla", label: "Alla" },
  { id: "pagar", label: "Pågår" },
  { id: "granska", label: "Att granska" },
  { id: "klara", label: "Klara" },
  { id: "misslyckade", label: "Misslyckade" },
] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number]["id"];

const RUN_FILTERS: Record<StatusFilter, (run: CampaignRunInfo) => boolean> = {
  alla: () => true,
  pagar: isActive,
  granska: (run) => toReview(run.clip_counts) > 0,
  klara: (run) => run.status === "done",
  misslyckade: (run) => run.status === "failed",
};

const anyFailed = (project: CampaignProject) => project.files.some((f) => f.status === "failed");

/** Ett projekt för egna filer pågår medan en fil körs, och har misslyckats när en fil har det. */
const PROJECT_FILTERS: Record<StatusFilter, (project: CampaignProject) => boolean> = {
  alla: () => true,
  pagar: (project) => project.active,
  granska: (project) => toReview(project.clip_counts) > 0,
  klara: (project) => !project.active && !anyFailed(project),
  misslyckade: anyFailed,
};

interface CampaignChoice {
  name: string;
  label: string;
}

function Filters({
  status,
  onStatus,
  campaigns,
  campaign,
  onCampaign,
}: {
  status: StatusFilter;
  onStatus: (s: StatusFilter) => void;
  campaigns: CampaignChoice[];
  campaign: string;
  onCampaign: (c: string) => void;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 text-[13px]">
      <div role="tablist" aria-label="Visa projekt" className="flex flex-wrap overflow-hidden rounded-[9px] border border-line">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={status === f.id}
            onClick={() => onStatus(f.id)}
            className="px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
          >
            {f.label}
          </button>
        ))}
      </div>
      {campaigns.length > 1 && (
        <label className="flex items-center gap-2 text-muted-foreground">
          Kampanj:
          <select
            value={campaign}
            onChange={(e) => onCampaign(e.target.value)}
            className="h-8 rounded-[9px] border border-line bg-background px-2 text-foreground [&>option]:bg-panel"
          >
            <option value="">Alla</option>
            {campaigns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <Link href="/uploads" className="ml-auto text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
        Ladda upp en egen video
      </Link>
    </div>
  );
}

function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
      <span className="flex-1">{error}</span>
      <button type="button" onClick={onRetry} className="inline-flex h-8 items-center rounded-lg border border-[#4d1d1d] px-3 font-medium hover:bg-white/5">
        Försök igen
      </button>
    </div>
  );
}

function NoProjects() {
  return (
    <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
      Inga projekt än.{" "}
      <Link href="/" className="text-brand underline-offset-4 hover:underline">
        Klistra in en Twitch-länk
      </Link>{" "}
      eller{" "}
      <Link href="/uploads" className="text-brand underline-offset-4 hover:underline">
        ladda upp en egen video
      </Link>{" "}
      för att göra det första.
    </p>
  );
}

/** Körningarna, kampanjernas projekt för egna filer och kampanjerna. Läser om var femte sekund medan något pågår, som Hem gör. */
function useProjects() {
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [projects, setProjects] = useState<CampaignProject[]>([]);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Räknas upp efter varje omläsning, så att nästa startar också när anropet misslyckades.
  const [pollTick, setPollTick] = useState(0);

  function load() {
    setError(null);
    api.campaign.runs
      .list()
      .then(setRuns)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta projekten"));
    // Ett fel här döljer bara korten för egna filer.
    api.campaign.projects.list().then(setProjects).catch(() => setProjects([]));
    api.campaign.profiles().then(setProfiles).catch(() => {});
  }

  useEffect(load, []);

  const anyActive = (runs?.some(isActive) ?? false) || projects.some((p) => p.active);
  useEffect(() => {
    if (!anyActive) return;
    const timer = setTimeout(() => {
      Promise.all([api.campaign.runs.list().then(setRuns), api.campaign.projects.list().then(setProjects)])
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [anyActive, pollTick]);

  return { runs, projects, profiles, error, load };
}

/** Kampanjerna som har projekt, med streamerns namn som i resten av appen. */
function campaignChoices(runs: CampaignRunInfo[], projects: CampaignProject[], profiles: CampaignProfile[]): CampaignChoice[] {
  const names = new Set([...runs.map((r) => r.profile), ...projects.map((p) => p.profile)]);
  return [...names].sort().map((name) => ({ name, label: profiles.find((p) => p.name === name)?.streamer || campaignLabel(name) }));
}

/** Korten som passar filtren, senaste först. */
function projectCards(runs: CampaignRunInfo[], projects: CampaignProject[], campaign: string, status: StatusFilter) {
  const inCampaign = (profile: string) => !campaign || profile === campaign;
  return projectEntries(
    runs.filter((r) => inCampaign(r.profile) && RUN_FILTERS[status](r)),
    projects.filter((p) => inCampaign(p.profile) && PROJECT_FILTERS[status](p))
  );
}

/** Texten när listan är tom: hämtar, inga projekt alls, eller inga med filtret. */
function ListState({ loading, total, shown }: { loading: boolean; total: number | null; shown: number }) {
  if (loading) return <p className="text-sm text-muted-foreground">Hämtar projekten…</p>;
  if (total === 0) return <NoProjects />;
  if (total && shown === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
        Inga projekt med det filtret.
      </p>
    );
  }
  return null;
}

export function ProjectsClient() {
  const { runs, projects, profiles, error, load } = useProjects();
  const [campaign, setCampaign] = useState("");
  const [status, setStatus] = useState<StatusFilter>("alla");
  const campaigns = useMemo(() => campaignChoices(runs ?? [], projects, profiles), [runs, projects, profiles]);
  const shown = projectCards(runs ?? [], projects, campaign, status);
  const total = projectEntries(runs ?? [], projects).length;

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
        ← Hem
      </Link>
      <h1 className="mt-2 text-3xl font-bold tracking-tight">Projekt</h1>
      <p className="mt-1.5 mb-6 text-muted-foreground">
        En stream per projekt, och ett projekt per kampanj för egna filer. Öppna ett projekt för att se förloppet eller granska klippen.
      </p>
      {error && <LoadError error={error} onRetry={load} />}
      {total > 0 && (
        <Filters status={status} onStatus={setStatus} campaigns={campaigns} campaign={campaign} onCampaign={setCampaign} />
      )}
      <ListState loading={runs === null && !error} total={runs === null ? null : total} shown={shown.length} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((entry) => (
          <EntryCard key={entry.key} entry={entry} />
        ))}
      </div>
    </main>
  );
}
