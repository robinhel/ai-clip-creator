"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import { CampaignStatusBadge, PLATFORM_LABEL, STATUS_LABEL, STATUS_ORDER } from "@/components/CampaignStatusBadge";
import { api } from "@/lib/api";
import {
  buildPatch,
  changed,
  draftFrom,
  minViewsOf,
  prevStatus,
  rowId,
  rowStatus,
  rowsOf,
  sortRows,
  STATS,
  stepFor,
  todoItems,
  type Draft,
  type PatchBody,
  type Row,
  type Step,
  type TodoItem,
} from "@/lib/statistik";
import { cn, serverTime, SUMMARY_CHANGED } from "@/lib/utils";
import type { CampaignClip, CampaignRunInfo, CampaignStatus, CampaignSummary, ClipPostPatch } from "@/lib/types";

const usd = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "USD" });
const count = new Intl.NumberFormat("sv-SE");
const percent = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const date = new Intl.DateTimeFormat("sv-SE", { dateStyle: "medium" });
const DAY_MS = 86_400_000;

/** Klipp från Egna videor har vod_id upload (campaign/uploads_api.py). */
const UPLOAD = "upload";
const POSTED: CampaignStatus[] = ["postad", "inskickad", "analytics_inskickad", "betald"];

const NEXT_STEPS: Partial<Record<CampaignStatus, CampaignStatus[]>> = {
  postad: ["inskickad"],
  inskickad: ["analytics_inskickad", "betald"],
  analytics_inskickad: ["betald"],
};

// Ett steg bakåt, så att ett felklick går att ångra. Samma steg som servern tillåter (campaign/api.py).
// Betald går tillbaka till statusen före Betald när servern har den (prevStatus).
const BACK: Partial<Record<CampaignStatus, CampaignStatus>> = {
  postad: "godkand",
  inskickad: "postad",
  analytics_inskickad: "inskickad",
  betald: "inskickad",
};

// Kostnaden i budgeten per syfte, med svenska ord. Okända syften visas som de heter.
const PURPOSE_LABEL: Record<string, string> = {
  valja: "Claude väljer klipp",
  kalibrera: "Claude jämför betygen",
  granska: "Bildgranskning",
  variant: "Text för ett annat konto",
  chatt: "Chatten",
  chattsökning: "Sökning i chatten",
  transkribering: "Transkribering",
};

/** Kampanjens namn och länken till analytics-formuläret, ur kampanjens data. */
interface CampaignInfo {
  streamer: string;
  analyticsForm: string | null;
}
type Campaigns = Record<string, CampaignInfo>;

/** Filtret: postade inlägg (standard), alla eller en status. Står i adressen som ?status=. */
type Filter = CampaignStatus | "postade" | "alla";
const DEFAULT_FILTER: Filter = "postade";
const matches = (filter: Filter, status: CampaignStatus) =>
  filter === "alla" || (filter === "postade" ? POSTED.includes(status) : filter === status);

function BudgetCard({ title, spent, cap }: { title: string; spent: number; cap: number }) {
  const pct = cap > 0 ? Math.min((spent / cap) * 100, 100) : 0;
  return (
    <Card>
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{usd.format(spent)}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        <Progress value={pct} className={cn(pct >= 90 && "[&_[data-slot=progress-indicator]]:bg-destructive")} />
        <p className="text-xs text-muted-foreground">av {usd.format(cap)}</p>
      </CardContent>
    </Card>
  );
}

type Earnings = NonNullable<CampaignSummary["earnings"]>;
const monthName = new Intl.DateTimeFormat("sv-SE", { month: "short", year: "2-digit" });

/** Staplar per månad i SVG, med beloppet ovanför varje stapel. */
function MonthChart({ months }: { months: Earnings["by_month"] }) {
  const shown = months.slice(-12);
  const top = Math.max(...shown.map((m) => m.usd), 0);
  const H = 120;
  const bar = 34;
  const gap = 18;
  const width = shown.length * (bar + gap);
  return (
    <svg role="img" aria-label="Uppskattad intäkt per månad" viewBox={`0 0 ${width} ${H + 40}`} className="h-44 max-w-full" style={{ width }}>
      {shown.map((m, i) => {
        const h = top > 0 ? Math.max(2, (m.usd / top) * H) : 2;
        const x = i * (bar + gap) + gap / 2;
        const [y, mo] = m.month.split("-").map(Number);
        return (
          <g key={m.month} data-month={m.month}>
            <rect x={x} y={18 + H - h} width={bar} height={h} rx={5} className={m.usd > 0 ? "fill-brand" : "fill-line"} />
            <text x={x + bar / 2} y={12 + H - h} textAnchor="middle" className="fill-foreground text-[10px] tabular-nums">
              {usd.format(m.usd)}
            </text>
            <text x={x + bar / 2} y={H + 34} textAnchor="middle" className="fill-muted-foreground text-[10px]">
              {monthName.format(new Date(y, mo - 1, 1))}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function EarningsCards({ earnings }: { earnings: Earnings }) {
  // Per klipp är beloppet 0 under kampanjens minsta visningar.
  const over = earnings.per_clip.filter((c) => c.usd > 0).length;
  const cards: [string, string, string][] = [
    ["Uppskattad intäkt", usd.format(earnings.estimated_usd), "Visningar gånger kampanjens pris, med taket per klipp."],
    ["Utbetalt", usd.format(earnings.paid_usd), "Inlägg med status Betald. Beloppet i Betalt när det är ifyllt."],
    ["Klipp över minsta visningar", `${over} av ${earnings.per_clip.length}`, "Bara de här klippen ger pengar."],
  ];
  return (
    <section aria-label="Intäkter" className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
        {cards.map(([label, value, hint]) => (
          <div key={label} className="rounded-2xl border border-line bg-panel px-4 py-3">
            <p className="text-[13px] text-muted-foreground">{label}</p>
            <p data-earning={label} className="text-2xl font-bold tabular-nums">{value}</p>
            <p className="text-xs text-muted-foreground">{hint}</p>
          </div>
        ))}
      </div>
      <div className="rounded-2xl border border-line bg-panel p-4">
        <p className="mb-2 text-[13px] text-muted-foreground">Uppskattad intäkt per månad</p>
        {earnings.by_month.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Inga klipp med visningar än.</p>
        ) : (
          <div className="overflow-x-auto">
            <MonthChart months={earnings.by_month} />
          </div>
        )}
      </div>
    </section>
  );
}

/** Knappen som öppnar kampanjens analytics-formulär, eller var länken läggs in när den saknas. */
function AnalyticsFormLink({ profile, info }: { profile: string; info?: CampaignInfo }) {
  if (info?.analyticsForm) {
    return (
      <a
        href={info.analyticsForm}
        target="_blank"
        rel="noreferrer"
        className={buttonVariants({ size: "sm", variant: "outline" })}
      >
        Öppna formuläret
        <ExternalLink aria-hidden />
      </a>
    );
  }
  return (
    <span className="text-xs text-muted-foreground">
      Länken till analytics-formuläret saknas.{" "}
      <Link href={`/campaigns/${profile}`} className="underline underline-offset-4 hover:text-foreground">
        Lägg in den på kampanjsidan
      </Link>
      .
    </span>
  );
}

const days = (from: string, now: number) => Math.max(1, Math.floor((now - serverTime(from)) / DAY_MS));

/** Vad Robin ska göra i steget, i ord. */
function stepText(item: TodoItem, now: number): string {
  const data = item.post ?? item.clip;
  const views = count.format(data.views ?? 0);
  switch (item.step) {
    case "inskickad":
      return `Skicka in länken i Content Rewards. Tryck sedan ${STATUS_LABEL.inskickad}.`;
    case "visningar":
      return "Fyll i visningar, gillningar och kommentarer.";
    case "analytics_inskickad":
      return `${views} visningar. Skicka en skärminspelning av inläggets analytics i analytics-formuläret. Tryck sedan ${STATUS_LABEL.analytics_inskickad}.`;
    case "betald":
      return `Fyll i beloppet när Whop har betalat. Tryck sedan ${STATUS_LABEL.betald}.`;
    case "visningar_igen":
      return `Fyll i visningarna igen. De ändrades för ${days(data.updated, now)} dagar sedan. Kampanjen betalar från ${count.format(item.minViews)}.`;
    default:
      return "";
  }
}

/** Klippets titel och var inlägget ligger, så att två inlägg för samma klipp går att skilja åt. */
const itemName = ({ clip, post }: Row) => `${clip.title || "Utan titel"}${post ? ` på ${PLATFORM_LABEL[post.platform]}` : ""}`;

function TodoCard({
  items,
  campaigns,
  now,
  onGo,
}: {
  items: TodoItem[];
  campaigns: Campaigns;
  now: number;
  onGo: (item: TodoItem) => void;
}) {
  const here = items.filter((i) => i.step !== "posta");
  const toPost = items.length - here.length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Att göra</CardTitle>
        <CardDescription>Tryck på en punkt för att gå till raden med knappen för steget.</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Inget att göra just nu.</p>
        ) : (
          <ul className="divide-y divide-line text-sm" aria-label="Att göra">
            {here.map((item) => (
              <li key={item.key} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2.5">
                <a
                  href={`#${item.rowId}`}
                  data-todo={item.step}
                  onClick={(e) => {
                    e.preventDefault();
                    onGo(item);
                  }}
                  className="min-w-0 flex-1 rounded-md underline-offset-4 hover:underline"
                >
                  <span className="font-medium">{itemName(item)}</span>
                  <span className="block text-muted-foreground">{stepText(item, now)}</span>
                </a>
                {item.step === "analytics_inskickad" && (
                  <AnalyticsFormLink profile={item.clip.profile} info={campaigns[item.clip.profile]} />
                )}
              </li>
            ))}
            {toPost > 0 && (
              <li className="py-2.5">
                <Link href="/queue" className="underline-offset-4 hover:underline" data-todo="posta">
                  <span className="font-medium">
                    {toPost === 1 ? "1 godkänt klipp väntar" : `${toPost} godkända klipp väntar`} i Att posta.
                  </span>
                  <span className="block text-muted-foreground">Posta dem och spara länken.</span>
                </Link>
              </li>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

const numberInput =
  "h-8 w-full text-right tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";

/** Ett inlägg, eller ett klipp utan inlägg. Raden har egna fält, så att osparade siffror finns kvar vid filterbyte. */
function ClipRow({
  row,
  campaigns,
  estimate,
  runId,
  hidden,
  highlight,
  now,
  onUpdate,
}: {
  row: Row;
  campaigns: Campaigns;
  estimate?: number;
  /** Projektet klippet kommer från. */
  runId?: number;
  /** Dold av filtret. Raden finns kvar, så att osparade siffror inte försvinner. */
  hidden: boolean;
  /** Raden som en punkt i Att göra pekade på. */
  highlight: boolean;
  now: number;
  onUpdate: (clip: CampaignClip) => void;
}) {
  const { clip, post } = row;
  const saved = post ?? clip;
  const status = rowStatus(row);
  const posted = POSTED.includes(status);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(post ?? clip));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const savedValues = post ?? clip;
  const dirty = changed(draft, savedValues).length > 0;
  const step = stepFor(row, now);
  const info = campaigns[clip.profile];
  const shownEstimate = post ? post.estimate_usd : estimate;
  // Analytics inskickad går först vid kampanjens minsta visningar. Servern säger samma sak om man försöker.
  const belowMin = (saved.views ?? 0) < minViewsOf(clip);

  async function send(body: PatchBody) {
    setBusy(true);
    setError(null);
    try {
      onUpdate(post ? await api.campaign.posts.patch(post.id, body as ClipPostPatch) : await api.campaign.clips.patch(clip.id, body));
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara");
    } finally {
      setBusy(false);
    }
  }

  /** Spara fälten, och statusen om ett steg ges. Det som är skrivet men inte sparat följer med. */
  function save(next?: CampaignStatus) {
    const result = buildPatch(draft, savedValues, next);
    if ("error" in result) setError(result.error);
    else send(result.body);
  }

  async function removePost() {
    if (!post || !window.confirm(`Ta bort inlägget på ${PLATFORM_LABEL[post.platform]}? Länken och siffrorna försvinner.`)) return;
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.campaign.posts.remove(post.id));
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att ta bort inlägget");
      setBusy(false);
    }
  }

  const onEnter = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && dirty) save();
  };
  const back = status === "betald" && post ? (prevStatus(post) ?? BACK.betald) : BACK[status];
  const label = `${clip.id}${post ? ` på ${PLATFORM_LABEL[post.platform]}` : ""}`;

  return (
    <li
      id={rowId(row)}
      hidden={hidden}
      data-row-status={status}
      className={cn(
        "scroll-mt-24 rounded-xl border border-line bg-panel p-4 transition-shadow lg:grid lg:grid-cols-[minmax(0,1fr)_21rem_7.5rem_10.5rem] lg:items-start lg:gap-5",
        highlight && "ring-2 ring-brand"
      )}
    >
      <div className="min-w-0">
        <p className="font-medium leading-snug break-words">
          {runId ? (
            <Link href={`/runs/${runId}`} className="underline-offset-4 hover:underline">
              {clip.title || "Utan titel"}
            </Link>
          ) : (
            clip.title || "Utan titel"
          )}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {info?.streamer ?? clip.profile} · {clip.vod_id === UPLOAD ? "Egen video" : `VOD ${clip.vod_id}`}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          {post ? (
            <a href={post.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium underline underline-offset-4">
              {PLATFORM_LABEL[post.platform]}
              <ExternalLink className="size-3.5" aria-hidden />
            </a>
          ) : null}
          <CampaignStatusBadge status={status} />
          <span className="text-xs text-muted-foreground">
            {post ? `Postad ${date.format(serverTime(post.created))}` : `Klippt ${date.format(serverTime(clip.created))}`}
          </span>
        </div>
        {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
      </div>

      {posted ? (
        <div className="mt-3 lg:mt-0">
          <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
            {STATS.map(({ key, label: name }) => (
              <label key={key} className="space-y-1 text-xs text-muted-foreground">
                <span>{name}</span>
                <Input
                  aria-label={`${name} för klipp ${label}`}
                  type="number"
                  min="0"
                  step="1"
                  inputMode="numeric"
                  value={draft[key]}
                  data-goto={key === "views" && (step === "visningar" || step === "visningar_igen") ? "" : undefined}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  onKeyDown={onEnter}
                  disabled={busy}
                  className={numberInput}
                />
              </label>
            ))}
          </div>
          <Engagement row={row} />
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground lg:mt-0">
          {status === "godkand" ? (
            <Link href="/queue" className="underline underline-offset-4 hover:text-foreground">
              Posta klippet i Att posta
            </Link>
          ) : (
            "Inte postat."
          )}
        </p>
      )}

      <div className="mt-3 flex items-end gap-4 text-xs text-muted-foreground lg:mt-0 lg:block lg:space-y-2">
        <p>
          Uppskattat
          <span data-estimate={clip.id} className="block text-sm text-foreground tabular-nums">
            {shownEstimate == null ? "–" : usd.format(shownEstimate)}
          </span>
        </p>
        {/* Vad Whop faktiskt betalade. Ett inlägg får betalt först efter att det är inskickat. */}
        {post && status !== "postad" && (
          <label className="block w-32 space-y-1 lg:w-full">
            <span>Betalt i US$</span>
            <Input
              aria-label={`Betalt i US$ för klipp ${label}`}
              inputMode="decimal"
              placeholder="t.ex. 12,50"
              value={draft.paid}
              data-goto={step === "betald" ? "" : undefined}
              onChange={(e) => setDraft((d) => ({ ...d, paid: e.target.value }))}
              onKeyDown={onEnter}
              disabled={busy}
              className="h-8 w-full text-right tabular-nums"
            />
          </label>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-start gap-1.5 lg:mt-0 lg:flex-col lg:items-stretch">
        {dirty && (
          <Button size="sm" onClick={() => save()} disabled={busy}>
            Spara
          </Button>
        )}
        {(NEXT_STEPS[status] ?? []).map((next) => {
          const locked = next === "analytics_inskickad" && belowMin;
          return (
            <div key={next} className="space-y-1">
              <Button
                size="sm"
                variant={next === step ? "default" : "outline"}
                className="w-full"
                disabled={busy || locked}
                data-goto={next === step ? "" : undefined}
                onClick={() => save(next)}
              >
                {STATUS_LABEL[next]}
              </Button>
              {locked && (
                <p className="text-xs text-muted-foreground">Går först vid {count.format(minViewsOf(clip))} visningar.</p>
              )}
            </div>
          );
        })}
        {step === "analytics_inskickad" && <AnalyticsFormLink profile={clip.profile} info={info} />}
        {/* Ett postat inlägg ångras genom att det tas bort; klippet blir godkänt igen när inget inlägg är kvar. */}
        {post && status === "postad" ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={removePost}>
            Ta bort
          </Button>
        ) : (
          back && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => send({ status: back })} title={`Tillbaka till ${STATUS_LABEL[back]}`}>
              Ångra
            </Button>
          )
        )}
      </div>
    </li>
  );
}

/** Engagemang i procent, rött under kampanjens krav. */
function Engagement({ row }: { row: Row }) {
  const value = (row.post ?? row.clip).engagement;
  const need = row.clip.engagement_min;
  if (value == null) return null;
  return (
    <p className="mt-1.5 text-xs text-muted-foreground">
      Engagemang{" "}
      <span
        className={cn("tabular-nums text-foreground", need != null && value < need && "font-medium text-destructive")}
        title={need != null ? `Kampanjen kräver minst ${percent.format(need)} %` : undefined}
      >
        {percent.format(value)} %
      </span>
      {need != null && value < need && ` (kampanjen kräver ${percent.format(need)} %)`}
    </p>
  );
}

interface Props {
  initialSummary?: CampaignSummary | null;
  initialClips?: CampaignClip[] | null;
}

export function ClipLogClient({ initialSummary = null, initialClips = null }: Props) {
  const [summary, setSummary] = useState<CampaignSummary | null>(initialSummary);
  const [clips, setClips] = useState<CampaignClip[] | null>(initialClips);
  const [filter, setFilter] = useState<Filter>(DEFAULT_FILTER);
  const [campaign, setCampaign] = useState("");
  const [search, setSearch] = useState("");
  const [runs, setRuns] = useState<CampaignRunInfo[]>([]);
  const [campaigns, setCampaigns] = useState<Campaigns>({});
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [now] = useState(() => Date.now());

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [summaryData, clipData] = await Promise.all([api.campaign.summary(), api.campaign.clips.list()]);
      setSummary(summaryData);
      setClips(clipData);
      // Länken till projektet är extra; listan ska visas ändå.
      api.campaign.runs.list().then(setRuns).catch(() => {});
      setError(null);
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta klipploggen");
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Servern har oftast redan hämtat datan; hämta bara här om den inte kunde.
  const hasInitial = initialSummary !== null && initialClips !== null;
  useEffect(() => {
    if (!hasInitial) load();
    else api.campaign.runs.list().then(setRuns).catch(() => {});
  }, [load, hasInitial]);

  // Kampanjernas namn och länken till analytics-formuläret. Den är extra; listan visas ändå.
  const profiles = [...new Set((clips ?? []).map((c) => c.profile))].sort();
  const profileKey = profiles.join(",");
  useEffect(() => {
    for (const name of profileKey ? profileKey.split(",") : []) {
      api.campaign.profile
        .get(name)
        .then((d) => setCampaigns((prev) => ({ ...prev, [name]: { streamer: d.streamer, analyticsForm: d.links?.analytics_form ?? null } })))
        .catch(() => {});
    }
  }, [profileKey]);

  // Filtret står i adressen (?status=…&kampanj=…), så att en länk eller omladdning visar samma lista.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");
    if (status === "alla" || (status && STATUS_ORDER.includes(status as CampaignStatus))) setFilter(status as Filter);
    setCampaign(params.get("kampanj") ?? "");
  }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of [["status", filter === DEFAULT_FILTER ? "" : filter], ["kampanj", campaign]] as const) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }, [filter, campaign]);

  // En punkt i Att göra: visa raden om filtret döljer den, rulla dit och sätt markören på steget.
  useEffect(() => {
    if (!highlight) return;
    const el = document.getElementById(highlight);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    el?.querySelector<HTMLElement>("[data-goto]")?.focus({ preventScroll: true });
    const timer = window.setTimeout(() => setHighlight(null), 2500);
    return () => window.clearTimeout(timer);
  }, [highlight]);

  function runIdFor(clip: CampaignClip): number | undefined {
    // Senaste projektet för samma VOD och kampanj, som i Att posta.
    return runs
      .filter((r) => r.vod_id === clip.vod_id && r.profile === clip.profile)
      .reduce<number | undefined>((latest, r) => Math.max(latest ?? 0, r.run_id), undefined);
  }

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    // Intäkterna ändras när ett inlägg får siffror eller blir betalt.
    api.campaign.summary().then(setSummary).catch(() => {});
  }

  const rows = sortRows(rowsOf(clips ?? []), now);
  const needle = search.trim().toLowerCase();
  const inScope = (r: Row) =>
    (!campaign || r.clip.profile === campaign) &&
    (!needle || r.clip.title.toLowerCase().includes(needle) || r.clip.vod_id.includes(needle));
  const shows = (r: Row) => inScope(r) && matches(filter, rowStatus(r));
  const visible = rows.filter(shows);
  const counts = (f: Filter) => rows.filter((r) => inScope(r) && matches(f, rowStatus(r))).length;
  const filtered = filter !== DEFAULT_FILTER || !!campaign || !!needle;
  const todo = todoItems(clips ?? [], now);
  const purposes = Object.entries(summary?.budget.by_purpose ?? {});
  const estimates = new Map(summary?.earnings?.per_clip.map((c) => [c.id, c.usd]) ?? []);

  function clearFilters() {
    setFilter(DEFAULT_FILTER);
    setCampaign("");
    setSearch("");
  }

  function go(item: TodoItem) {
    if (!item.rowId) return;
    const row = rows.find((r) => rowId(r) === item.rowId);
    if (row && !shows(row)) clearFilters();
    setHighlight(item.rowId);
  }

  // Statusar utan rader visas inte, utom den som är vald.
  const chips: Filter[] = ["postade", "alla", ...STATUS_ORDER.filter((s) => s === filter || counts(s) > 0)];

  return (
    <main className="w-full max-w-[1180px] space-y-8 px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Statistik</h1>
          <p className="text-sm text-muted-foreground">
            Dina postade inlägg: skicka in, fyll i visningar, skicka analytics och markera {STATUS_LABEL.betald}.
          </p>
        </div>
        <Button variant="outline" onClick={load} disabled={refreshing}>
          <RefreshCw className={refreshing ? "animate-spin" : undefined} />
          Uppdatera
        </Button>
      </div>

      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-destructive/5 p-4 text-sm text-destructive">
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={load}>
            Försök igen
          </Button>
        </div>
      )}

      {!clips && !error && <p className="text-sm text-muted-foreground">Hämtar klipploggen…</p>}

      {clips && <TodoCard items={todo} campaigns={campaigns} now={now} onGo={go} />}

      {clips && (
        <section className="space-y-4" aria-labelledby="inlagg-rubrik">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 id="inlagg-rubrik" className="text-lg font-semibold">
              Inlägg och klipp{" "}
              <span className="font-normal text-muted-foreground">
                (visar {visible.length} av {rows.length})
              </span>
            </h2>
            <div className="flex flex-wrap gap-2">
              <Input
                aria-label="Sök på titel eller VOD"
                type="search"
                placeholder="Sök titel eller VOD"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-48"
              />
              {profiles.length > 1 && (
                <NativeSelect aria-label="Visa kampanj" value={campaign} onChange={(e) => setCampaign(e.target.value)} className="w-44">
                  <option value="">Alla kampanjer</option>
                  {profiles.map((name) => (
                    <option key={name} value={name}>
                      {campaigns[name]?.streamer ?? name}
                    </option>
                  ))}
                </NativeSelect>
              )}
              {filtered && (
                <Button variant="ghost" onClick={clearFilters}>
                  Rensa filter
                </Button>
              )}
            </div>
          </div>

          {/* Postade räknas per inlägg, de andra per klipp: samma rader som listan visar. */}
          <div role="group" aria-label="Visa status" className="flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <button
                key={chip}
                type="button"
                aria-pressed={filter === chip}
                onClick={() => setFilter(chip)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs transition-colors hover:bg-muted",
                  filter === chip && "border-brand bg-brand/15 text-foreground"
                )}
              >
                {chip === "postade" ? "Postade inlägg" : chip === "alla" ? "Alla" : STATUS_LABEL[chip]}
                <span className="tabular-nums font-medium">{counts(chip)}</span>
              </button>
            ))}
          </div>

          {visible.length === 0 && (
            <p className="rounded-xl border border-dashed px-4 py-12 text-center text-sm text-muted-foreground">
              {clips.length === 0 ? (
                <>
                  Inga klipp än. Hämta klipp från en stream på{" "}
                  <Link href="/" className="underline underline-offset-4 hover:text-foreground">Hem</Link>, eller ladda upp en{" "}
                  <Link href="/uploads" className="underline underline-offset-4 hover:text-foreground">egen video</Link>.
                </>
              ) : (
                "Inga inlägg passar filtret."
              )}
            </p>
          )}
          <ul className="space-y-3" aria-label="Inlägg och klipp">
            {rows.map((row) => (
              <ClipRow
                key={`${rowId(row)}-${(row.post ?? row.clip).updated}`}
                row={row}
                campaigns={campaigns}
                estimate={estimates.get(row.clip.id)}
                runId={runIdFor(row.clip)}
                hidden={!shows(row)}
                highlight={highlight === rowId(row)}
                now={now}
                onUpdate={handleUpdate}
              />
            ))}
          </ul>
        </section>
      )}

      {summary?.earnings && <EarningsCards earnings={summary.earnings} />}

      {summary && (
        <section aria-label="Budget" className="grid gap-4 sm:grid-cols-2">
          <BudgetCard title="Budget i månaden" spent={summary.budget.month_usd} cap={summary.budget.month_cap_usd} />
          <BudgetCard title="Budget i dag" spent={summary.budget.today_usd} cap={summary.budget.day_cap_usd} />
          {purposes.length > 0 && (
            <p className="text-xs text-muted-foreground sm:col-span-2">
              Kostnad i månaden:{" "}
              {purposes
                .map(([purpose, cost]) => `${PURPOSE_LABEL[purpose] ?? purpose} ${usd.format(cost.usd)} (${cost.calls} anrop)`)
                .join(" · ")}
            </p>
          )}
        </section>
      )}
    </main>
  );
}
