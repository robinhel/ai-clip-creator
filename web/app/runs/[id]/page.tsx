"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Bell, Check, Keyboard, Loader2, RotateCcw, Sparkles, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow, ignoreKey } from "@/components/ClipResultRow";
import { DeleteVod } from "@/components/DeleteVod";
import { clipTotal } from "@/components/ProjectCard";
import { api, cancelRun } from "@/lib/api";
import { rejectedFix } from "@/lib/steps";
import { clock, cn, duration, serverTime, usd } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignRunDetail, CampaignStatus, Run, RunStep } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;
const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });

const FILTERS: { id: string; label: string; statuses?: CampaignStatus[] }[] = [
  { id: "alla", label: "Alla" },
  { id: "granska", label: "Att granska", statuses: ["renderad"] },
  { id: "godkanda", label: "Godkända", statuses: ["godkand"] },
  // Allt efter Markera som postad, så att ett postat klipp inte försvinner ur flikarna.
  { id: "postade", label: "Postade", statuses: ["postad", "inskickad", "analytics_inskickad", "betald"] },
  { id: "nekade", label: "Nekade", statuses: ["nekad"] },
  { id: "underkanda", label: "Underkända", statuses: ["underkand"] },
];

/** Nycklar läggs in under Inställningar. Texten kommer från servern, till exempel "Berget-nyckeln (BERGET_API_KEY) saknas. Lägg in den under Inställningar. Den behövs för transkriberingen." */
function KeyFix({ text }: { text?: string | null }) {
  if (!text || !/API_KEY|nyckel/i.test(text)) return null;
  return (
    <Link href="/settings" className="mt-1 inline-block text-sm font-medium text-foreground underline underline-offset-4">
      Lägg in nyckeln i Inställningar
    </Link>
  );
}

type Sort = "score" | "time";

/** Alltid t:mm:ss, som "0:00:00". */
const hms = (s: number) => `${Math.floor(s / 3600)}:${clock(s % 3600).padStart(5, "0")}`;

/** Betyg först, klipp utan betyg sist. Tidsordning efter start i VOD:en. */
function sortClips(clips: CampaignClip[], sort: Sort): CampaignClip[] {
  const key = (c: CampaignClip) => (sort === "score" ? -(c.score ?? -1) : (c.start_s ?? Infinity));
  return [...clips].sort((a, b) => key(a) - key(b) || a.id - b.id);
}

function stepTime(step: RunStep, now: number): string {
  if (step.status === "waiting") return step.estimate_s ? `cirka ${duration(step.estimate_s)}` : "";
  const started = step.started_at ? serverTime(step.started_at) : null;
  if (!started) return "";
  if (step.status === "done") return step.finished_at ? duration((serverTime(step.finished_at) - started) / 1000) : "";
  if (step.status !== "running") return "";
  const elapsed = duration((now - started) / 1000);
  return step.estimate_s ? `${elapsed} av cirka ${duration(step.estimate_s)}` : elapsed;
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
  const stateText = { waiting: "Väntar", running: "Pågår", done: "Klart", failed: cancelled ? "Avbröts här" : "Misslyckades" };
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
  return `${hms(start)}–${last ? hms(last) : "slutet"}`;
}

/** "Kostar cirka 0,05 US$ (Berget 0,01 US$, Claude 0,04 US$). " I snabbläget bara Berget. */
function costText(run: CampaignRunDetail): string {
  const cost = run.cost_estimate;
  if (run.fast && cost) return `Kostar cirka ${usd.format(cost.berget_usd)} hos Berget. Signalerna väljer, utan Claude. `;
  if (run.cost_estimate_usd == null) return "";
  const parts = cost ? ` (Berget ${usd.format(cost.berget_usd)}, Claude ${usd.format(cost.claude_usd)})` : "";
  return `Kostar cirka ${usd.format(run.cost_estimate_usd)}${parts}. `;
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
        Kampanj: {run.profile}, VOD {run.vod_id}
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
      Det här är ett äldre försök. Projektet har körts igen.{" "}
      <Link href={`/runs/${latestRunId}`} className="font-medium text-foreground underline underline-offset-4">
        Öppna det senaste försöket
      </Link>
      .
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
          Avbryt
        </Button>
      )}
      {step === "ask" && (
        <>
          <span>Avbryta? Klipp som redan är klara sparas. En halv nedladdning tas bort.</span>
          <Button variant="destructive" className="h-8" onClick={cancel}>
            Ja, avbryt
          </Button>
          <Button variant="outline" className="h-8" onClick={() => setStep("idle")}>
            Nej, fortsätt
          </Button>
        </>
      )}
      {step === "sent" && (
        <span role="status" className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Avbryter. Det stoppar vid nästa steg, före nästa anrop till Claude.
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
        Få en notis när projektet är klart
      </Button>
    );
  }
  if (permission === "granted") {
    return (
      <p className="mb-3 flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <Bell className="size-3.5" aria-hidden />
        Du får en notis när projektet är klart, om sidan är öppen.
      </p>
    );
  }
  if (permission === "denied") return <p className="mb-3 text-[13px] text-muted-foreground">Notiser är avstängda för appen i webbläsaren.</p>;
  return null;
}

/** Tid kvar, kostnad, Avbryt och notis medan projektet pågår. */
function ActiveInfo({ run, onCancel }: { run: CampaignRunDetail; onCancel: () => Promise<string | null> }) {
  return (
    <>
      <p className="mb-2 max-w-[640px] text-sm text-muted-foreground">
        {run.eta_s != null ? `Cirka ${duration(run.eta_s)} kvar. ` : ""}
        {costText(run)}
        Du kan stänga sidan. Klippen dyker upp nedanför när de är klara.
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
          {retrying ? "Startar…" : "Kör igen"}
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
  return failedStep === "transcribe" ? "Klara delar är sparade. Kör igen fortsätter där det stoppade." : "Kör igen startar om med samma val.";
}

/** Ett projekt som stoppade: avbrutet av Robin, misslyckat, eller ett äldre försök. */
function FailedBox({ run, superseded, ...retry }: RetryProps & { run: CampaignRunDetail; superseded: boolean }) {
  const failedStep = run.steps.find((s) => s.status === "failed")?.id ?? run.stage;
  if (superseded) {
    const why = run.cancelled ? "Du avbröt försöket." : `Försöket misslyckades${run.error ? `: ${run.error.replace(/\.?$/, ".")}` : "."}`;
    return (
      <p className="mb-4 max-w-[640px] text-sm text-muted-foreground">
        {why} Projektet har körts igen, så det behöver inte köras igen.
      </p>
    );
  }
  if (run.cancelled) {
    return (
      <div role="status" className="mb-4 max-w-[640px] rounded-lg border border-line bg-panel p-4">
        <p className="font-medium">Du avbröt</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Klipp som hann bli klara finns kvar nedanför. Kör igen startar om med samma val
          {failedStep === "transcribe" ? ", och klara delar av transkriberingen används igen" : ""}.
        </p>
        <RetryButton run={run} {...retry} keyText={retry.retryError} />
      </div>
    );
  }
  return (
    <div role="alert" className="mb-4 max-w-[640px] rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4">
      <p className="font-medium text-[#fca5a5]">Projektet misslyckades</p>
      {run.error && <p className="mt-1 font-mono text-[13px] break-words text-[#f2c4c4]">{run.error}</p>}
      <p className="mt-3 text-sm text-muted-foreground">{retryText(run, failedStep)}</p>
      <RetryButton run={run} {...retry} keyText={retry.retryError || run.error} />
    </div>
  );
}

function KeysHelp() {
  return (
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
      <div role="tablist" aria-label="Visa klipp" className="flex max-w-full flex-wrap overflow-hidden rounded-[9px] border border-line">
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
          Overlay, text, caption och regelkontroll körs om. Det tar några minuter. Klippen nedan uppdateras när det är klart.
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
    const title = done ? "✓ Klar" : "✗ Misslyckades";
    const name = run.title || `VOD ${run.vod_id}`;
    const previous = document.title;
    document.title = title;
    if (notifyPermission() === "granted") {
      new Notification(title, { body: done ? `${name}: ${clipTotal(run)} klipp att granska.` : `${name}: projektet stoppade. Öppna projektet för att se varför.` });
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

/** J och K flyttar mellan klippen i listan som den visas, rullar dit och markerar raden. */
function useClipKeys(ids: number[], setActiveId: (update: (current: number | null) => number | null) => void) {
  const order = useRef<number[]>([]);
  order.current = ids;
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

const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function PageMessage({ error }: { error: string | null }) {
  if (!error) {
    return (
      <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
        <p className="text-sm text-muted-foreground">Hämtar projektet…</p>
      </main>
    );
  }
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

/** Klippen. De syns medan projektet pågår: de kommer ett i taget i sista steget. */
function ClipsSection({
  run,
  clips,
  formats,
  onUpdate,
  onReload,
}: {
  run: CampaignRunDetail;
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
  const isActive = run.status === "pending" || run.status === "running";
  return (
    <section className="mt-8">
      {isActive && (
        <p className="mb-3 text-sm text-muted-foreground">
          {clips.length === 1 ? "1 klipp är klart." : `${clips.length} klipp är klara.`} Fler kan komma medan projektet pågår.
        </p>
      )}
      {clips.length > 0 && <ClipToolbar clips={clips} filter={filter} onFilter={chooseFilter} sort={sort} onSort={setSort} />}
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
            onUpdate={onUpdate}
            onReload={onReload}
          />
        ))
      )}
    </section>
  );
}

/** "3 klipp gjordes om. 1 klipp rördes inte …", räknat på vilka klipp som ändrades. För äldre servrar. */
function redoneText(clips: CampaignClip[], before: Map<number, string>): string {
  const redone = clips.filter((c) => before.has(c.id) && before.get(c.id) !== c.updated).length;
  const skipped = clips.filter((c) => before.has(c.id)).length - redone;
  const note = skipped ? ` ${skipped} klipp rördes inte, till exempel klipp som väntar på ditt ja, så att en ändrad caption inte skrivs över.` : "";
  return `${redone} klipp gjordes om.${note}`;
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
      setError(errorText(err, "Kunde inte göra klippen kampanjklara igen. Försök igen."));
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

/** Projektet och dess klipp, och om det finns ett senare försök i samma projekt (samma stream och kampanj). */
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
      setError("Projektet finns inte. Kontrollera länken.");
      return null;
    }
    try {
      const data = await api.campaign.runs.get(runId);
      setRun(data);
      setError(null);
      return data;
    } catch (err) {
      setError(errorText(err, "Kunde inte hämta projektet"));
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
      .then((list) => setLatestRunId(list.find((r) => r.vod_id === run.vod_id && r.profile === run.profile)?.run_id ?? null))
      .catch(() => {});
  }, [run?.vod_id, run?.profile]); // eslint-disable-line react-hooks/exhaustive-deps

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
      title="Lägger på overlay och text, skriver caption och kör regelkontrollen igen"
    >
      {running ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
      {running ? "Gör klippen klara…" : "Gör klippen kampanjklara igen"}
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
      setRetryError(errorText(err, "Kunde inte köra igen"));
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
      return errorText(err, "Kunde inte avbryta");
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
        ← Alla projekt
      </Link>
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
          Kunde inte läsa om projektet: {error}
          {isActive || finish.running ? " Försöker igen om några sekunder." : ""}
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
        <ClipsSection run={run} clips={clips} formats={formats} onUpdate={handleUpdate} onReload={fetchClips} />
      )}
    </main>
  );
}
