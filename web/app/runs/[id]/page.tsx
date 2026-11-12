"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Check, Loader2, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow } from "@/components/ClipResultRow";
import { api } from "@/lib/api";
import { cn, duration, serverTime, usd } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignRunDetail, CampaignStatus, Run, RunStep } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;
const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });

const FILTERS: { id: string; label: string; status?: CampaignStatus }[] = [
  { id: "alla", label: "Alla" },
  { id: "granska", label: "Att granska", status: "renderad" },
  { id: "godkanda", label: "Godkända", status: "godkand" },
  { id: "nekade", label: "Nekade", status: "nekad" },
  { id: "underkanda", label: "Underkända", status: "underkand" },
];

type Sort = "score" | "time";

/** Betyg först, klipp utan betyg sist. Tidsordning efter start i VOD:en. */
function sortClips(clips: CampaignClip[], sort: Sort): CampaignClip[] {
  const key = (c: CampaignClip) => (sort === "score" ? -(c.score ?? -1) : (c.start_s ?? Infinity));
  return [...clips].sort((a, b) => key(a) - key(b) || a.id - b.id);
}

function stepTime(step: RunStep, now: number): string {
  const started = step.started_at ? serverTime(step.started_at) : null;
  if (step.status === "done" && started && step.finished_at) {
    return duration((serverTime(step.finished_at) - started) / 1000);
  }
  if (step.status === "running" && started) {
    const elapsed = (now - started) / 1000;
    return step.estimate_s ? `${duration(elapsed)} av cirka ${duration(step.estimate_s)}` : duration(elapsed);
  }
  if (step.status === "waiting" && step.estimate_s) return `cirka ${duration(step.estimate_s)}`;
  return "";
}

function Steps({ steps }: { steps: RunStep[] }) {
  const now = Date.now();
  return (
    <ol className="max-w-[640px]">
      {steps.map((step, i) => (
        <li key={step.id} className="flex items-start gap-3.5 border-b border-line py-3.5">
          <span
            className={cn(
              "grid size-[26px] shrink-0 place-items-center rounded-full bg-line text-[13px] font-bold text-muted-foreground",
              step.status === "done" && "bg-good text-[#04220f]",
              step.status === "running" && "bg-brand text-white shadow-[0_0_0_5px_rgba(124,92,255,.25)]",
              step.status === "failed" && "bg-bad text-white"
            )}
            aria-hidden
          >
            {step.status === "done" ? <Check className="size-3.5" /> : step.status === "failed" ? <X className="size-3.5" /> : i + 1}
          </span>
          <div className="min-w-0">
            <p className={cn("font-semibold", step.status === "waiting" && "text-muted-foreground")}>{step.label}</p>
            <p className="text-[13px] text-muted-foreground">
              {{ waiting: "Väntar", running: "Pågår", done: "Klart", failed: "Misslyckades" }[step.status]}
            </p>
          </div>
          <span className="ml-auto text-[13px] whitespace-nowrap text-muted-foreground tabular-nums">
            {stepTime(step, now)}
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function RunDetailPage() {
  const params = useParams();
  const runId = Number(params.id);

  const [run, setRun] = useState<CampaignRunDetail | null>(null);
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("alla");
  const [sort, setSort] = useState<Sort>("score");
  // clipsmiths körning, bara för att se när "Gör klippen kampanjklara igen" är klar.
  const [legacyRun, setLegacyRun] = useState<Run | null>(null);
  const [finishStartedAt, setFinishStartedAt] = useState<number | null>(null);
  const [finishError, setFinishError] = useState<string | null>(null);
  // Räknas upp efter varje omläsning, så att nästa startar också när anropet misslyckades.
  const [pollTick, setPollTick] = useState(0);

  const isActive = run?.status === "pending" || run?.status === "running";
  // Klar när körningen har ändrats efter att vi startade och inte längre är aktiv.
  const finishing =
    finishStartedAt !== null &&
    (isActive || !legacyRun || serverTime(legacyRun.updated_at) < finishStartedAt);

  const fetchClips = useCallback(async () => {
    try {
      setClips(await api.campaign.clips.list({ run_id: runId }));
    } catch {
      // Felet för körningen visas redan; klippen hämtas igen vid nästa läsning.
    }
  }, [runId]);

  const fetchRun = useCallback(async () => {
    try {
      const data = await api.campaign.runs.get(runId);
      setRun(data);
      setError(null);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta projektet");
      return null;
    }
  }, [runId]);

  useEffect(() => {
    fetchRun();
    fetchClips();
    api.campaign.profiles().then(setProfiles).catch(() => {});
  }, [fetchRun, fetchClips]);

  // Läs om var tredje sekund medan körningen pågår eller klippen görs klara igen.
  useEffect(() => {
    if (!isActive && !finishing) return;
    const timer = setTimeout(async () => {
      const [data] = await Promise.all([
        fetchRun(),
        finishStartedAt !== null ? api.runs.get(runId).then(setLegacyRun).catch(() => {}) : null,
      ]);
      if (data && data.status !== "pending" && data.status !== "running") await fetchClips();
      setPollTick((t) => t + 1);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [isActive, finishing, pollTick, finishStartedAt, runId, fetchRun, fetchClips]);

  async function handleFinish() {
    setFinishError(null);
    try {
      await api.campaign.runs.finish(runId);
      setLegacyRun(null);
      setFinishStartedAt(Date.now() - 1000);
    } catch (err) {
      setFinishError(err instanceof Error ? err.message : "Kunde inte starta");
    }
  }

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
  }

  const sorted = useMemo(() => sortClips(clips ?? [], sort), [clips, sort]);
  const active = FILTERS.find((f) => f.id === filter) ?? FILTERS[0];
  const visible = sorted.filter((c) => !active.status || c.status === active.status);
  const formats = profiles.find((p) => p.name === run?.profile)?.formats ?? [];

  if (error && !run) {
    return (
      <main className="w-full max-w-[1180px] space-y-4 px-6 py-9 md:px-11">
        <p role="alert" className="text-bad">
          {error}
        </p>
        <Link href="/projects" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
          Till alla projekt
        </Link>
      </main>
    );
  }

  if (!run) {
    return (
      <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
        <p className="text-sm text-muted-foreground">Hämtar projektet…</p>
      </main>
    );
  }

  const meta = [
    run.channel_name ?? run.profile,
    run.game,
    run.created ? day.format(serverTime(run.created)) : null,
    run.duration_s ? duration(run.duration_s) : null,
  ].filter(Boolean);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-3xl font-bold tracking-tight break-words">
            {run.title || run.vod_id}
            {clips && clips.length > 0 && (
              <span className="font-normal text-muted-foreground">, {clips.length} klipp</span>
            )}
          </h1>
          <p className="mt-1.5 text-muted-foreground">{meta.join(", ")}</p>
          <p className="text-sm text-muted-foreground">
            Kampanj: {run.profile}, VOD {run.vod_id}
          </p>
          {run.focus && <p className="mt-1 text-sm text-muted-foreground">Letade efter: {run.focus}</p>}
        </div>
        <Button
          variant="outline"
          className="h-9"
          onClick={handleFinish}
          disabled={isActive || finishing}
          title="Lägger på overlay och text, skriver caption och kör regelkontrollen igen"
        >
          {finishing ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
          {finishing ? "Gör klippen klara…" : "Gör klippen kampanjklara igen"}
        </Button>
      </div>

      {error && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Kunde inte läsa om projektet: {error}
          {isActive || finishing ? " Försöker igen om några sekunder." : ""}
        </p>
      )}
      {finishError && (
        <p role="alert" className="mt-4 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {finishError}
        </p>
      )}
      {finishing && !isActive && (
        <p className="mt-4 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
          Overlay, text, caption och regelkontroll körs om. Det tar några minuter. Klippen nedan uppdateras när det är klart.
        </p>
      )}

      {(isActive || run.status === "failed") && (
        <section className="mt-7">
          {isActive && (
            <p className="mb-2 text-sm text-muted-foreground">
              {run.eta_s != null ? `Cirka ${duration(run.eta_s)} kvar. ` : ""}
              {run.cost_estimate_usd != null ? `Kostar cirka ${usd.format(run.cost_estimate_usd)}. ` : ""}
              Du kan stänga sidan. Klippen dyker upp här när de är klara.
            </p>
          )}
          {run.status === "failed" && (
            <div role="alert" className="mb-4 max-w-[640px] rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4">
              <p className="font-medium text-[#fca5a5]">Körningen misslyckades</p>
              {run.error && <p className="mt-1 text-sm break-words text-muted-foreground">{run.error}</p>}
            </div>
          )}
          <Steps steps={run.steps} />
        </section>
      )}

      {!isActive && clips && (clips.length > 0 || run.status === "done") && (
        <section className="mt-8">
          {clips.length > 0 && <div className="mb-2 flex flex-wrap items-center gap-3 text-[13px]">
            <div role="tablist" aria-label="Visa klipp" className="flex overflow-hidden rounded-[9px] border border-line">
              {FILTERS.map((f) => {
                const count = f.status ? clips.filter((c) => c.status === f.status).length : clips.length;
                return (
                  <button
                    key={f.id}
                    role="tab"
                    aria-selected={filter === f.id}
                    onClick={() => setFilter(f.id)}
                    className="px-3 py-1.5 text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
                  >
                    {f.label} <span className="tabular-nums">{count}</span>
                  </button>
                );
              })}
            </div>
            <span className="flex-1" />
            <span className="text-muted-foreground">Sortera:</span>
            <div className="flex overflow-hidden rounded-[9px] border border-line">
              {(
                [
                  ["score", "Betyg"],
                  ["time", "Tidsordning"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  aria-pressed={sort === id}
                  onClick={() => setSort(id)}
                  className="px-3 py-1.5 text-muted-foreground hover:text-foreground aria-pressed:bg-panel aria-pressed:text-foreground"
                >
                  {label}
                </button>
              ))}
            </div>
          </div>}

          {visible.length === 0 ? (
            <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
              {clips.length === 0 ? "Inga klipp i det här projektet." : "Inga klipp med den statusen."}
            </p>
          ) : (
            visible.map((clip) => (
              <ClipResultRow
                // Ny nyckel när servern har sparat, så att fälten visar det sparade.
                key={`${clip.id}-${clip.updated}`}
                clip={clip}
                number={sorted.indexOf(clip) + 1}
                formats={formats}
                onUpdate={handleUpdate}
              />
            ))
          )}
        </section>
      )}
    </main>
  );
}
