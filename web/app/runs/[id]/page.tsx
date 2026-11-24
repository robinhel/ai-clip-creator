"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Bell, Check, FolderOpen, Loader2, RotateCcw, Sparkles, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DeleteVod } from "@/components/DeleteVod";
import { ClipsSection } from "@/components/ProjectClient";
import { api, cancelRun } from "@/lib/api";
import { campaignLabel } from "@/lib/campaignName";
import { countClips, latestAttempt } from "@/lib/projects";
import { rejectedFix } from "@/lib/steps";
import { clock, cn, duration, serverTime, usd } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignRunDetail, Run, RunStep } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;
const day = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short" });

/** Nycklar läggs in under Inställningar. Texten kommer från servern, till exempel "Berget-nyckeln (BERGET_API_KEY) saknas. Lägg in den under Inställningar. Den behövs för transkriberingen." */
function KeyFix({ text }: { text?: string | null }) {
  if (!text || !/API_KEY|nyckel|key/i.test(text)) return null;
  return (
    <Link href="/settings" className="mt-1 inline-block text-sm font-medium text-foreground underline underline-offset-4">
      Add the key in Settings
    </Link>
  );
}

/** Alltid t:mm:ss, som "0:00:00". */
const hms = (s: number) => `${Math.floor(s / 3600)}:${clock(s % 3600).padStart(5, "0")}`;

function stepTime(step: RunStep, now: number): string {
  if (step.status === "waiting") return step.estimate_s ? `about ${duration(step.estimate_s)}` : "";
  const started = step.started_at ? serverTime(step.started_at) : null;
  if (!started) return "";
  if (step.status === "done") return step.finished_at ? duration((serverTime(step.finished_at) - started) / 1000) : "";
  if (step.status !== "running") return "";
  const elapsed = duration((now - started) / 1000);
  return step.estimate_s ? `${elapsed} of about ${duration(step.estimate_s)}` : elapsed;
}

/** Tunn stapel när steget säger "Del N av M", som transkriberingen gör per del till Berget. */
function PartBar({ message }: { message?: string | null }) {
  const m = message?.match(/^(?:Part|Del) (\d+) (?:of|av) (\d+)$/);
  if (!m || Number(m[2]) === 0) return null;
  // Meddelandet kommer när del N börjar, så N - 1 delar är klara.
  const pct = Math.min(100, ((Number(m[1]) - 1) / Number(m[2])) * 100);
  return (
    <div
      role="progressbar"
      aria-label="Parts done"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="mt-1.5 h-1 w-48 overflow-hidden rounded-full bg-line"
    >
      <div className="h-full rounded-full bg-brand transition-[width]" style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Notisens läge i webbläsaren, eller null om webbläsaren saknar notiser. */
function notifyPermission(): NotificationPermission | null {
  return typeof window !== "undefined" && "Notification" in window ? Notification.permission : null;
}

function StepIcon({ step, number }: { step: RunStep; number: number }) {
  return (
    <span
      className={cn(
        "grid size-[26px] shrink-0 place-items-center rounded-full bg-line text-[13px] font-bold text-muted-foreground",
        step.status === "done" && "bg-good text-[#04220f]",
        step.status === "running" && "bg-brand text-white shadow-[0_0_0_5px_rgba(124,92,255,.25)]",
        step.status === "failed" && "bg-bad text-white"
      )}
      aria-hidden
    >
      {step.status === "done" ? <Check className="size-3.5" /> : step.status === "failed" ? <X className="size-3.5" /> : number}
    </span>
  );
}

function Steps({ steps, cancelled }: { steps: RunStep[]; cancelled?: boolean }) {
  const now = Date.now();
  const stateText = { waiting: "Waiting", running: "Running", done: "Done", failed: cancelled ? "Cancelled here" : "Failed" };
  return (
    <ol className="max-w-[640px]">
      {steps.map((step, i) => (
        <li key={step.id} className="flex items-start gap-3.5 border-b border-line py-3.5">
          <StepIcon step={step} number={i + 1} />
          <div className="min-w-0">
            <p className={cn("font-semibold", step.status === "waiting" && "text-muted-foreground")}>{step.label}</p>
            <p className="text-[13px] text-muted-foreground">
              {stateText[step.status]}
              {step.status === "running" && step.message ? `: ${step.message}` : ""}
            </p>
            {step.status === "running" && <PartBar message={step.message} />}
          </div>
          <span className="ml-auto text-[13px] whitespace-nowrap text-muted-foreground tabular-nums">{stepTime(step, now)}</span>
        </li>
      ))}
    </ol>
  );
}

/** "SkiiMaskTimmy, Bodycam, 17 nov., 4 min" */
function runMeta(run: CampaignRunDetail): string {
  return [
    run.channel_name ?? run.profile,
    run.game,
    run.created ? day.format(serverTime(run.created)) : null,
    run.duration_s ? duration(run.duration_s) : null,
  ]
    .filter(Boolean)
    .join(", ");
}

/** Delen av VOD:en, som "0:10:00–1:00:00", eller null för hela. end_s 0 betyder till slutet av VOD:en. */
function runPart(run: CampaignRunDetail): string | null {
  const start = run.start_s ?? 0;
  const end = run.end_s ?? 0;
  if (!start && !end) return null;
  const last = end || run.duration_s;
  return `${hms(start)}–${last ? hms(last) : "the end"}`;
}

/** "Kostar cirka 0,05 US$ (Berget 0,01 US$, Claude 0,04 US$). " I snabbläget bara Berget. */
function costText(run: CampaignRunDetail): string {
  const cost = run.cost_estimate;
  if (run.fast && cost) return `Costs about ${usd.format(cost.berget_usd)} at Berget. The signals choose, without Claude. `;
  if (run.cost_estimate_usd == null) return "";
  const parts = cost ? ` (Berget ${usd.format(cost.berget_usd)}, Claude ${usd.format(cost.claude_usd)})` : "";
  return `Costs about ${usd.format(run.cost_estimate_usd)}${parts}. `;
}

function RunHeader({ run, clips }: { run: CampaignRunDetail; clips: CampaignClip[] | null }) {
  const part = runPart(run);
  return (
    <div className="min-w-0 flex-[1_1_20rem]">
      <h1 className="text-3xl font-bold tracking-tight break-words">
        {run.title || run.vod_id}
        {clips && clips.length > 0 && <span className="font-normal text-muted-foreground">, {clips.length} klipp</span>}
      </h1>
      <p className="mt-1.5 text-muted-foreground">{runMeta(run)}</p>
      <p className="text-sm text-muted-foreground">
        Campaign: {campaignLabel(run.profile)}, VOD {run.vod_id}
      </p>
      {part && <p className="text-sm text-muted-foreground tabular-nums">Del av VOD:en: {part}</p>}
      {run.focus && <p className="mt-1 text-sm text-muted-foreground">Letade efter: {run.focus}</p>}
    </div>
  );
}

function SupersededNote({ latestRunId }: { latestRunId: number | null }) {
  if (latestRunId == null) return null;
  return (
    <p role="status" className="mt-3 mb-5 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
      This is an older run. The project has been run again.{" "}
      <Link href={`/runs/${latestRunId}`} className="font-medium text-foreground underline underline-offset-4">
        Open the latest run
      </Link>
      .
    </p>
  );
}

/** En egen fil hör till kampanjens projekt för egna filer. Länken dit, med projektets titel när den går att hämta. */
function ProjectLink({ projectId }: { projectId: number }) {
  const [title, setTitle] = useState<string | null>(null);
  useEffect(() => {
    api.campaign.projects
      .get(projectId)
      .then((p) => setTitle(p.title))
      .catch(() => setTitle(null));
  }, [projectId]);
  return (
    <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
      <FolderOpen className="size-4 shrink-0" aria-hidden />
      Part of the project:{" "}
      <Link href={`/projects/${projectId}`} className="font-medium text-foreground underline underline-offset-4">
        {title ?? `project ${projectId}`}
      </Link>
    </p>
  );
}

type CancelStep = "idle" | "ask" | "sent";

/** Avbryt: först en fråga, sedan väntar sidan på att servern stoppar vid nästa steg. */
function CancelBar({ onCancel }: { onCancel: () => Promise<string | null> }) {
  const [step, setStep] = useState<CancelStep>("idle");
  const [error, setError] = useState<string | null>(null);
  async function cancel() {
    setError(null);
    setStep("sent");
    const problem = await onCancel();
    if (problem) {
      setError(problem);
      setStep("idle");
    }
  }
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
      {step === "idle" && (
        <Button variant="outline" className="h-8" onClick={() => setStep("ask")}>
          <Square aria-hidden />
          Cancel
        </Button>
      )}
      {step === "ask" && (
        <>
          <span>Cancel? Clips that are already done are kept. A partial download is deleted.</span>
          <Button variant="destructive" className="h-8" onClick={cancel}>
            Yes, cancel
          </Button>
          <Button variant="outline" className="h-8" onClick={() => setStep("idle")}>
            No, continue
          </Button>
        </>
      )}
      {step === "sent" && (
        <span role="status" className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Cancelling. It stops at the next step, before the next call to Claude.
        </span>
      )}
      {error && <span className="text-[#fca5a5]">{error}</span>}
    </div>
  );
}

/** Knappen för en notis, eller vad webbläsaren redan har svarat. */
function NotifyLine() {
  const [permission, setPermission] = useState<NotificationPermission | null>(null);
  useEffect(() => setPermission(notifyPermission()), []);
  if (permission === "default") {
    return (
      <Button variant="outline" className="mb-3 h-8" onClick={async () => setPermission(await Notification.requestPermission())}>
        <Bell aria-hidden />
        Get a notification when the project is done
      </Button>
    );
  }
  if (permission === "granted") {
    return (
      <p className="mb-3 flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <Bell className="size-3.5" aria-hidden />
        You will get a notification when the project is done, if the page is open.
      </p>
    );
  }
  if (permission === "denied") return <p className="mb-3 text-[13px] text-muted-foreground">Notifications are turned off for the app in the browser.</p>;
  return null;
}

/** Tid kvar, kostnad, Avbryt och notis medan projektet pågår. */
function ActiveInfo({ run, onCancel }: { run: CampaignRunDetail; onCancel: () => Promise<string | null> }) {
  return (
    <>
      <p className="mb-2 max-w-[640px] text-sm text-muted-foreground">
        {run.eta_s != null ? `About ${duration(run.eta_s)} left. ` : ""}
        {costText(run)}
        You can close the page. The clips show up below when they are done.
      </p>
      {run.cancellable && <CancelBar onCancel={onCancel} />}
      <NotifyLine />
    </>
  );
}

interface RetryProps {
  retrying: boolean;
  retryError: string | null;
  onRetry: () => void;
}

function RetryButton({ run, retrying, retryError, onRetry, keyText }: RetryProps & { run: CampaignRunDetail; keyText: string | null }) {
  return (
    <>
      {/* Länken till Inställningar bredvid knappen, med mellanrum, och under den på en smal skärm. */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <Button className="h-9" onClick={onRetry} disabled={retrying || !!run.retry_problem}>
          {retrying ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCcw aria-hidden />}
          {retrying ? "Starting…" : "Run again"}
        </Button>
        <KeyFix text={keyText} />
      </div>
      {retryError && <p className="mt-2 text-sm text-[#fca5a5]">{retryError}</p>}
    </>
  );
}

/** Vad Kör igen gör efter ett fel. */
function retryText(run: CampaignRunDetail, failedStep: string | null | undefined): string {
  if (run.retry_problem) return run.retry_problem;
  return failedStep === "transcribe" ? "Finished parts are saved. Run again continues where it stopped." : "Run again starts over with the same choices.";
}

/** Ett projekt som stoppade: avbrutet av Robin, misslyckat, eller ett äldre försök. */
function FailedBox({ run, superseded, ...retry }: RetryProps & { run: CampaignRunDetail; superseded: boolean }) {
  const failedStep = run.steps.find((s) => s.status === "failed")?.id ?? run.stage;
  if (superseded) {
    const why = run.cancelled ? "You cancelled the run." : `The run failed${run.error ? `: ${run.error.replace(/\.?$/, ".")}` : "."}`;
    return (
      <p className="mb-4 max-w-[640px] text-sm text-muted-foreground">
        {why} The project has been run again, so it does not need to run again.
      </p>
    );
  }
  if (run.cancelled) {
    return (
      <div role="status" className="mb-4 max-w-[640px] rounded-lg border border-line bg-panel p-4">
        <p className="font-medium">You cancelled</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Clips that were finished are still below. Run again starts over with the same choices
          {failedStep === "transcribe" ? ", and finished parts of the transcription are reused" : ""}.
        </p>
        <RetryButton run={run} {...retry} keyText={retry.retryError} />
      </div>
    );
  }
  return (
    <div role="alert" className="mb-4 max-w-[640px] rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4">
      <p className="font-medium text-[#fca5a5]">The project failed</p>
      {run.error && <p className="mt-1 font-mono text-[13px] break-words text-[#f2c4c4]">{run.error}</p>}
      <p className="mt-3 text-sm text-muted-foreground">{retryText(run, failedStep)}</p>
      <RetryButton run={run} {...retry} keyText={retry.retryError || run.error} />
    </div>
  );
}

/** Resultatet och felet för Gör klippen kampanjklara igen, och texten medan det pågår. */
function FinishNotes({ result, error, running }: { result: string | null; error: string | null; running: boolean }) {
  return (
    <>
      {result && (
        <p role="status" className="mt-4 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
          {result}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {running && (
        <p className="mt-4 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
          Overlay, text, caption and rule check run again. It takes a few minutes. The clips below update when it is done.
        </p>
      )}
    </>
  );
}

/** När projektet blir klart: fliktiteln visar resultatet, och en notis kommer om Robin har sagt ja till notiser. */
function useDoneNotice(run: CampaignRunDetail | null, isActive: boolean) {
  const wasActive = useRef(false);
  useEffect(() => {
    if (!run) return;
    if (isActive) {
      wasActive.current = true;
      return;
    }
    if (!wasActive.current || (run.status !== "done" && run.status !== "failed")) return;
    wasActive.current = false;
    const done = run.status === "done";
    const title = done ? "✓ Done" : "✗ Failed";
    const name = run.title || `VOD ${run.vod_id}`;
    const previous = document.title;
    document.title = title;
    if (notifyPermission() === "granted") {
      new Notification(title, { body: done ? `${name}: ${countClips(run.clip_counts)} ${countClips(run.clip_counts) === 1 ? "clip" : "clips"} to review.` : `${name}: the project stopped. Open the project to see why.` });
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
}

const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function PageMessage({ error }: { error: string | null }) {
  if (!error) {
    return (
      <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
        <p className="text-sm text-muted-foreground">Loading the project…</p>
      </main>
    );
  }
  return (
    <main className="w-full max-w-[1180px] space-y-4 px-6 py-9 md:px-11">
      <p role="alert" className="text-bad">
        {error}
      </p>
      <Link href="/projects" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
        To all projects
      </Link>
    </main>
  );
}

/** "3 klipp gjordes om. 1 klipp rördes inte …", räknat på vilka klipp som ändrades. För äldre servrar. */
function redoneText(clips: CampaignClip[], before: Map<number, string>): string {
  const redone = clips.filter((c) => before.has(c.id) && before.get(c.id) !== c.updated).length;
  const skipped = clips.filter((c) => before.has(c.id)).length - redone;
  const note = skipped ? ` ${skipped} ${skipped === 1 ? "clip was" : "clips were"} not touched, for example clips waiting for your yes, so that an edited caption is not overwritten.` : "";
  return `${redone} ${redone === 1 ? "clip was" : "clips were"} redone.${note}`;
}

/** Gör klippen kampanjklara igen: startar, ser när clipsmiths körning är klar och säger vilka klipp som gjordes om. */
function useFinishAgain(runId: number, run: CampaignRunDetail | null, clips: CampaignClip[] | null, isActive: boolean) {
  // clipsmiths körning, bara för att se när det är klart.
  const [legacyRun, setLegacyRun] = useState<Run | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  // Klippens updated före start, för att räkna vilka som gjordes om.
  const before = useRef<Map<number, string> | null>(null);
  // Klar när körningen har ändrats efter att vi startade och inte längre är aktiv.
  const running = startedAt !== null && (isActive || !legacyRun || serverTime(legacyRun.updated_at) < startedAt);

  async function start() {
    setError(null);
    setResult(null);
    before.current = new Map((clips ?? []).map((c) => [c.id, c.updated]));
    try {
      await api.campaign.runs.finish(runId);
      setLegacyRun(null);
      setStartedAt(Date.now() - 1000);
    } catch (err) {
      before.current = null;
      setError(errorText(err, "Could not make the clips campaign-ready again. Try again."));
    }
  }

  useEffect(() => {
    if (running || !before.current || !clips) return;
    const was = before.current;
    before.current = null;
    // Servern skriver hur många klipp som gjordes om och hoppades över, med orsak, i körningens message.
    setResult(run?.message && /gjordes om/.test(run.message) ? run.message : redoneText(clips, was));
  }, [running, clips, run?.message]);

  const poll = useCallback(async () => {
    if (startedAt !== null) await api.runs.get(runId).then(setLegacyRun).catch(() => {});
  }, [startedAt, runId]);

  return { running, error, result, start, poll };
}

/** Projektet och dess klipp, och om det finns ett senare försök i samma projekt (samma stream och kampanj, inte chatten). */
function useRun(runId: number) {
  const validId = Number.isInteger(runId) && runId > 0;
  const [run, setRun] = useState<CampaignRunDetail | null>(null);
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [latestRunId, setLatestRunId] = useState<number | null>(null);

  const fetchClips = useCallback(async () => {
    try {
      setClips(await api.campaign.clips.list({ run_id: runId }));
    } catch {
      // Felet för projektet visas redan; klippen hämtas igen vid nästa läsning.
    }
  }, [runId]);

  const fetchRun = useCallback(async () => {
    if (!validId) {
      setError("The project does not exist. Check the link.");
      return null;
    }
    try {
      const data = await api.campaign.runs.get(runId);
      setRun(data);
      setError(null);
      return data;
    } catch (err) {
      setError(errorText(err, "Could not load the project"));
      return null;
    }
  }, [runId, validId]);

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
      .then((list) => setLatestRunId(latestAttempt(list, run)))
      .catch(() => {});
  }, [run?.vod_id, run?.profile, run?.origin]); // eslint-disable-line react-hooks/exhaustive-deps

  return { run, clips, setClips, profiles, error, latestRunId, fetchRun, fetchClips };
}

/** Läser om var tredje sekund medan `on` gäller. Klippen läses om också, så att de syns när de blir klara. */
function usePolling(on: boolean, poll: () => Promise<unknown>) {
  // Räknas upp efter varje omläsning, så att nästa startar också när anropet misslyckades.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = setTimeout(async () => {
      await poll();
      setTick((t) => t + 1);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [on, tick, poll]);
}

function FinishButton({ running, disabled, onClick }: { running: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <Button
      variant="outline"
      className="h-9"
      onClick={onClick}
      disabled={disabled || running}
      title="Adds overlay and text, writes the caption and runs the rule check again"
    >
      {running ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
      {running ? "Making the clips ready…" : "Make clips campaign-ready again"}
    </Button>
  );
}

export default function RunDetailPage() {
  const params = useParams();
  const router = useRouter();
  const runId = Number(params.id);
  const { run, clips, setClips, profiles, error, latestRunId, fetchRun, fetchClips } = useRun(runId);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  const isActive = run?.status === "pending" || run?.status === "running";
  const finish = useFinishAgain(runId, run, clips, isActive);
  // Ersatt bara av ett försök med högre nummer. Listan kan sakna ett försök som just har startat.
  const superseded = run != null && latestRunId != null && latestRunId > run.run_id;

  const poll = useCallback(async () => {
    const [data] = await Promise.all([fetchRun(), finish.poll()]);
    if (data) await fetchClips();
  }, [fetchRun, fetchClips, finish.poll]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(isActive || finish.running, poll);
  useDoneNotice(run, isActive);

  async function handleRetry() {
    setRetrying(true);
    setRetryError(null);
    try {
      const { id } = await api.campaign.runs.retry(runId);
      router.push(`/runs/${id}`);
    } catch (err) {
      setRetryError(errorText(err, "Could not run again"));
      setRetrying(false);
    }
  }

  /** null när servern tog emot det. Sidan läser om tills projektet har stoppat. */
  async function handleCancel(): Promise<string | null> {
    try {
      await cancelRun(runId);
      await fetchRun();
      return null;
    } catch (err) {
      return errorText(err, "Could not cancel");
    }
  }

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
  }

  if (!run) return <PageMessage error={error} />;

  // Gör klippen kampanjklara igen gör bara om underkända klipp, med samma råklipp. Fel i captionen och längden
  // rättas inte av den: de rättas på raden, eller så trycker Robin Nej (rejectedFix).
  const canRedo = (clips ?? []).some((c) => rejectedFix(c)?.kind === "projekt");
  const formats = profiles.find((p) => p.name === run.profile)?.formats ?? [];

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <Link href="/projects" className="text-sm text-muted-foreground hover:text-foreground">
        ← All projects
      </Link>
      {run.project_id != null && <ProjectLink projectId={run.project_id} />}
      {superseded && <SupersededNote latestRunId={latestRunId} />}
      <div className="mt-2 flex flex-wrap items-start gap-4">
        {/* basis 20rem: på en smal skärm hamnar knappen under rubriken i stället för att trycka ihop den. */}
        <RunHeader run={run} clips={clips} />
        <div className="flex flex-wrap items-start gap-2">
          {/* Bara när knappen kan rätta något. Ett misslyckat projekt har inga klipp att göra om; servern svarar 400. */}
          {run.status !== "failed" && (canRedo || finish.running) && (
            <FinishButton running={finish.running} disabled={isActive} onClick={finish.start} />
          )}
          <DeleteVod run={run} onDeleted={fetchRun} />
        </div>
      </div>

      {error && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          Could not reload the project: {error}
          {isActive || finish.running ? " Trying again in a few seconds." : ""}
        </p>
      )}
      <FinishNotes result={finish.result} error={finish.error} running={finish.running && !isActive} />

      {(isActive || run.status === "failed") && (
        <section className="mt-7">
          {isActive && <ActiveInfo run={run} onCancel={handleCancel} />}
          {run.status === "failed" && (
            <FailedBox run={run} superseded={superseded} retrying={retrying} retryError={retryError} onRetry={handleRetry} />
          )}
          <Steps steps={run.steps} cancelled={run.cancelled} />
        </section>
      )}

      {clips && (clips.length > 0 || run.status === "done") && (
        <ClipsSection running={isActive} clips={clips} formats={formats} onUpdate={handleUpdate} onReload={fetchClips} />
      )}
    </main>
  );
}
