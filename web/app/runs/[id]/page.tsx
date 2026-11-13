"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Bell, Check, Keyboard, Loader2, RotateCcw, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow, ignoreKey } from "@/components/ClipResultRow";
import { clipTotal } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import { clock, cn, duration, serverTime, usd } from "@/lib/utils";
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

/** Alltid t:mm:ss, som "0:00:00". */
const hms = (s: number) => `${Math.floor(s / 3600)}:${clock(s % 3600).padStart(5, "0")}`;

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

/** Tunn stapel när steget säger "Del N av M", som transkriberingen gör per del till Berget. */
function PartBar({ message }: { message?: string | null }) {
  const m = message?.match(/^Del (\d+) av (\d+)$/);
  if (!m || Number(m[2]) === 0) return null;
  // Meddelandet kommer när del N börjar, så N - 1 delar är klara.
  const pct = Math.min(100, ((Number(m[1]) - 1) / Number(m[2])) * 100);
  return (
    <div
      role="progressbar"
      aria-label="Delar klara"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="mt-1.5 h-1 w-48 overflow-hidden rounded-full bg-line"
    >
      <div className="h-full rounded-full bg-brand transition-[width]" style={{ width: `${pct}%` }} />
    </div>
  );
}

const KEYS: [string, string][] = [
  ["J", "nästa klipp"],
  ["K", "föregående klipp"],
  ["Y", "ja"],
  ["N", "nej"],
  ["E", "redigera"],
  ["C", "kopiera caption"],
];

/** Notisens läge i webbläsaren, eller null om webbläsaren saknar notiser. */
function notifyPermission(): NotificationPermission | null {
  return typeof window !== "undefined" && "Notification" in window ? Notification.permission : null;
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
              {step.status === "running" && step.message ? `: ${step.message}` : ""}
            </p>
            {step.status === "running" && <PartBar message={step.message} />}
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
  const router = useRouter();
  const runId = Number(params.id);
  const validId = Number.isInteger(runId) && runId > 0;

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
  const [activeId, setActiveId] = useState<number | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | null>(null);
  const wasActive = useRef(false);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
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
    if (!validId) {
      setError("Projektet finns inte. Kontrollera länken.");
      return null;
    }
    try {
      const data = await api.campaign.runs.get(runId);
      setRun(data);
      setError(null);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta projektet");
      return null;
    }
  }, [runId, validId]);

  // Den senaste körningen i samma projekt (samma stream och kampanj). Är det inte den här är körningen ersatt.
  const [latestRunId, setLatestRunId] = useState<number | null>(null);
  useEffect(() => {
    fetchRun();
    if (!validId) return;
    fetchClips();
    api.campaign.profiles().then(setProfiles).catch(() => {});
  }, [fetchRun, fetchClips, validId]);
  useEffect(() => {
    if (!run) return;
    api.campaign.runs
      .list()
      .then((list) => setLatestRunId(list.find((r) => r.vod_id === run.vod_id && r.profile === run.profile)?.run_id ?? null))
      .catch(() => {});
  }, [run?.vod_id, run?.profile]); // eslint-disable-line react-hooks/exhaustive-deps
  // Ersatt bara av en körning med högre nummer. Listan kan sakna en körning som just har startat.
  const superseded = run != null && latestRunId != null && latestRunId > run.run_id;

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

  // Fliken i adressen (?filter=granska), så att andra sidor kan länka till rätt flik.
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

  // Klippens updated före "Gör klippen kampanjklara igen", för att räkna vilka som gjordes om.
  const beforeFinish = useRef<Map<number, string> | null>(null);
  const [finishResult, setFinishResult] = useState<string | null>(null);
  async function handleFinish() {
    setFinishError(null);
    setFinishResult(null);
    beforeFinish.current = new Map((clips ?? []).map((c) => [c.id, c.updated]));
    try {
      await api.campaign.runs.finish(runId);
      setLegacyRun(null);
      setFinishStartedAt(Date.now() - 1000);
    } catch (err) {
      beforeFinish.current = null;
      setFinishError(err instanceof Error ? err.message : "Kunde inte starta");
    }
  }
  useEffect(() => {
    if (finishing || !beforeFinish.current || !clips) return;
    const before = beforeFinish.current;
    beforeFinish.current = null;
    // Servern skriver hur många klipp som gjordes om och hoppades över, med orsak, i körningens message.
    if (run?.message && /gjordes om/.test(run.message)) return setFinishResult(run.message);
    // Äldre server: räkna själv på vilka klipp som ändrades.
    const redone = clips.filter((c) => before.has(c.id) && before.get(c.id) !== c.updated).length;
    const skipped = clips.filter((c) => before.has(c.id)).length - redone;
    setFinishResult(
      `${redone} klipp gjordes om.` +
        (skipped ? ` ${skipped} klipp rördes inte, till exempel klipp som väntar på ditt ja, så att en ändrad caption inte skrivs över.` : "")
    );
  }, [finishing, clips, run?.message]);

  useEffect(() => setPermission(notifyPermission()), []);
  useEffect(() => {
    if (!run) return;
    if (isActive) {
      wasActive.current = true;
      return;
    }
    if (!wasActive.current || (run.status !== "done" && run.status !== "failed")) return;
    wasActive.current = false;
    const done = run.status === "done";
    const title = done ? "✓ Klar" : "✗ Misslyckades";
    const name = run.title || `VOD ${run.vod_id}`;
    const previous = document.title;
    document.title = title;
    if (notifyPermission() === "granted") {
      new Notification(title, { body: done ? `${name}: ${clipTotal(run)} klipp att granska.` : `${name}: körningen stoppade. Öppna projektet för att se felet.` });
    }
    // Fliktiteln visar resultatet tills du tittar på sidan igen.
    function restore() {
      if (document.visibilityState === "visible") document.title = previous;
    }
    const timer = setTimeout(restore, 10000);
    document.addEventListener("visibilitychange", restore);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", restore);
      document.title = previous;
    };
  }, [run, isActive]);

  async function askPermission() {
    setPermission(await Notification.requestPermission());
  }

  async function handleRetry() {
    setRetrying(true);
    setRetryError(null);
    try {
      const { id } = await api.campaign.runs.retry(runId);
      router.push(`/runs/${id}`);
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : "Kunde inte starta körningen igen");
      setRetrying(false);
    }
  }

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
  }

  const sorted = useMemo(() => sortClips(clips ?? [], sort), [clips, sort]);
  const active = FILTERS.find((f) => f.id === filter) ?? FILTERS[0];
  const visible = sorted.filter((c) => !active.status || c.status === active.status);
  const formats = profiles.find((p) => p.name === run?.profile)?.formats ?? [];

  // J och K flyttar mellan klippen i listan som den visas, rullar dit och markerar raden.
  const order = useRef<number[]>([]);
  order.current = visible.map((c) => c.id);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const key = e.key.toLowerCase();
      if ((key !== "j" && key !== "k") || ignoreKey(e) || order.current.length === 0) return;
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
  }, []);

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
  const start = run.start_s ?? 0;
  const end = run.end_s ?? 0;
  // end_s 0 betyder till slutet av VOD:en.
  const part = start || end ? `${hms(start)}–${end ? hms(end) : run.duration_s ? hms(run.duration_s) : "slutet"}` : null;
  const failedStep = run.steps.find((s) => s.status === "failed")?.id ?? run.stage;

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      {superseded && (
        <p role="status" className="mb-5 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
          Det här är en äldre körning. Projektet har en nyare körning.{" "}
          <Link href={`/runs/${latestRunId}`} className="font-medium text-foreground underline underline-offset-4">
            Öppna den senaste körningen
          </Link>
          .
        </p>
      )}
      <div className="flex flex-wrap items-start gap-4">
        {/* basis 20rem: på en smal skärm hamnar knappen under rubriken i stället för att trycka ihop den. */}
        <div className="min-w-0 flex-[1_1_20rem]">
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
          {part && <p className="text-sm text-muted-foreground tabular-nums">Del av VOD:en: {part}</p>}
          {run.focus && <p className="mt-1 text-sm text-muted-foreground">Letade efter: {run.focus}</p>}
        </div>
        {/* En misslyckad körning har inga klipp att göra om; servern svarar 400. */}
        {run.status !== "failed" && (
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
        )}
      </div>

      {error && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Kunde inte läsa om projektet: {error}
          {isActive || finishing ? " Försöker igen om några sekunder." : ""}
        </p>
      )}
      {finishResult && (
        <p role="status" className="mt-4 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
          {finishResult}
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
              {run.cost_estimate_usd != null
                ? `Kostar cirka ${usd.format(run.cost_estimate_usd)}${
                    run.cost_estimate
                      ? ` (Berget ${usd.format(run.cost_estimate.berget_usd)}, Claude ${usd.format(run.cost_estimate.claude_usd)})`
                      : ""
                  }. `
                : ""}
              Du kan stänga sidan. Klippen dyker upp här när de är klara.
            </p>
          )}
          {isActive && permission === "default" && (
            <Button variant="outline" className="mb-3 h-8" onClick={askPermission}>
              <Bell aria-hidden />
              Få en notis när körningen är klar
            </Button>
          )}
          {isActive && permission === "granted" && (
            <p className="mb-3 flex items-center gap-1.5 text-[13px] text-muted-foreground">
              <Bell className="size-3.5" aria-hidden />
              Du får en notis när körningen är klar, om sidan är öppen.
            </p>
          )}
          {isActive && permission === "denied" && (
            <p className="mb-3 text-[13px] text-muted-foreground">Notiser är avstängda för appen i webbläsaren.</p>
          )}
          {run.status === "failed" && superseded && (
            <p className="mb-4 max-w-[640px] text-sm text-muted-foreground">
              Körningen misslyckades{run.error ? `: ${run.error.replace(/\.?$/, ".")}` : "."} En nyare körning finns, så den behöver inte köras igen.
            </p>
          )}
          {run.status === "failed" && !superseded && (
            <div role="alert" className="mb-4 max-w-[640px] rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4">
              <p className="font-medium text-[#fca5a5]">Körningen misslyckades</p>
              {run.error && <p className="mt-1 font-mono text-[13px] break-words text-[#f2c4c4]">{run.error}</p>}
              <p className="mt-3 text-sm text-muted-foreground">
                {failedStep === "transcribe"
                  ? "Klara delar är sparade. Kör igen fortsätter där körningen stoppade."
                  : "Kör igen startar om körningen med samma val."}
              </p>
              <Button className="mt-3 h-9" onClick={handleRetry} disabled={retrying}>
                {retrying ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCcw aria-hidden />}
                {retrying ? "Startar…" : "Kör igen"}
              </Button>
              {retryError && <p className="mt-2 text-sm text-[#fca5a5]">{retryError}</p>}
            </div>
          )}
          <Steps steps={run.steps} />
        </section>
      )}

      {!isActive && clips && (clips.length > 0 || run.status === "done") && (
        <section className="mt-8">
          {clips.length > 0 && <div className="mb-2 flex flex-wrap items-center gap-3 text-[13px]">
            <div role="tablist" aria-label="Visa klipp" className="flex max-w-full overflow-x-auto rounded-[9px] border border-line">
              {FILTERS.map((f) => {
                const count = f.status ? clips.filter((c) => c.status === f.status).length : clips.length;
                return (
                  <button
                    key={f.id}
                    role="tab"
                    aria-selected={filter === f.id}
                    onClick={() => chooseFilter(f.id)}
                    className="px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
                  >
                    {f.label} <span className="tabular-nums">{count}</span>
                  </button>
                );
              })}
            </div>
            <span className="flex-1" />
            <details className="relative">
              <summary className="flex cursor-pointer items-center gap-1.5 rounded-[9px] border border-line px-3 py-1.5 text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
                <Keyboard className="size-3.5" aria-hidden />
                Tangenter
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
                <p className="col-span-2 mt-1 text-xs text-muted-foreground">Inte när du skriver i ett fält.</p>
              </dl>
            </details>
            <span className="flex items-center gap-3">
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
            </span>
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
                active={clip.id === activeId}
                onUpdate={handleUpdate}
              />
            ))
          )}
        </section>
      )}
    </main>
  );
}
