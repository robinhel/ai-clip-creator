"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProjectCard, UploadsCard, groupTime, groupUploads, isActive, runTime, type UploadGroup } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignProfile, CampaignRunInfo, CampaignStatus } from "@/lib/types";

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

const toReview = (counts: Partial<Record<CampaignStatus, number>>) => (counts.renderad ?? 0) + (counts.underkand ?? 0);

function runMatches(run: CampaignRunInfo, filter: StatusFilter): boolean {
  if (filter === "pagar") return isActive(run);
  if (filter === "granska") return toReview(run.clip_counts) > 0;
  if (filter === "klara") return run.status === "done";
  if (filter === "misslyckade") return run.status === "failed";
  return true;
}

/** Egna videor pågår aldrig och misslyckas inte som ett projekt. */
function groupMatches(group: UploadGroup, filter: StatusFilter): boolean {
  if (filter === "granska") return toReview(group.counts) > 0;
  return filter === "alla" || filter === "klara";
}

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

/** Projekten, egna videor och kampanjerna. Läser om projekten var femte sekund medan något pågår, som Hem gör. */
function useProjects() {
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [uploads, setUploads] = useState<CampaignClip[]>([]);
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
    api.campaign.clips.list({ vod_id: "upload" }).then(setUploads).catch(() => setUploads([]));
    api.campaign.profiles().then(setProfiles).catch(() => {});
  }

  useEffect(load, []);

  const anyActive = runs?.some(isActive) ?? false;
  useEffect(() => {
    if (!anyActive) return;
    const timer = setTimeout(() => {
      api.campaign.runs
        .list()
        .then(setRuns)
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [anyActive, pollTick]);

  const groups = useMemo(() => groupUploads(uploads, profiles), [uploads, profiles]);
  return { runs, groups, profiles, error, load };
}

/** Kampanjerna som har projekt eller egna videor, med streamerns namn som i resten av appen. */
function campaignChoices(runs: CampaignRunInfo[], groups: UploadGroup[], profiles: CampaignProfile[]): CampaignChoice[] {
  const names = new Set([...runs.map((r) => r.profile), ...groups.map((g) => g.profile)]);
  return [...names].sort().map((name) => ({ name, label: profiles.find((p) => p.name === name)?.streamer || name }));
}

/** Korten som passar filtren, senaste först. */
function projectCards(runs: CampaignRunInfo[], groups: UploadGroup[], campaign: string, status: StatusFilter) {
  const inCampaign = (profile: string) => !campaign || profile === campaign;
  return [
    ...runs
      .filter((r) => inCampaign(r.profile) && runMatches(r, status))
      .map((run) => ({ key: `run-${run.run_id}`, time: runTime(run), card: <ProjectCard run={run} /> })),
    ...groups
      .filter((g) => inCampaign(g.profile) && groupMatches(g, status))
      .map((g) => ({ key: `upload-${g.profile}`, time: groupTime(g), card: <UploadsCard group={g} /> })),
  ].sort((a, b) => b.time - a.time);
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
  const { runs, groups, profiles, error, load } = useProjects();
  const [campaign, setCampaign] = useState("");
  const [status, setStatus] = useState<StatusFilter>("alla");
  const campaigns = useMemo(() => campaignChoices(runs ?? [], groups, profiles), [runs, groups, profiles]);
  const shown = projectCards(runs ?? [], groups, campaign, status);
  const total = (runs?.length ?? 0) + groups.length;

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
        ← Hem
      </Link>
      <h1 className="mt-2 text-3xl font-bold tracking-tight">Projekt</h1>
      <p className="mt-1.5 mb-6 text-muted-foreground">
        En stream per projekt, och egna videor per kampanj. Öppna ett projekt för att se förloppet eller granska klippen.
      </p>
      {error && <LoadError error={error} onRetry={load} />}
      {total > 0 && (
        <Filters status={status} onStatus={setStatus} campaigns={campaigns} campaign={campaign} onCampaign={setCampaign} />
      )}
      <ListState loading={runs === null && !error} total={runs === null ? null : total} shown={shown.length} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((p) => (
          <Fragment key={p.key}>{p.card}</Fragment>
        ))}
      </div>
    </main>
  );
}
