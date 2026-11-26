"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Loader2, RotateCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { CampaignTaste as TasteData, Taste, ViralClip } from "@/lib/types";

const num = new Intl.NumberFormat("en-US");
const SOURCES: Record<ViralClip["source"], string> = {
  twitch: "Twitch",
  content_rewards: "Content Rewards",
  link: "Own link",
};
type Busy = "collect" | "link" | "analyse" | null;

const clock = (s: number) => `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
/** Visningarna i fältet: undefined när fältet är tomt, null när det inte är ett heltal 0 eller mer. */
function parseViews(text: string): number | null | undefined {
  if (text.trim() === "") return undefined;
  const n = Number(text);
  return Number.isInteger(n) && n >= 0 ? n : null;
}
/** Källa, skapare och platsen i originalströmmen. */
function whereText(c: ViralClip): string {
  const place = c.location ? `${c.location.vod_id} at ${clock(c.location.start_s)}` : "not found in the stream";
  return [SOURCES[c.source] ?? c.source, c.creator, place].filter(Boolean).join(" · ");
}

/** Visningar och visningar per dag. */
function viewsText(c: ViralClip): string {
  const views = c.views != null ? `${num.format(c.views)} views` : "views unknown";
  return c.views_per_day != null ? `${views} · ${num.format(Math.round(c.views_per_day))}/day` : views;
}

const POLL_MS = 5000;
const message = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong. Try again.");

/** Smaken och klippen från servern. retry laddar om efter ett fel. */
function useTaste(id: string) {
  const [data, setData] = useState<TasteData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

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

  // Ett bakgrundsjobb (länk eller analys) körs: läs smaken var 5:e sekund tills det är klart.
  const running = data?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      api.campaign.profile
        .taste(id)
        .then(setData)
        .catch(() => undefined); // nästa varv försöker igen
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [id, running]);

  return { data, setData, loadError, retry: () => setAttempt((n) => n + 1) };
}

/** Smaken: andras virala klipp, var de kommer ifrån i strömmen och reglerna Claude får när den väljer klipp. */
export function CampaignTaste({ id }: { id: string }) {
  const { data, setData, loadError, retry } = useTaste(id);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);

  /** Ett anrop i taget. true när det gick. */
  async function act(kind: Exclude<Busy, null>, call: () => Promise<TasteData>) {
    setBusy(kind);
    setError(null);
    try {
      const next = await call();
      setData((prev) => ({ ...prev, ...next })); // 202-svaren saknar text och error
      return true;
    } catch (err) {
      setError(message(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <Loading error={loadError} onRetry={retry} />;

  const spin = (kind: Busy) => busy === kind && <Loader2 className="animate-spin" aria-hidden />;
  const { taste, clips, text, running = false, error: jobError } = data;
  const blocked = busy !== null || running;

  return (
    <div className="space-y-8">
      <p className="max-w-[760px] text-sm text-muted-foreground">
        Other people&apos;s viral clips in the niche. The app finds the outliers, finds where each clip comes from in the
        original stream, and works out how it was made. Claude gets the result as taste when it picks clips.
      </p>

      <section className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <Button onClick={() => act("collect", () => api.campaign.profile.collectViral(id))} disabled={blocked}>
          {spin("collect")}
          Fetch viral clips
        </Button>
        <LinkForm
          busy={busy}
          blocked={blocked}
          onAdd={(url, views) => act("link", () => api.campaign.profile.addViralLink(id, url, views))}
        />
        <Button
          variant="outline"
          onClick={() => act("analyse", () => api.campaign.profile.analyseTaste(id))}
          disabled={blocked || clips.length === 0}
        >
          {busy === "analyse" ? spin("analyse") : <Sparkles aria-hidden />}
          Analyse
        </Button>
      </section>
      {running && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Working… Downloading and measuring can take several
          minutes.
        </p>
      )}
      {(error || (!running && jobError)) && (
        <p role="alert" className="max-w-[640px] text-sm text-bad">
          {error || jobError}
        </p>
      )}

      <Outliers clips={clips} />
      <TasteRules taste={taste} text={text} />
    </div>
  );
}

/** Medan smaken laddas, eller felet med Try again. */
function Loading({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  if (error) {
    return (
      <div role="alert" className="max-w-[640px] space-y-3 text-sm">
        <p className="text-bad">{error}</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden />
          Try again
        </Button>
      </div>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden /> Loading the taste…
    </p>
  );
}

/** Fält för en egen länk och visningarna. Fälten töms när klippet är tillagt. */
function LinkForm({
  busy,
  blocked,
  onAdd,
}: {
  busy: Busy;
  blocked: boolean;
  onAdd: (url: string, views?: number) => Promise<boolean>;
}) {
  const [url, setUrl] = useState("");
  const [views, setViews] = useState("");
  const viewCount = parseViews(views);
  const badViews = viewCount === null;

  async function add() {
    if (await onAdd(url.trim(), viewCount ?? undefined)) {
      setUrl("");
      setViews("");
    }
  }

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim() && !badViews) void add();
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
      <Button type="submit" variant="outline" disabled={blocked || !url.trim() || badViews}>
        {busy === "link" && <Loader2 className="animate-spin" aria-hidden />}
        Add
      </Button>
    </form>
  );
}

/** Avvikarna: titel, källa, visningar, visningar per dag, outlier och platsen i strömmen. */
function Outliers({ clips }: { clips: ViralClip[] }) {
  return (
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
                <p className="text-xs text-muted-foreground">{whereText(c)}</p>
              </div>
              <p className="shrink-0 text-xs text-muted-foreground">
                {viewsText(c)}
                {c.outlier != null && <span className="ml-2 font-semibold text-foreground">{c.outlier.toFixed(1)}x</span>}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** Smakreglerna och texten som Claude får. */
function TasteRules({ taste, text }: { taste: Taste | null; text?: string }) {
  return (
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
            <p className="whitespace-pre-line">{text || taste.summary_en}</p>
          </div>
          <p className="text-xs text-muted-foreground">
            From {taste.clip_ids.length} {taste.clip_ids.length === 1 ? "clip" : "clips"}, updated {new Date(taste.updated).toLocaleString("en-GB")}.
          </p>
        </div>
      )}
    </section>
  );
}
