"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown, ExternalLink, RefreshCw } from "lucide-react";
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
  DEFAULT_FILTER,
  draftFrom,
  inScope,
  lengthGroups,
  matches,
  METRICS,
  minViewsOf,
  parseCount,
  POSTED,
  rowId,
  rowStatus,
  rowsOf,
  sortRows,
  STATS,
  stepFor,
  todoItems,
  undoStatus,
  watchPctOfLength,
  type Draft,
  type Filter,
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
const decimal = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });
const date = new Intl.DateTimeFormat("sv-SE", { dateStyle: "medium" });
const DAY_MS = 86_400_000;

/** Klipp från Egna videor har vod_id upload (campaign/uploads_api.py). */
const UPLOAD = "upload";

/** Knapparna för stegen. Steget efter Inskickad heter Analytics inskickad i hela appen. */
const STEP_LABEL = { inskickad: "Inskickad", analytics_inskickad: "Analytics inskickad", betald: "Betald" } as const;
type NextStep = keyof typeof STEP_LABEL;

const NEXT_STEPS: Partial<Record<CampaignStatus, NextStep[]>> = {
  postad: ["inskickad"],
  inskickad: ["analytics_inskickad", "betald"],
  analytics_inskickad: ["betald"],
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

const link = "underline underline-offset-4 hover:text-foreground";

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

function BudgetSection({ budget }: { budget: CampaignSummary["budget"] }) {
  const purposes = Object.entries(budget.by_purpose ?? {});
  return (
    <section aria-label="Budget" className="grid gap-4 sm:grid-cols-2">
      <BudgetCard title="Budget i månaden" spent={budget.month_usd} cap={budget.month_cap_usd} />
      <BudgetCard title="Budget i dag" spent={budget.today_usd} cap={budget.day_cap_usd} />
      {purposes.length > 0 && (
        <p className="text-xs text-muted-foreground sm:col-span-2">
          Kostnad i månaden:{" "}
          {purposes
            .map(([purpose, cost]) => `${PURPOSE_LABEL[purpose] ?? purpose} ${usd.format(cost.usd)} (${cost.calls} anrop)`)
            .join(" · ")}
        </p>
      )}
    </section>
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
    ["Utbetalt", usd.format(earnings.paid_usd), "Inlägg med status Betald: beloppet i Betalt, eller uppskattningen när beloppet saknas."],
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
      <a href={info.analyticsForm} target="_blank" rel="noreferrer" className={buttonVariants({ size: "sm", variant: "outline" })}>
        Öppna formuläret
        <ExternalLink aria-hidden />
      </a>
    );
  }
  return (
    <span className="text-xs text-muted-foreground">
      Länken till analytics-formuläret saknas.{" "}
      <Link href={`/campaigns/${profile}`} className={link}>
        Lägg in den på kampanjsidan
      </Link>
      .
    </span>
  );
}

const days = (from: string, now: number) => Math.max(1, Math.floor((now - serverTime(from)) / DAY_MS));
const viewsOf = (item: TodoItem) => count.format((item.post ?? item.clip).views ?? 0);

/** Vad Robin ska göra i steget, i ord. Steget posta görs i Att posta och har en egen punkt. */
const STEP_TEXT: Record<Exclude<Step, "posta">, (item: TodoItem, now: number) => string> = {
  inskickad: () => `Skicka in länken i Content Rewards. Tryck sedan ${STEP_LABEL.inskickad}.`,
  visningar: () => "Fyll i visningar, gillningar och kommentarer.",
  analytics_inskickad: (item) =>
    `${viewsOf(item)} visningar. Fyll i Fler mått. Skicka en skärminspelning av inläggets analytics i analytics-formuläret. Tryck sedan ${STEP_LABEL.analytics_inskickad}.`,
  betald: () => `Fyll i beloppet när Whop har betalat. Tryck sedan ${STEP_LABEL.betald}.`,
  visningar_igen: (item, now) =>
    `Fyll i visningarna igen. De ändrades för ${days((item.post ?? item.clip).updated, now)} dagar sedan. Kampanjen betalar från ${count.format(item.minViews)}.`,
};

/** Klippets titel och var inlägget ligger, så att två inlägg för samma klipp går att skilja åt. */
const itemName = ({ clip, post }: Row) => `${clip.title || "Utan titel"}${post ? ` på ${PLATFORM_LABEL[post.platform]}` : ""}`;

function TodoItemRow({ item, info, now, onGo }: { item: TodoItem; info?: CampaignInfo; now: number; onGo: (item: TodoItem) => void }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2.5">
      <a
        href={`#${item.rowId}`}
        data-todo={item.step}
        onClick={(e) => {
          e.preventDefault();
          onGo(item);
        }}
        className="min-w-0 flex-1 basis-72 rounded-md underline-offset-4 hover:underline"
      >
        <span className="font-medium">{itemName(item)}</span>
        <span className="block text-muted-foreground">{STEP_TEXT[item.step as Exclude<Step, "posta">](item, now)}</span>
      </a>
      {item.step === "analytics_inskickad" && <AnalyticsFormLink profile={item.clip.profile} info={info} />}
    </li>
  );
}

function TodoCard({ items, campaigns, now, onGo }: { items: TodoItem[]; campaigns: Campaigns; now: number; onGo: (item: TodoItem) => void }) {
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
              <TodoItemRow key={item.key} item={item} info={campaigns[item.clip.profile]} now={now} onGo={onGo} />
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

/** Sparar raden: inlägget när det finns, annars klippet. Menyns siffror läses om efteråt. */
function useRowSave({ clip, post }: Row, onUpdate: (clip: CampaignClip) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<CampaignClip>, failed: string) {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await action());
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
    } catch (err) {
      setError(err instanceof Error ? err.message : failed);
    } finally {
      setBusy(false);
    }
  }

  const send = (body: PatchBody) =>
    run(
      () => (post ? api.campaign.posts.patch(post.id, body as ClipPostPatch) : api.campaign.clips.patch(clip.id, body)),
      "Det gick inte att spara"
    );

  async function remove() {
    if (!post || !window.confirm(`Ta bort inlägget på ${PLATFORM_LABEL[post.platform]}? Länken och siffrorna försvinner.`)) return;
    await run(() => api.campaign.posts.remove(post.id), "Det gick inte att ta bort inlägget");
  }

  return { busy, error, setError, send, remove };
}

/** Fälten på en rad: det som är skrivet, och vilket fält Att göra ska sätta markören i. */
interface FieldProps {
  row: Row;
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  busy: boolean;
  step: Step | null;
  /** Klippets id och plattform, så att en skärmläsare skiljer raderna åt. */
  label: string;
  onEnter: (e: React.KeyboardEvent) => void;
}

function RowTitle({ row, runId, streamer, error }: { row: Row; runId?: number; streamer?: string; error: string | null }) {
  const { clip, post } = row;
  const title = clip.title || "Utan titel";
  return (
    <div className="min-w-0">
      <p className="font-medium leading-snug break-words">
        {runId ? (
          <Link href={`/runs/${runId}`} className="underline-offset-4 hover:underline">
            {title}
          </Link>
        ) : (
          title
        )}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {streamer ?? clip.profile} · {clip.vod_id === UPLOAD ? "Egen video" : `VOD ${clip.vod_id}`}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        {post && (
          <a href={post.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium underline underline-offset-4">
            {PLATFORM_LABEL[post.platform]}
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
        <CampaignStatusBadge status={rowStatus(row)} />
        <span className="text-xs text-muted-foreground">
          {post ? `Postad ${date.format(serverTime(post.created))}` : `Klippt ${date.format(serverTime(clip.created))}`}
        </span>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Engagemang i procent, rött under kampanjens krav. */
function Engagement({ row }: { row: Row }) {
  const value = (row.post ?? row.clip).engagement;
  const need = row.clip.engagement_min;
  if (value == null) return null;
  const low = need != null && value < need;
  return (
    <p className="mt-1.5 text-xs text-muted-foreground">
      Engagemang{" "}
      <span className={cn("tabular-nums text-foreground", low && "font-medium text-destructive")}>{percent.format(value)} %</span>
      {low && ` (kampanjen kräver ${percent.format(need)} %)`}
    </p>
  );
}

/** De fyra talen och engagemanget. children: knappen Fler mått, direkt under engagemanget. */
function StatFields({ row, draft, setDraft, busy, step, label, onEnter, children }: FieldProps & { children?: React.ReactNode }) {
  const goto = step === "visningar" || step === "visningar_igen";
  return (
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
              data-goto={key === "views" && goto ? "" : undefined}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              onKeyDown={onEnter}
              disabled={busy}
              className={numberInput}
            />
          </label>
        ))}
      </div>
      <Engagement row={row} />
      {children}
    </div>
  );
}

/** Uppskattad intäkt och, när inlägget är inskickat, fältet Betalt med vad Whop faktiskt betalade. */
function MoneyFields({ row, draft, setDraft, busy, step, label, onEnter, estimate }: FieldProps & { estimate?: number }) {
  const shown = row.post ? row.post.estimate_usd : estimate;
  return (
    <div className="mt-3 flex items-start gap-4 text-xs text-muted-foreground lg:mt-0 lg:block lg:space-y-2">
      <p className="space-y-1">
        Uppskattat
        <span data-estimate={row.clip.id} className="block text-sm leading-8 text-foreground tabular-nums lg:leading-normal">
          {shown == null ? "–" : usd.format(shown)}
        </span>
      </p>
      {row.post && row.post.status !== "postad" && (
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
  );
}

/** Ja, Nej eller Okänt för Nådde gränsen på 7 dagar. */
function reachedText(reached?: boolean | null): string {
  if (reached == null) return "Okänt";
  return reached ? "Ja" : "Nej";
}

/** Värdena i Fler mått som Robin inte skriver själv. */
function MetricFacts({ row }: { row: Row }) {
  const { clip, post } = row;
  const pct = watchPctOfLength(post?.avg_watch_s, clip.length_s);
  const facts: [string, string][] = [
    ["Längd (s)", clip.length_s == null ? "–" : decimal.format(clip.length_s)],
    ["Tittartid i procent av längden", pct == null ? "–" : `${decimal.format(pct)} %`],
    ["Nådde gränsen på 7 dagar", reachedText(post?.reached_min_7d)],
    ["Version", clip.version_of == null ? "Original" : `Version av #${clip.version_of}`],
  ];
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs sm:grid-cols-4">
      {facts.map(([name, value]) => (
        <div key={name}>
          <dt className="text-muted-foreground">{name}</dt>
          <dd className="text-sm tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const morePanelId = (row: Row) => `${rowId(row)}-matt`;

/** Knappen som öppnar och stänger Fler mått. Står under engagemanget, bara på en rad med inlägg. */
function MoreButton({ row, open, onToggle }: { row: Row; open: boolean; onToggle: () => void }) {
  if (!row.post) return null;
  return (
    <Button
      size="sm"
      variant="ghost"
      className="mt-1.5 -ml-2.5"
      aria-expanded={open}
      aria-controls={open ? morePanelId(row) : undefined}
      onClick={onToggle}
    >
      <ChevronDown className={cn("transition-transform", open && "rotate-180")} aria-hidden />
      Fler mått
    </Button>
  );
}

/** Fler mått från TikTok Studio, under hela raden när den är öppen. Sparas som de andra fälten, med Spara, ett steg eller Enter. */
function MoreMetrics({ row, draft, setDraft, busy, step, label, onEnter, open }: FieldProps & { open: boolean }) {
  if (!open) return null;
  // Att göra vid Analytics inskickad sätter markören i första fältet.
  const goto = step === "analytics_inskickad";
  return (
    <div id={morePanelId(row)} className="mt-3 lg:col-span-4 lg:row-start-2 lg:mt-0">
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-5">
        {METRICS.map((metric) => (
          <label key={metric.key} className="space-y-1 text-xs text-muted-foreground">
            <span>{metric.label}</span>
            <Input
              aria-label={`${metric.label} för klipp ${label}`}
              inputMode={metric.key === "saves" ? "numeric" : "decimal"}
              value={draft[metric.key]}
              data-goto={goto && metric.key === "avg_watch_s" ? "" : undefined}
              onChange={(e) => setDraft((d) => ({ ...d, [metric.key]: e.target.value }))}
              onKeyDown={onEnter}
              disabled={busy}
              className="h-8 w-full text-right tabular-nums"
            />
            {metric.key === "countries_pct" && row.clip.audience_countries && <span className="block">{row.clip.audience_countries}</span>}
          </label>
        ))}
      </div>
      <MetricFacts row={row} />
    </div>
  );
}

/**
 * Raderna där Fler mått är öppen, med rowId som nyckel. Ligger utanför raden: raden byggs om efter varje
 * sparning (nyckeln har updated), och panelen ska vara öppen kvar.
 */
function useMoreOpen() {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (id: string) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  const show = (id: string) => setOpen((o) => ({ ...o, [id]: true }));
  return { open, toggle, show };
}
type MoreOpen = ReturnType<typeof useMoreOpen>;

function NotPosted({ status }: { status: CampaignStatus }) {
  return (
    <p className="mt-3 text-sm text-muted-foreground lg:mt-0">
      {status === "godkand" ? (
        <Link href="/queue" className={link}>
          Posta klippet i Att posta
        </Link>
      ) : (
        "Inte postat."
      )}
    </p>
  );
}

function StepButton(props: { next: NextStep; primary: boolean; locked: boolean; minViews: number; busy: boolean; onClick: () => void }) {
  const { next, primary, locked, minViews, busy, onClick } = props;
  return (
    <div className="space-y-1">
      <Button
        size="sm"
        variant={primary ? "default" : "outline"}
        className="w-full"
        disabled={busy || locked}
        data-goto={primary ? "" : undefined}
        onClick={onClick}
      >
        {STEP_LABEL[next]}
      </Button>
      {locked && <p className="text-xs text-muted-foreground">Analytics går först vid {count.format(minViews)} visningar.</p>}
    </div>
  );
}

/** Ett postat inlägg ångras genom att det tas bort; klippet blir godkänt igen när inget inlägg är kvar. */
function UndoButton({ row, busy, onSave, onRemove }: { row: Row; busy: boolean; onSave: (next: CampaignStatus) => void; onRemove: () => void }) {
  if (row.post?.status === "postad") {
    return (
      <Button size="sm" variant="ghost" disabled={busy} onClick={onRemove}>
        Ta bort
      </Button>
    );
  }
  const back = undoStatus(row);
  if (!back) return null;
  return (
    <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSave(back)} title={`Tillbaka till ${STATUS_LABEL[back]}`}>
      Ångra
    </Button>
  );
}

interface ActionProps {
  row: Row;
  step: Step | null;
  dirty: boolean;
  busy: boolean;
  /** Visningarna i fältet är under kampanjens minsta. */
  belowMin: boolean;
  info?: CampaignInfo;
  onSave: (next?: CampaignStatus) => void;
  onRemove: () => void;
}

function RowActions({ row, step, dirty, busy, belowMin, info, onSave, onRemove }: ActionProps) {
  return (
    <div className="mt-3 flex flex-wrap items-start gap-1.5 lg:mt-0 lg:flex-col lg:items-stretch">
      {dirty && (
        <Button size="sm" onClick={() => onSave()} disabled={busy}>
          Spara
        </Button>
      )}
      {(NEXT_STEPS[rowStatus(row)] ?? []).map((next) => (
        <StepButton
          key={next}
          next={next}
          primary={next === step}
          locked={next === "analytics_inskickad" && belowMin}
          minViews={minViewsOf(row.clip)}
          busy={busy}
          onClick={() => onSave(next)}
        />
      ))}
      {step === "analytics_inskickad" && <AnalyticsFormLink profile={row.clip.profile} info={info} />}
      <UndoButton row={row} busy={busy} onSave={onSave} onRemove={onRemove} />
    </div>
  );
}

interface RowProps {
  row: Row;
  info?: CampaignInfo;
  estimate?: number;
  /** Projektet klippet kommer från. */
  runId?: number;
  /** Dold av filtret. Raden finns kvar, så att osparade siffror inte försvinner. */
  hidden: boolean;
  /** Raden som en punkt i Att göra pekade på. */
  highlight: boolean;
  /** Fler mått är öppen. */
  moreOpen: boolean;
  now: number;
  onUpdate: (clip: CampaignClip) => void;
  onToggleMore: () => void;
}

/** Ett inlägg, eller ett klipp utan inlägg. På smal skärm ett kort, på bred skärm en rad med fyra kolumner. */
function ClipRow({ row, info, estimate, runId, hidden, highlight, moreOpen, now, onUpdate, onToggleMore }: RowProps) {
  const saved = row.post ?? row.clip;
  const status = rowStatus(row);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(saved));
  const { busy, error, setError, send, remove } = useRowSave(row, onUpdate);
  const dirty = changed(draft, saved).length > 0;
  const step = stepFor(row, now);

  /** Spara fälten, och statusen om ett steg ges. Det som är skrivet men inte sparat följer med. */
  function save(next?: CampaignStatus) {
    const result = buildPatch(draft, saved, next);
    if ("error" in result) setError(result.error);
    else send(result.body);
  }

  const fields: FieldProps = {
    row,
    draft,
    setDraft,
    busy,
    step,
    label: `${row.clip.id}${row.post ? ` på ${PLATFORM_LABEL[row.post.platform]}` : ""}`,
    onEnter: (e) => {
      if (e.key === "Enter" && dirty) save();
    },
  };

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
      <RowTitle row={row} runId={runId} streamer={info?.streamer} error={error} />
      {POSTED.includes(status) ? (
        <>
          <StatFields {...fields}>
            <MoreButton row={row} open={moreOpen} onToggle={onToggleMore} />
          </StatFields>
          <MoneyFields {...fields} estimate={estimate} />
        </>
      ) : (
        <NotPosted status={status} />
      )}
      {row.post && <MoreMetrics {...fields} open={moreOpen} />}
      <RowActions
        row={row}
        step={step}
        dirty={dirty}
        busy={busy}
        belowMin={(parseCount(draft.views) ?? 0) < minViewsOf(row.clip)}
        info={info}
        onSave={save}
        onRemove={remove}
      />
    </li>
  );
}

/** Klipploggen från servern, med Uppdatera och projekten som titlarna länkar till. */
function useClipLog({ initialSummary = null, initialClips = null }: Props) {
  const [summary, setSummary] = useState(initialSummary);
  const [clips, setClips] = useState(initialClips);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [summaryData, clipData] = await Promise.all([api.campaign.summary(), api.campaign.clips.list()]);
      setSummary(summaryData);
      setClips(clipData);
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
  }, [load, hasInitial]);

  function update(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    // Intäkterna ändras när ett inlägg får siffror eller blir betalt.
    api.campaign.summary().then(setSummary).catch(() => {});
  }

  return { summary, clips, error, refreshing, load, update };
}

/** Projektet som klippets titel länkar till: det senaste för samma VOD och kampanj, som i Att posta.
 * Länken är extra; listan visas ändå. */
function useRunLinks() {
  const [runs, setRuns] = useState<CampaignRunInfo[]>([]);
  const reload = useCallback(() => {
    api.campaign.runs.list().then(setRuns).catch(() => {});
  }, []);
  useEffect(reload, [reload]);
  const runIdFor = (clip: CampaignClip): number | undefined =>
    runs
      .filter((r) => r.vod_id === clip.vod_id && r.profile === clip.profile)
      .reduce<number | undefined>((latest, r) => Math.max(latest ?? 0, r.run_id), undefined);
  return { runIdFor, reload };
}

/** Kampanjernas namn och länken till analytics-formuläret. De är extra; listan visas ändå. */
function useCampaigns(profiles: string[]): Campaigns {
  const [campaigns, setCampaigns] = useState<Campaigns>({});
  const key = profiles.join(",");
  useEffect(() => {
    for (const name of key ? key.split(",") : []) {
      api.campaign.profile
        .get(name)
        .then((d) => setCampaigns((prev) => ({ ...prev, [name]: { streamer: d.streamer, analyticsForm: d.links?.analytics_form ?? null } })))
        .catch(() => {});
    }
  }, [key]);
  return campaigns;
}

/** Filter och sökning. Filtret står i adressen (?status=…&kampanj=…), så att en länk eller omladdning visar samma lista. */
function useFilters({ initialFilter = DEFAULT_FILTER, initialCampaign = "" }: Props) {
  const [filter, setFilter] = useState(initialFilter);
  const [campaign, setCampaign] = useState(initialCampaign);
  const [search, setSearch] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of [["status", filter === DEFAULT_FILTER ? "" : filter], ["kampanj", campaign]] as const) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }, [filter, campaign]);

  const filtered = filter !== DEFAULT_FILTER || !!campaign || !!search.trim();
  function clear() {
    setFilter(DEFAULT_FILTER);
    setCampaign("");
    setSearch("");
  }
  return { filter, setFilter, campaign, setCampaign, search, setSearch, filtered, clear };
}

/** Raden som en punkt i Att göra pekar på: rulla dit, sätt markören på steget och markera raden en stund. */
function useHighlight() {
  const [highlight, setHighlight] = useState<{ id: string } | null>(null);
  useEffect(() => {
    if (!highlight) return;
    const el = document.getElementById(highlight.id);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    el?.querySelector<HTMLElement>("[data-goto]")?.focus({ preventScroll: true });
    const timer = window.setTimeout(() => setHighlight(null), 2500);
    return () => window.clearTimeout(timer);
  }, [highlight]);
  return [highlight?.id ?? null, (id: string) => setHighlight({ id })] as const;
}

type Filters = ReturnType<typeof useFilters>;

function ListHeader({ shown, total, profiles, campaigns, f }: { shown: number; total: number; profiles: string[]; campaigns: Campaigns; f: Filters }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <h2 id="inlagg-rubrik" className="text-lg font-semibold">
        Inlägg och klipp{" "}
        <span className="font-normal text-muted-foreground">
          (visar {shown} av {total})
        </span>
      </h2>
      <div className="flex flex-wrap gap-2">
        <Input
          aria-label="Sök på titel eller VOD"
          type="search"
          placeholder="Sök titel eller VOD"
          value={f.search}
          onChange={(e) => f.setSearch(e.target.value)}
          className="w-48"
        />
        {profiles.length > 1 && (
          <NativeSelect aria-label="Visa kampanj" value={f.campaign} onChange={(e) => f.setCampaign(e.target.value)} className="w-44">
            <option value="">Alla kampanjer</option>
            {profiles.map((name) => (
              <option key={name} value={name}>
                {campaigns[name]?.streamer ?? name}
              </option>
            ))}
          </NativeSelect>
        )}
        {f.filtered && (
          <Button variant="ghost" onClick={f.clear}>
            Rensa filter
          </Button>
        )}
      </div>
    </div>
  );
}

function filterLabel(filter: Filter): string {
  if (filter === "postade") return "Postade inlägg";
  if (filter === "alla") return "Alla";
  return STATUS_LABEL[filter];
}

/** Statusarna som filter. Postade räknas per inlägg, de andra per klipp: samma rader som listan visar. */
function StatusChips({ rows, filter, onChange }: { rows: Row[]; filter: Filter; onChange: (filter: Filter) => void }) {
  const size = (f: Filter) => rows.filter((r) => matches(f, rowStatus(r))).length;
  // Statusar utan rader visas inte, utom den som är vald.
  const chips: Filter[] = ["postade", "alla", ...STATUS_ORDER.filter((s) => s === filter || size(s) > 0)];
  return (
    <div role="group" aria-label="Visa status" className="flex flex-wrap gap-1.5">
      {chips.map((chip) => (
        <button
          key={chip}
          type="button"
          aria-pressed={filter === chip}
          onClick={() => onChange(chip)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs transition-colors hover:bg-muted",
            filter === chip && "border-brand bg-brand/15 text-foreground"
          )}
        >
          {filterLabel(chip)}
          <span className="tabular-nums font-medium">{size(chip)}</span>
        </button>
      ))}
    </div>
  );
}

function EmptyList({ noClips, filtered }: { noClips: boolean; filtered: boolean }) {
  let text: React.ReactNode = filtered ? "Inga inlägg passar filtret." : "Inga postade inlägg än. Tryck Alla för att se alla klipp.";
  if (noClips) {
    text = (
      <>
        Inga klipp än. Hämta klipp från en stream på{" "}
        <Link href="/" className={link}>
          Hem
        </Link>
        , eller ladda upp en{" "}
        <Link href="/uploads" className={link}>
          egen video
        </Link>
        .
      </>
    );
  }
  return <p className="rounded-xl border border-dashed px-4 py-12 text-center text-sm text-muted-foreground">{text}</p>;
}

function PageHeader({ refreshing, onRefresh }: { refreshing: boolean; onRefresh: () => void }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Statistik</h1>
        <p className="text-sm text-muted-foreground">
          Dina postade inlägg: skicka in, fyll i visningar, skicka analytics och markera {STEP_LABEL.betald}.
        </p>
      </div>
      <Button variant="outline" onClick={onRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? "animate-spin" : undefined} />
        Uppdatera
      </Button>
    </div>
  );
}

function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-destructive/5 p-4 text-sm text-destructive">
      <span>{error}</span>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Försök igen
      </Button>
    </div>
  );
}

/** Raden passar sökningen, kampanjen och statusfiltret. */
const showsIn = (f: Filters) => (r: Row) => inScope(r, f.campaign, f.search) && matches(f.filter, rowStatus(r));

interface ListProps {
  clips: CampaignClip[];
  rows: Row[];
  profiles: string[];
  campaigns: Campaigns;
  f: Filters;
  earnings?: Earnings;
  runIdFor: (clip: CampaignClip) => number | undefined;
  highlight: string | null;
  more: MoreOpen;
  now: number;
  onUpdate: (clip: CampaignClip) => void;
}

/** Alla rader. Dolda rader finns kvar, så att osparade siffror inte försvinner när filtret byts. */
function LogList({ clips, rows, profiles, campaigns, f, earnings, runIdFor, highlight, more, now, onUpdate }: ListProps) {
  const shows = showsIn(f);
  const estimates = new Map(earnings?.per_clip.map((c) => [c.id, c.usd]));
  const shown = rows.filter(shows).length;
  return (
    <section className="space-y-4" aria-labelledby="inlagg-rubrik">
      <ListHeader shown={shown} total={rows.length} profiles={profiles} campaigns={campaigns} f={f} />
      <StatusChips rows={rows.filter((r) => inScope(r, f.campaign, f.search))} filter={f.filter} onChange={f.setFilter} />
      {shown === 0 && <EmptyList noClips={clips.length === 0} filtered={f.filtered} />}
      <ul className="space-y-3" aria-label="Inlägg och klipp">
        {rows.map((row) => (
          <ClipRow
            key={`${rowId(row)}-${(row.post ?? row.clip).updated}`}
            row={row}
            info={campaigns[row.clip.profile]}
            estimate={estimates.get(row.clip.id)}
            runId={runIdFor(row.clip)}
            hidden={!shows(row)}
            highlight={highlight === rowId(row)}
            moreOpen={!!more.open[rowId(row)]}
            now={now}
            onUpdate={onUpdate}
            onToggleMore={() => more.toggle(rowId(row))}
          />
        ))}
      </ul>
    </section>
  );
}

/** Postade inlägg och snittvisningar per klippets längd, för vald kampanj. */
function LengthTable({ rows }: { rows: Row[] }) {
  const cell = "py-1.5 text-right tabular-nums";
  return (
    <section aria-labelledby="langd-rubrik" className="rounded-2xl border border-line bg-panel p-4">
      <h2 id="langd-rubrik" className="text-lg font-semibold">
        Längd och visningar
      </h2>
      <p className="text-xs text-muted-foreground">Postade inlägg efter klippets längd.</p>
      <table className="mt-2 w-full max-w-md text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th scope="col" className="py-1.5 text-left font-normal">
              Längd
            </th>
            <th scope="col" className="py-1.5 text-right font-normal">
              Inlägg
            </th>
            <th scope="col" className="py-1.5 text-right font-normal">
              Snittvisningar
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {lengthGroups(rows).map((group) => (
            <tr key={group.label} data-length={group.label}>
              <th scope="row" className="py-1.5 text-left font-medium">
                {group.label}
              </th>
              <td className={cell}>{count.format(group.posts)}</td>
              <td className={cell}>{group.avgViews == null ? "–" : count.format(Math.round(group.avgViews))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Intäkter och budget, under listan. */
function MoneySection({ summary }: { summary: CampaignSummary | null }) {
  if (!summary) return null;
  return (
    <>
      {summary.earnings && <EarningsCards earnings={summary.earnings} />}
      <BudgetSection budget={summary.budget} />
    </>
  );
}

interface Props {
  initialSummary?: CampaignSummary | null;
  initialClips?: CampaignClip[] | null;
  /** ?status= och ?kampanj= ur adressen, lästa på servern så att rätt lista syns direkt. */
  initialFilter?: Filter;
  initialCampaign?: string;
  /** Tiden som Att göra räknar från. Servern skickar den, så att servern och webbläsaren visar samma sak. */
  now?: number;
}

export function ClipLogClient(props: Props) {
  const log = useClipLog(props);
  const runs = useRunLinks();
  const f = useFilters(props);
  const [highlight, setHighlight] = useHighlight();
  const more = useMoreOpen();
  const [now] = useState(() => props.now ?? Date.now());
  const clips = log.clips ?? [];
  const profiles = [...new Set(clips.map((c) => c.profile))].sort();
  const campaigns = useCampaigns(profiles);
  const rows = sortRows(rowsOf(clips), now);

  function refresh() {
    log.load();
    runs.reload();
  }

  function go(item: TodoItem) {
    const row = rows.find((r) => rowId(r) === item.rowId);
    if (!row) return;
    if (!showsIn(f)(row)) f.clear();
    // Vid Analytics inskickad öppnas Fler mått i samma rendering, så att markören hamnar i Tittartid.
    if (item.step === "analytics_inskickad") more.show(rowId(row));
    setHighlight(rowId(row));
  }

  return (
    <main className="w-full max-w-[1180px] space-y-8 px-6 py-9 md:px-11">
      <PageHeader refreshing={log.refreshing} onRefresh={refresh} />
      {log.error && <LoadError error={log.error} onRetry={refresh} />}
      {!log.clips && !log.error && <p className="text-sm text-muted-foreground">Hämtar klipploggen…</p>}

      {log.clips && (
        <>
          <TodoCard items={todoItems(clips, now)} campaigns={campaigns} now={now} onGo={go} />
          <LengthTable rows={rows.filter((r) => inScope(r, f.campaign, ""))} />
          <LogList
            clips={clips}
            rows={rows}
            profiles={profiles}
            campaigns={campaigns}
            f={f}
            earnings={log.summary?.earnings}
            runIdFor={runs.runIdFor}
            highlight={highlight}
            more={more}
            now={now}
            onUpdate={log.update}
          />
        </>
      )}

      <MoneySection summary={log.summary} />
    </main>
  );
}
