"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { usd } from "@/lib/utils";
import type { CampaignExamples } from "@/lib/types";

const num = new Intl.NumberFormat("sv-SE");
const PLATFORMS: Record<string, string> = {
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
  instagram: "Instagram Reels",
  x: "X",
  facebook: "Facebook",
};

/** Kampanjens bästa klipp och klippare, från kampanjsidan på Content Rewards. */
export function CampaignExampleClips({ id, link }: { id: string; link?: string | null }) {
  const [data, setData] = useState<CampaignExamples | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    setData(null);
    api.campaign.profile
      .examples(id)
      .then((d) => alive && setData(d))
      .catch((err) => alive && setError(err instanceof Error ? err.message : "Exempelklippen gick inte att hämta."));
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  const settings = `/campaigns/${id}?tab=installningar&del=oversikt#lankar`;
  const linkClass = "text-foreground underline underline-offset-4";

  if (error) {
    return (
      <div role="alert" className="max-w-[640px] space-y-3 text-sm">
        <p className="text-bad">{error}</p>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
            <RotateCw aria-hidden />
            Försök igen
          </Button>
          {link && (
            <a href={link} target="_blank" rel="noreferrer" className={`inline-flex items-center gap-1 ${linkClass}`}>
              Öppna kampanjsidan på Content Rewards <ExternalLink className="size-3.5" aria-hidden />
            </a>
          )}
          <Link href={settings} className={linkClass}>
            Ändra länken under Ställ in
          </Link>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Hämtar klippen från Content Rewards…
      </p>
    );
  }
  if (data.problem) {
    return (
      <p className="max-w-[640px] text-sm text-muted-foreground">
        {data.problem}{" "}
        <Link href={settings} className={linkClass}>
          Öppna Ställ in
        </Link>
      </p>
    );
  }

  return (
    <div className="space-y-8">
      <p className="text-sm text-muted-foreground">
        Förebilder: de klipp i kampanjen som har flest visningar. Hämtas från{" "}
        {data.source && (
          <a href={data.source} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-4">
            kampanjsidan på Content Rewards
          </a>
        )}{" "}
        högst en gång i timmen.
      </p>

      {data.earners.length > 0 && (
        <section>
          <h2 className="mb-3 text-[13px] text-muted-foreground">Klipparna som tjänat mest</h2>
          <ol className="grid gap-3 sm:grid-cols-3">
            {data.earners.map((e) => (
              <li key={e.rank} className="rounded-[10px] border border-line bg-panel px-4 py-3 text-sm">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium">
                    {e.rank}. {e.name}
                  </span>
                  <span className="shrink-0 font-semibold">{usd.format(e.earned_usd)}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {num.format(e.views)} visningar, {e.approved} godkända klipp
                </p>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-[13px] text-muted-foreground">Bästa klippen</h2>
        {data.clips.length === 0 ? (
          <p className="text-sm text-muted-foreground">Kampanjen har inga godkända klipp än.</p>
        ) : (
          <ol className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            {data.clips.map((c) => (
              <li key={c.rank} className="min-w-0 space-y-1.5 text-sm" data-example={c.rank}>
                {c.video_url ? (
                  // Videon spelas från Content Rewards CDN. preload="none": inget laddas förrän du trycker.
                  <video
                    src={c.video_url}
                    poster={c.thumbnail_url ?? undefined}
                    controls
                    preload="none"
                    playsInline
                    className="aspect-[9/16] w-full rounded-[10px] border border-line bg-black object-cover"
                  />
                ) : (
                  <div className="flex aspect-[9/16] w-full items-center justify-center rounded-[10px] border border-line bg-panel px-2 text-center text-xs text-muted-foreground">
                    Videon finns inte att spela här.
                  </div>
                )}
                <p className="font-medium">
                  {c.rank}. {num.format(c.views)} visningar
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {PLATFORMS[c.platform] ?? c.platform}
                  {c.name && ` · ${c.name}`}
                  {c.earner_rank && ` · topp ${c.earner_rank}`}
                </p>
                {c.title && <p className="line-clamp-2 text-xs text-muted-foreground" title={c.title}>{c.title}</p>}
                {c.post_url && (
                  <a
                    href={c.post_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-xs underline underline-offset-4 hover:text-foreground"
                  >
                    Öppna inlägget <ExternalLink className="size-3" aria-hidden />
                  </a>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
