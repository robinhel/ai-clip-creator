"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NewRunDialog } from "@/components/NewRunDialog";
import { RunCard } from "@/components/RunCard";
import { api } from "@/lib/api";
import type { CampaignRunInfo, Run } from "@/lib/types";

const POLL_INTERVAL_MS = 5000;

function hasActiveRun(runs: Run[]): boolean {
  return runs.some((r) => r.status === "pending" || r.status === "running");
}

export function DashboardClient() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [campaignRuns, setCampaignRuns] = useState<Record<number, CampaignRunInfo>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchRuns = useCallback(async () => {
    try {
      const [data, infos] = await Promise.all([
        api.runs.list(),
        // Kampanjnamnet är extra; listan ska visas även om det anropet fallerar.
        api.campaign.runs.list().catch(() => [] as CampaignRunInfo[]),
      ]);
      setRuns(data);
      setCampaignRuns(Object.fromEntries(infos.map((info) => [info.run_id, info])));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta körningarna");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!hasActiveRun(runs)) return;
    timerRef.current = setTimeout(fetchRuns, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [runs, fetchRuns]);

  function handleCreated(run: Run, info: CampaignRunInfo) {
    setRuns((prev) => [run, ...prev]);
    setCampaignRuns((prev) => ({ ...prev, [run.id]: info }));
  }

  function handleDeleted(id: number) {
    setRuns((prev) => prev.filter((r) => r.id !== id));
  }

  return (
    <main className="w-full max-w-6xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Körningar</h1>
          <p className="text-sm text-muted-foreground">
            Varje körning gör klipp av en VOD för en kampanj.
          </p>
        </div>
        <NewRunDialog onCreated={handleCreated} />
      </div>

      {loading && <p className="text-muted-foreground text-sm">Hämtar körningar…</p>}

      {error && (
        <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </p>
      )}

      {!loading && runs.length === 0 && !error && (
        <div className="rounded-xl border border-dashed py-16 text-center text-muted-foreground">
          <p className="text-lg">Inga körningar än.</p>
          <p className="text-sm mt-1">Klicka på &ldquo;Ny körning&rdquo; för att göra klipp av en VOD.</p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {runs.map((run) => (
          <RunCard key={run.id} run={run} campaign={campaignRuns[run.id]} onDelete={handleDeleted} />
        ))}
      </div>
    </main>
  );
}
