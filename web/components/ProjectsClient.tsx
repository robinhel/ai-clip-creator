"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ProjectCard, UploadsCard, groupTime, groupUploads, isActive, runTime } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignProfile, CampaignRunInfo } from "@/lib/types";

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

function runMatches(run: CampaignRunInfo, filter: StatusFilter): boolean {
  const toReview = (run.clip_counts.renderad ?? 0) + (run.clip_counts.underkand ?? 0);
  if (filter === "pagar") return isActive(run);
  if (filter === "granska") return toReview > 0;
  if (filter === "klara") return run.status === "done";
  if (filter === "misslyckade") return run.status === "failed";
  return true;
}

export function ProjectsClient() {
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [uploads, setUploads] = useState<CampaignClip[]>([]);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [campaign, setCampaign] = useState("");
  const [status, setStatus] = useState<StatusFilter>("alla");
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

  // Läs om var femte sekund medan något projekt pågår, som Hem gör.
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
  // Kampanjerna som har projekt eller egna videor, med streamerns namn som i resten av appen.
  const campaigns = useMemo(() => {
    const names = new Set([...(runs ?? []).map((r) => r.profile), ...groups.map((g) => g.profile)]);
    return [...names].sort().map((name) => ({ name, label: profiles.find((p) => p.name === name)?.streamer || name }));
  }, [runs, groups, profiles]);

  const shown = [
    ...(runs ?? [])
      .filter((r) => (!campaign || r.profile === campaign) && runMatches(r, status))
      .map((run) => ({ key: `run-${run.run_id}`, time: runTime(run), card: <ProjectCard run={run} /> })),
    ...groups
      .filter((g) => (!campaign || g.profile === campaign) && (status === "alla" || (status === "granska" && (g.counts.renderad ?? 0) + (g.counts.underkand ?? 0) > 0) || status === "klara"))
      .map((g) => ({ key: `upload-${g.profile}`, time: groupTime(g), card: <UploadsCard group={g} /> })),
  ].sort((a, b) => b.time - a.time);
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
      {error && (
        <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          <span className="flex-1">{error}</span>
          <button type="button" onClick={load} className="inline-flex h-8 items-center rounded-lg border border-[#4d1d1d] px-3 font-medium hover:bg-white/5">
            Försök igen
          </button>
        </div>
      )}
      {total > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 text-[13px]">
          <div role="tablist" aria-label="Visa projekt" className="flex flex-wrap overflow-hidden rounded-[9px] border border-line">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.id}
                role="tab"
                aria-selected={status === f.id}
                onClick={() => setStatus(f.id)}
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
                onChange={(e) => setCampaign(e.target.value)}
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
      )}
      {runs === null && !error && <p className="text-sm text-muted-foreground">Hämtar projekten…</p>}
      {runs?.length === 0 && groups.length === 0 && (
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
      )}
      {total > 0 && shown.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga projekt med det filtret.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((p) => (
          <Fragment key={p.key}>{p.card}</Fragment>
        ))}
      </div>
    </main>
  );
}
