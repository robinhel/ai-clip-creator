"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Loader2, RotateCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { CampaignTaste as TasteData, ViralClip } from "@/lib/types";

const num = new Intl.NumberFormat("en-US");
const SOURCES: Record<ViralClip["source"], string> = {
  twitch: "Twitch",
  content_rewards: "Content Rewards",
  link: "Own link",
};
type Busy = "collect" | "link" | "analyse" | null;

const clock = (s: number) => `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const message = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong. Try again.");

/** Smaken: andras virala klipp, var de kommer ifrån i strömmen och reglerna Claude får när den väljer klipp. */
export function CampaignTaste({ id }: { id: string }) {
  const [data, setData] = useState<TasteData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState("");
  const [views, setViews] = useState("");

  useEffect(() => {
    let alive = true;
    setLoadError(null);
    setData(null);
    api.campaign.profile
      .taste(id)
      .then((d) => alive && setData(d))
      .catch((err) => alive && setLoadError(message(err)));
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  async function act(kind: Exclude<Busy, null>, call: () => Promise<TasteData>) {
    setBusy(kind);
    setError(null);
    try {
      setData(await call());
      if (kind === "link") {
        setUrl("");
        setViews("");
      }
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(null);
    }
  }

  if (loadError) {
    return (
      <div role="alert" className="max-w-[640px] space-y-3 text-sm">
        <p className="text-bad">{loadError}</p>
        <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
          <RotateCw aria-hidden />
          Try again
        </Button>
      </div>
    );
  }
  if (!data) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading the taste…
      </p>
    );
  }

  const spin = (kind: Busy) => busy === kind && <Loader2 className="animate-spin" aria-hidden />;
  const viewCount = views.trim() === "" ? undefined : Number(views);
  const badViews = viewCount !== undefined && !(Number.isInteger(viewCount) && viewCount >= 0);
  const { taste, clips } = data;

  return (
    <div className="space-y-8">
      <p className="max-w-[760px] text-sm text-muted-foreground">
        Other people&apos;s viral clips in the niche. The app finds the outliers, finds where each clip comes from in the
        original stream, and works out how it was made. Claude gets the result as taste when it picks clips.
      </p>

      <section className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <Button onClick={() => act("collect", () => api.campaign.profile.collectViral(id))} disabled={busy !== null}>
          {spin("collect")}
          Fetch viral clips
        </Button>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (url.trim() && !badViews) act("link", () => api.campaign.profile.addViralLink(id, url.trim(), viewCount));
          }}
        >
          <label className="block space-y-1">
            <span className="block text-[13px] text-muted-foreground">Link to a viral clip</span>
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.tiktok.com/@…" className="w-72" />
          </label>
          <label className="block space-y-1">
            <span className="block text-[13px] text-muted-foreground">Views (optional)</span>
            <Input value={views} onChange={(e) => setViews(e.target.value)} inputMode="numeric" className="w-32" aria-invalid={badViews} />
          </label>
          <Button type="submit" variant="outline" disabled={busy !== null || !url.trim() || badViews}>
            {spin("link")}
            Add
          </Button>
        </form>
        <Button
          variant="outline"
          onClick={() => act("analyse", () => api.campaign.profile.analyseTaste(id))}
          disabled={busy !== null || clips.length === 0}
        >
          {busy === "analyse" ? spin("analyse") : <Sparkles aria-hidden />}
          Analyse
        </Button>
      </section>
      {error && (
        <p role="alert" className="max-w-[640px] text-sm text-bad">
          {error}
        </p>
      )}

      <section>
        <h2 className="mb-3 text-[13px] text-muted-foreground">Outliers</h2>
        {clips.length === 0 ? (
          <p className="text-sm text-muted-foreground">No viral clips yet. Press Fetch viral clips, or add a link.</p>
        ) : (
          <ol className="divide-y divide-line rounded-[10px] border border-line bg-panel">
            {clips.map((c) => (
              <li key={c.id} className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 px-4 py-3 text-sm" data-viral={c.id}>
                <div className="min-w-0 space-y-0.5">
                  <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium hover:underline">
                    <span className="truncate">{c.title || c.url}</span> <ExternalLink className="size-3 shrink-0" aria-hidden />
                  </a>
                  <p className="text-xs text-muted-foreground">
                    {SOURCES[c.source] ?? c.source}
                    {c.creator && ` · ${c.creator}`}
                    {" · "}
                    {c.location ? `${c.location.vod_id} at ${clock(c.location.start_s)}` : "not found in the stream"}
                  </p>
                </div>
                <p className="shrink-0 text-xs text-muted-foreground">
                  {c.views != null ? `${num.format(c.views)} views` : "views unknown"}
                  {c.views_per_day != null && ` · ${num.format(Math.round(c.views_per_day))}/day`}
                  {c.outlier != null && <span className="ml-2 font-semibold text-foreground">{c.outlier.toFixed(1)}x</span>}
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="max-w-[760px]">
        <h2 className="mb-3 text-[13px] text-muted-foreground">Taste rules</h2>
        {!taste ? (
          <p className="text-sm text-muted-foreground">Not analysed yet. Press Analyse when the list has clips.</p>
        ) : (
          <div className="space-y-4 text-sm">
            <ul className="list-disc space-y-1 pl-5">
              {taste.rules.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
            <div className="rounded-[10px] border border-line bg-panel px-4 py-3">
              <h3 className="mb-1 text-[13px] text-muted-foreground">What Claude gets</h3>
              <p className="whitespace-pre-line">{taste.summary_en}</p>
            </div>
            <p className="text-xs text-muted-foreground">
              From {taste.clip_ids.length} clips, updated {new Date(taste.updated).toLocaleString("en-GB")}.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
