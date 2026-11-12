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
import { cn, serverTime } from "@/lib/utils";
import type { CampaignClip, CampaignStatus, CampaignSummary } from "@/lib/types";

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

function ClipRow({ clip, onUpdate }: { clip: CampaignClip; onUpdate: (clip: CampaignClip) => void }) {
  const [stats, setStats] = useState<Record<StatKey, string>>(() => ({
    views: clip.views?.toString() ?? "",
    likes: clip.likes?.toString() ?? "",
    comments: clip.comments?.toString() ?? "",
    shares: clip.shares?.toString() ?? "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const posted = !["renderad", "underkand", "godkand", "nekad"].includes(clip.status);
  const dirty = STATS.some(({ key }) => stats[key] !== (clip[key]?.toString() ?? ""));
  const low = clip.engagement != null && clip.engagement_min != null && clip.engagement < clip.engagement_min;

  async function patch(body: Parameters<typeof api.campaign.clips.patch>[1]) {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.campaign.clips.patch(clip.id, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara");
    } finally {
      setBusy(false);
    }
  }

  function saveStats() {
    const body: Partial<Record<StatKey, number>> = {};
    for (const { key, label } of STATS) {
      const text = stats[key].trim();
      if (text === "") continue;
      if (!/^\d+$/.test(text)) {
        setError(`${label}: skriv ett helt tal utan decimaler, till exempel 2500.`);
        return;
      }
      body[key] = Number(text);
    }
    patch(body);
  }

  return (
    <tr className="border-b align-top last:border-0">
      <td className="py-3 pr-4">
        <p className="max-w-64 font-medium leading-snug break-words">{clip.title || "utan titel"}</p>
        <p className="text-xs text-muted-foreground">
          VOD {clip.vod_id} · {date.format(serverTime(clip.created))}
        </p>
        {error && <p className="mt-1 max-w-64 text-xs text-destructive">{error}</p>}
      </td>
      <td className="py-3 pr-4 text-muted-foreground">{clip.profile}</td>
      <td className="py-3 pr-4">
        <CampaignStatusBadge status={clip.status} />
      </td>
      <td className="py-3 pr-4">
        {clip.url ? (
          <a
            href={clip.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 underline underline-offset-4"
          >
            {clip.platform ? PLATFORM_LABEL[clip.platform] : "Länk"}
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
              aria-label={`${label} för klipp ${clip.id}`}
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
              className="h-7 w-24 text-right tabular-nums"
            />
          ) : (
            <span className="text-muted-foreground">–</span>
          )}
        </td>
      ))}
      <td className="py-3 pr-4 text-right tabular-nums">
        {clip.engagement == null ? (
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
            {clip.engagement.toFixed(2)} %
          </span>
        )}
      </td>
      <td className="py-3">
        <div className="flex flex-wrap justify-end gap-1.5">
          {dirty && (
            <Button size="sm" onClick={saveStats} disabled={busy}>
              Spara
            </Button>
          )}
          {(NEXT_STEPS[clip.status] ?? []).map((step) => (
            <Button
              key={step.status}
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => patch({ status: step.status })}
            >
              {step.label}
            </Button>
          ))}
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

  const visible = clips?.filter((c) => !filter || c.status === filter) ?? [];
  const purposes = Object.entries(summary?.budget.by_purpose ?? {});

  return (
    <main className="w-full max-w-6xl mx-auto px-4 py-8 space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Klipplogg</h1>
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
              Alla klipp <span className="font-normal text-muted-foreground">({visible.length})</span>
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
            <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
              <table className="w-full min-w-[64rem] text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pl-4 pr-4 font-medium">Klipp</th>
                    <th className="py-2 pr-4 font-medium">Kampanj</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 font-medium">Plattform</th>
                    {STATS.map(({ key, label }) => (
                      <th key={key} className="py-2 pr-2 font-medium">
                        {label}
                      </th>
                    ))}
                    <th className="py-2 pr-4 text-right font-medium">Engagemang</th>
                    <th className="py-2 pr-4">
                      <span className="sr-only">Åtgärder</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="[&_td:first-child]:pl-4 [&_td:last-child]:pr-4">
                  {visible.map((clip) => (
                    <ClipRow key={`${clip.id}-${clip.updated}`} clip={clip} onUpdate={handleUpdate} />
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
