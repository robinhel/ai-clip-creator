"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ListChecks, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { ClipCard } from "@/components/ClipCard";
import { ProgressStream } from "@/components/ProgressStream";
import { RUN_STATUS_LABEL } from "@/components/RunCard";
import { api } from "@/lib/api";
import type { CampaignRunInfo, Clip, Run } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;

export default function RunDetailPage() {
  const params = useParams();
  const router = useRouter();
  const runId = Number(params.id);

  const [run, setRun] = useState<Run | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [campaign, setCampaign] = useState<CampaignRunInfo | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isActive = run?.status === "pending" || run?.status === "running";

  const fetchClips = useCallback(async () => {
    try {
      const data = await api.clips.list(runId);
      setClips(data);
    } catch {
      // don't clobber the main error
    }
  }, [runId]);

  const fetchRun = useCallback(async () => {
    try {
      const data = await api.runs.get(runId);
      setRun(data);
      setError(null);
      if (data.status === "done" || data.status === "failed") {
        await fetchClips();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta körningen");
    } finally {
      setLoading(false);
    }
  }, [runId, fetchClips]);

  useEffect(() => {
    fetchRun();
    fetchClips();
    api.campaign.runs
      .list()
      .then((infos) => setCampaign(infos.find((info) => info.run_id === runId) ?? null))
      .catch(() => setCampaign(null));
  }, [fetchRun, fetchClips, runId]);

  // Poll while active, and while the clips are being finished in the background
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!isActive && !finishing) return;
    timerRef.current = setTimeout(async () => {
      await Promise.all([fetchRun(), fetchClips()]);
    }, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [isActive, finishing, run, fetchRun, fetchClips]);

  // Keyboard navigation
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName;
      if (["INPUT", "TEXTAREA", "VIDEO", "BUTTON"].includes(tag)) return;

      switch (e.key) {
        case "ArrowRight":
        case "ArrowDown":
          e.preventDefault();
          setFocusedIndex((i) => Math.min(i + 1, clips.length - 1));
          break;
        case "ArrowLeft":
        case "ArrowUp":
          e.preventDefault();
          setFocusedIndex((i) => Math.max(i - 1, 0));
          break;
        case "j":
        case "J":
          if (clips[focusedIndex]) approveFocused(true);
          break;
        case "k":
        case "K":
          if (clips[focusedIndex]) approveFocused(false);
          break;
        case "?":
          setShowShortcuts((v) => !v);
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips, focusedIndex]);

  async function approveFocused(approved: boolean) {
    const clip = clips[focusedIndex];
    if (!clip) return;
    try {
      const updated = await api.clips.patch(clip.id, { approved });
      handleClipUpdate(updated);
      setFocusedIndex((i) => Math.min(i + 1, clips.length - 1));
    } catch {
      // swallow — user can retry
    }
  }

  async function handleFinish() {
    setFinishError(null);
    try {
      await api.campaign.runs.finish(runId);
      setFinishing(true);
    } catch (err) {
      setFinishError(err instanceof Error ? err.message : "Kunde inte starta");
    }
  }

  function handleSseDone() {
    setFinishing(false);
    setTimeout(() => {
      fetchRun();
      fetchClips();
    }, 500);
  }

  function handleClipUpdate(updated: Clip) {
    setClips((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  }

  if (loading) {
    return (
      <main className="w-full max-w-6xl mx-auto px-4 py-8">
        <p className="text-muted-foreground text-sm">Hämtar körningen…</p>
      </main>
    );
  }

  if (error || !run) {
    return (
      <main className="w-full max-w-6xl mx-auto px-4 py-8 space-y-4">
        <p className="text-destructive">{error ?? "Körningen finns inte"}</p>
        <Button variant="outline" onClick={() => router.push("/")}>
          ← Tillbaka
        </Button>
      </main>
    );
  }

  const statusVariant =
    run.status === "done"
      ? "secondary"
      : run.status === "failed"
        ? "destructive"
        : run.status === "running"
          ? "default"
          : "outline";

  return (
    <main className="w-full max-w-6xl mx-auto px-4 py-8 space-y-6">
      <div className="flex flex-wrap items-start gap-3">
        <Button variant="ghost" size="sm" onClick={() => router.push("/")} aria-label="Tillbaka">
          ←
        </Button>
        <div className="flex-1 min-w-0">
          {campaign && (
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {campaign.profile}
              {campaign.game ? ` · ${campaign.game}` : ""}
            </p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-bold font-mono break-all">{run.vod_id}</h1>
            <Badge variant={statusVariant}>{RUN_STATUS_LABEL[run.status]}</Badge>
          </div>
          {run.channel && (
            <p className="text-sm text-muted-foreground">@{run.channel}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={handleFinish}
            disabled={isActive || finishing}
            title="Lägger på overlay och text, skriver caption och kör regelkontrollen igen"
          >
            <Sparkles />
            {finishing ? "Gör klippen klara…" : "Gör klippen kampanjklara igen"}
          </Button>
          <Link href="/queue" className={buttonVariants()}>
            <ListChecks />
            Till kön
          </Link>
        </div>
      </div>

      {finishError && (
        <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
          {finishError}
        </p>
      )}

      {finishing && !isActive && (
        <div className="rounded-lg border p-4 text-sm">
          <p className="font-medium">Klippen görs kampanjklara i bakgrunden</p>
          <p className="text-muted-foreground mt-1">
            Overlay, text, caption och regelkontroll körs om. Det tar några minuter. Klippen dyker
            upp i <Link href="/queue" className="underline underline-offset-4">kön</Link> när de är klara.
          </p>
        </div>
      )}

      {isActive && (
        <div className="rounded-lg border p-4">
          <p className="text-sm font-medium mb-3">Förlopp</p>
          <ProgressStream runId={runId} onDone={handleSseDone} />
        </div>
      )}

      {run.status === "failed" && run.error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4">
          <p className="text-sm font-medium text-destructive mb-1">Körningen misslyckades</p>
          <p className="text-xs text-muted-foreground font-mono">{run.error}</p>
        </div>
      )}

      {clips.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold">
            Klipp{" "}
            <span className="text-muted-foreground font-normal text-base">
              ({clips.length})
            </span>
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {clips.map((clip, i) => (
              <ClipCard
                key={clip.id}
                clip={clip}
                focused={i === focusedIndex}
                onUpdate={handleClipUpdate}
                onFocus={() => setFocusedIndex(i)}
              />
            ))}
          </div>
        </div>
      )}

      {run.status === "done" && clips.length === 0 && (
        <p className="text-muted-foreground text-sm">
          Körningen blev klar men gav inga klipp.
        </p>
      )}

      {/* Keyboard shortcut help */}
      <div className="fixed bottom-4 right-4">
        <Button
          size="sm"
          variant="outline"
          className="rounded-full w-8 h-8 p-0 text-muted-foreground"
          onClick={() => setShowShortcuts((v) => !v)}
          title="Kortkommandon"
        >
          ?
        </Button>
        {showShortcuts && (
          <div className="absolute bottom-10 right-0 bg-popover border rounded-lg shadow-lg p-3 w-52 text-xs space-y-1.5">
            <p className="font-semibold text-sm mb-2">Kortkommandon</p>
            <div className="grid grid-cols-2 gap-x-2 gap-y-1">
              <span className="font-mono text-muted-foreground">← →</span>
              <span>Byt klipp</span>
              <span className="font-mono text-muted-foreground">J</span>
              <span>Godkänn klipp</span>
              <span className="font-mono text-muted-foreground">K</span>
              <span>Neka klipp</span>
              <span className="font-mono text-muted-foreground">?</span>
              <span>Visa eller dölj hjälpen</span>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
