"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import {
  CampaignStatusBadge,
  PLATFORM_LABEL,
  STATUS_LABEL,
  STATUS_ORDER,
} from "@/components/CampaignStatusBadge";
import { api } from "@/lib/api";
import { cn, serverTime, SUMMARY_CHANGED } from "@/lib/utils";
import type { CampaignClip, CampaignStatus, CampaignSummary, ClipPost } from "@/lib/types";

const usd = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "USD" });
const date = new Intl.DateTimeFormat("sv-SE", { dateStyle: "medium" });

const STATS = [
  { key: "views", label: "Visningar" },
  { key: "likes", label: "Gilla" },
  { key: "comments", label: "Kommentarer" },
  { key: "shares", label: "Delningar" },
] as const;
type StatKey = (typeof STATS)[number]["key"];

const NEXT_STEPS: Partial<Record<CampaignStatus, { status: CampaignStatus; label: string }[]>> = {
  postad: [{ status: "inskickad", label: "Inskickad" }],
  inskickad: [
    { status: "analytics_inskickad", label: "Statistik inskickad" },
    { status: "betald", label: "Betald" },
  ],
  analytics_inskickad: [{ status: "betald", label: "Betald" }],
};

// Ett steg bakåt, så att ett felklick går att ångra. Samma steg som servern tillåter (campaign/api.py).
const BACK: Partial<Record<CampaignStatus, CampaignStatus>> = {
  postad: "godkand",
  inskickad: "postad",
  analytics_inskickad: "inskickad",
  betald: "inskickad",
};

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
    ["Utbetalt", usd.format(earnings.paid_usd), "Klipp med status Betald."],
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

/** En rad per inlägg. Ett klipp som inte är postat, eller kommer från en äldre server, har en rad för hela klippet. */
function ClipRow({
  clip,
  post,
  estimate,
  onUpdate,
}: {
  clip: CampaignClip;
  post?: ClipPost;
  estimate?: number;
  onUpdate: (clip: CampaignClip) => void;
}) {
  // Raden visar och ändrar inlägget när det finns, annars klippet.
  const row = post ?? clip;
  const status: CampaignStatus = row.status;
  const [stats, setStats] = useState<Record<StatKey, string>>(() => ({
    views: row.views?.toString() ?? "",
    likes: row.likes?.toString() ?? "",
    comments: row.comments?.toString() ?? "",
    shares: row.shares?.toString() ?? "",
  }));
  const [paid, setPaid] = useState(post?.paid_usd?.toString() ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const posted = !["renderad", "underkand", "godkand", "nekad"].includes(status);
  const paidDirty = !!post && paid.trim() !== (post.paid_usd?.toString() ?? "");
  const dirty = STATS.some(({ key }) => stats[key] !== (row[key]?.toString() ?? "")) || paidDirty;
  const low = row.engagement != null && clip.engagement_min != null && row.engagement < clip.engagement_min;
  const shownEstimate = post ? post.estimate_usd : estimate;
  // Statistik inskickad går först vid kampanjens minsta visningar. Servern säger samma sak om man försöker.
  const belowMin = clip.min_views != null && (row.views ?? 0) < clip.min_views;

  async function patch(body: Parameters<typeof api.campaign.clips.patch>[1]) {
    setBusy(true);
    setError(null);
    try {
      onUpdate(
        post
          ? await api.campaign.posts.patch(post.id, body as Parameters<typeof api.campaign.posts.patch>[1])
          : await api.campaign.clips.patch(clip.id, body)
      );
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara");
    } finally {
      setBusy(false);
    }
  }

  function saveStats() {
    const body: Partial<Record<StatKey | "paid_usd", number | null>> = {};
    for (const { key, label } of STATS) {
      const text = stats[key].trim();
      if (text === "") {
        if (row[key] != null) body[key] = null; // ett tömt fält tar bort talet
        continue;
      }
      if (!/^\d+$/.test(text)) {
        setError(`${label}: skriv ett helt tal utan decimaler, till exempel 2500.`);
        return;
      }
      body[key] = Number(text);
    }
    if (paidDirty) {
      const text = paid.trim().replace(",", ".");
      if (text !== "" && !/^\d+(\.\d{1,2})?$/.test(text)) {
        setError("Betalt: skriv beloppet i dollar, till exempel 12.50.");
        return;
      }
      body.paid_usd = text === "" ? null : Number(text);
    }
    patch(body);
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

  return (
    <tr className="border-b align-top last:border-0">
      <td className="py-3 pr-4">
        <p className="max-w-48 font-medium leading-snug break-words">{clip.title || "utan titel"}</p>
        <p className="text-xs text-muted-foreground">
          {clip.profile} · VOD {clip.vod_id} · {date.format(serverTime(clip.created))}
        </p>
        {error && <p className="mt-1 max-w-48 text-xs text-destructive">{error}</p>}
      </td>
      <td className="py-3 pr-4">
        <CampaignStatusBadge status={status} />
      </td>
      <td className="py-3 pr-4">
        {row.url ? (
          <a
            href={row.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 underline underline-offset-4"
          >
            {row.platform ? PLATFORM_LABEL[row.platform] : "Länk"}
            <ExternalLink className="size-3.5" />
          </a>
        ) : (
          <span className="text-muted-foreground">–</span>
        )}
      </td>
      {STATS.map(({ key, label }) => (
        <td key={key} className="py-3 pr-2">
          {posted ? (
            <Input
              aria-label={`${label} för klipp ${clip.id}${post ? ` på ${PLATFORM_LABEL[post.platform]}` : ""}`}
              type="number"
              min="0"
              step="1"
              inputMode="numeric"
              value={stats[key]}
              onChange={(e) => setStats((prev) => ({ ...prev, [key]: e.target.value }))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && dirty) saveStats();
              }}
              disabled={busy}
              className="h-7 w-[4.5rem] text-right tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            />
          ) : (
            <span className="text-muted-foreground">–</span>
          )}
        </td>
      ))}
      <td className="py-3 pr-4 text-right tabular-nums">
        {row.engagement == null ? (
          <span className="text-muted-foreground">–</span>
        ) : (
          <span
            className={cn(low && "font-medium text-destructive")}
            title={
              clip.engagement_min != null
                ? `Kampanjen kräver minst ${clip.engagement_min.toFixed(2)} %`
                : undefined
            }
          >
            {row.engagement.toFixed(2)} %
          </span>
        )}
      </td>
      <td className="py-3 pr-4 text-right tabular-nums">
        <p data-estimate={clip.id}>{shownEstimate == null ? <span className="text-muted-foreground">–</span> : usd.format(shownEstimate)}</p>
        {/* Vad Whop faktiskt betalade står under uppskattningen, så att de går att jämföra. */}
        {post && (
          <Input
            aria-label={`Betalt i dollar för klipp ${clip.id} på ${PLATFORM_LABEL[post.platform]}`}
            inputMode="decimal"
            placeholder="Betalt"
            value={paid}
            onChange={(e) => setPaid(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && dirty) saveStats();
            }}
            disabled={busy}
            className="mt-1 ml-auto h-7 w-[4.5rem] text-right tabular-nums"
          />
        )}
      </td>
      {/* Knapparna står fast till höger, så att de syns också när tabellen är bredare än sidan. */}
      <td className="sticky right-0 bg-background py-3 pl-2 shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.6)]">
        <div className="flex flex-wrap justify-end gap-1.5">
          {dirty && (
            <Button size="sm" onClick={saveStats} disabled={busy}>
              Spara
            </Button>
          )}
          {(NEXT_STEPS[status] ?? []).map((step) => {
            const locked = step.status === "analytics_inskickad" && belowMin;
            return (
              <Button
                key={step.status}
                size="sm"
                variant="outline"
                disabled={busy || locked}
                title={locked ? `Går först vid ${clip.min_views} visningar` : undefined}
                onClick={() => patch({ status: step.status })}
              >
                {step.label}
              </Button>
            );
          })}
          {/* Ett postat inlägg ångras genom att det tas bort; klippet blir godkänt igen när inget inlägg är kvar. */}
          {post && status === "postad" ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={removePost}>
              Ta bort
            </Button>
          ) : (
            BACK[status] && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch({ status: BACK[status] })}>
                Ångra
              </Button>
            )
          )}
        </div>
      </td>
    </tr>
  );
}

interface Props {
  initialSummary?: CampaignSummary | null;
  initialClips?: CampaignClip[] | null;
}

export function ClipLogClient({ initialSummary = null, initialClips = null }: Props) {
  const [summary, setSummary] = useState<CampaignSummary | null>(initialSummary);
  const [clips, setClips] = useState<CampaignClip[] | null>(initialClips);
  const [filter, setFilter] = useState<CampaignStatus | "">("");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [summaryData, clipData] = await Promise.all([
        api.campaign.summary(),
        api.campaign.clips.list(),
      ]);
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

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    // Antal per status och att göra-listan ändras när ett klipp byter status.
    api.campaign.summary().then(setSummary).catch(() => {});
  }

  // En rad per inlägg, så att TikTok, Reels och Shorts för samma klipp följs var för sig.
  const rows = (clips ?? []).flatMap((clip): { clip: CampaignClip; post?: ClipPost; status: CampaignStatus }[] =>
    clip.posts?.length ? clip.posts.map((post) => ({ clip, post, status: post.status as CampaignStatus })) : [{ clip, post: undefined, status: clip.status }]
  );
  const visible = rows.filter((r) => !filter || r.status === filter);
  const purposes = Object.entries(summary?.budget.by_purpose ?? {});
  const estimates = new Map(summary?.earnings?.per_clip.map((c) => [c.id, c.usd]) ?? []);

  return (
    <main className="w-full max-w-[1180px] space-y-8 px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Statistik</h1>
          <p className="text-sm text-muted-foreground">
            Alla klipp från renderat till betalt, med statistik och budget.
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

      {!summary && !error && <p className="text-sm text-muted-foreground">Hämtar klipploggen…</p>}

      {summary?.earnings && <EarningsCards earnings={summary.earnings} />}

      {summary && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <BudgetCard title="Budget i månaden" spent={summary.budget.month_usd} cap={summary.budget.month_cap_usd} />
          <BudgetCard title="Budget i dag" spent={summary.budget.today_usd} cap={summary.budget.day_cap_usd} />
          <Card className="sm:col-span-2">
            <CardHeader>
              <CardDescription>Klipp per status</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {STATUS_ORDER.map((status) => (
                  <div key={status} className="flex items-center gap-1.5">
                    <CampaignStatusBadge status={status} />
                    <span className="tabular-nums font-medium">{summary.counts[status] ?? 0}</span>
                  </div>
                ))}
              </div>
              {purposes.length > 0 && (
                <p className="mt-4 text-xs text-muted-foreground">
                  Kostnad i månaden:{" "}
                  {purposes
                    .map(([purpose, cost]) => `${purpose} ${usd.format(cost.usd)} (${cost.calls} anrop)`)
                    .join(" · ")}
                </p>
              )}
            </CardContent>
          </Card>
          <Card className="sm:col-span-2 lg:col-span-4">
            <CardHeader>
              <CardTitle>Att göra</CardTitle>
            </CardHeader>
            <CardContent>
              {summary.todo.length === 0 ? (
                <p className="text-sm text-muted-foreground">Inget att göra just nu.</p>
              ) : (
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {summary.todo.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {clips && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-lg font-semibold">
              Alla klipp och inlägg <span className="font-normal text-muted-foreground">({visible.length})</span>
            </h2>
            <NativeSelect
              aria-label="Visa status"
              value={filter}
              onChange={(e) => setFilter(e.target.value as CampaignStatus | "")}
              className="w-48"
            >
              <option value="">Alla statusar</option>
              {STATUS_ORDER.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status]}
                </option>
              ))}
            </NativeSelect>
          </div>
          {visible.length === 0 ? (
            <p className="rounded-xl border border-dashed px-4 py-12 text-center text-sm text-muted-foreground">
              {clips.length === 0 ? "Inga klipp i loggen än. Starta en körning för att få klipp." : "Inga klipp med den statusen."}
            </p>
          ) : (
            <div className="relative overflow-x-auto rounded-xl ring-1 ring-foreground/10">
              <table className="w-full min-w-[56rem] text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pl-4 pr-4 font-medium">Klipp</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 font-medium">Plattform</th>
                    {STATS.map(({ key, label }) => (
                      <th key={key} className="py-2 pr-2 font-medium">
                        {label}
                      </th>
                    ))}
                    <th className="py-2 pr-4 text-right font-medium">Engagemang</th>
                    <th className="py-2 pr-4 text-right font-medium" title="Uppskattad intäkt, och under den vad Whop faktiskt betalade i dollar">
                      Uppskattat
                      <br />
                      och betalt
                    </th>
                    <th className="py-2 pr-4">
                      <span className="sr-only">Åtgärder</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="[&_td:first-child]:pl-4 [&_td:last-child]:pr-4">
                  {visible.map(({ clip, post }) => (
                    <ClipRow
                      key={`${clip.id}-${post?.id ?? "klipp"}-${clip.updated}-${post?.updated ?? ""}`}
                      clip={clip}
                      post={post}
                      estimate={estimates.get(clip.id)}
                      onUpdate={handleUpdate}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
