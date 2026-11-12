"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QueueClipCard } from "@/components/QueueClipCard";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignProfile, CampaignRunInfo, CampaignStatus } from "@/lib/types";

const SECTIONS: { status: CampaignStatus; id: string; title: string; description: string; empty: string }[] = [
  {
    status: "renderad",
    id: "vantar",
    title: "Väntar på dig",
    description: "Titta på klippet, läs captionen och säg ja eller nej.",
    empty: "Inga klipp väntar.",
  },
  {
    status: "underkand",
    id: "underkanda",
    title: "Underkända",
    description: "Regelkontrollen hittade problem. Rätta dem eller neka klippet.",
    empty: "Inga underkända klipp.",
  },
  {
    status: "godkand",
    id: "att-posta",
    title: "Att posta",
    description: "Ladda ner filen, posta den med captionen och klistra in länken här.",
    empty: "Inget att posta just nu.",
  },
  {
    status: "postad",
    id: "skicka-in",
    title: "Skicka in",
    description: "Skicka in länken i Content Rewards och markera klippet här.",
    empty: "Inget att skicka in.",
  },
];

export function QueueClient() {
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [runs, setRuns] = useState<CampaignRunInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [clipData, profileData, runData] = await Promise.all([
        api.campaign.clips.list(),
        // Formaten och länken till körningen är extra; kön ska visas ändå.
        api.campaign.profiles().catch(() => [] as CampaignProfile[]),
        api.campaign.runs.list().catch(() => [] as CampaignRunInfo[]),
      ]);
      setClips(clipData);
      setProfiles(profileData);
      setRuns(runData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte hämta klippen");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function handleUpdate(updated: CampaignClip) {
    setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
  }

  function runIdFor(clip: CampaignClip): number | undefined {
    // Senaste körningen för samma VOD och kampanj.
    return runs
      .filter((r) => r.vod_id === clip.vod_id && r.profile === clip.profile)
      .reduce<number | undefined>((latest, r) => Math.max(latest ?? 0, r.run_id), undefined);
  }

  return (
    <main className="w-full max-w-6xl mx-auto px-4 py-8 space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Kön</h1>
          <p className="text-sm text-muted-foreground">
            Klippen går härifrån till postning och inskickning, ett steg i taget.
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

      {clips === null && !error && <p className="text-sm text-muted-foreground">Hämtar klipp…</p>}

      {clips && (
        <>
          <nav aria-label="Delar av kön" className="flex flex-wrap gap-2">
            {SECTIONS.map((section) => {
              const count = clips.filter((c) => c.status === section.status).length;
              return (
                <a
                  key={section.id}
                  href={`#${section.id}`}
                  className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm hover:bg-muted"
                >
                  {section.title}
                  <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums text-muted-foreground">
                    {count}
                  </span>
                </a>
              );
            })}
          </nav>

          {SECTIONS.map((section) => {
            const items = clips.filter((c) => c.status === section.status);
            return (
              <section key={section.id} id={section.id} className="scroll-mt-20 space-y-4">
                <div>
                  <h2 className="text-lg font-semibold">
                    {section.title}{" "}
                    <span className="font-normal text-muted-foreground">({items.length})</span>
                  </h2>
                  <p className="text-sm text-muted-foreground">{section.description}</p>
                </div>
                {items.length === 0 ? (
                  <p className="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                    {section.empty}
                  </p>
                ) : (
                  <div className="grid gap-4 xl:grid-cols-2">
                    {items.map((clip) => (
                      <QueueClipCard
                        // Ny nyckel när servern har sparat, så att fälten visar det sparade.
                        key={`${clip.id}-${clip.updated}`}
                        clip={clip}
                        formats={profiles.find((p) => p.name === clip.profile)?.formats ?? []}
                        runId={runIdFor(clip)}
                        onUpdate={handleUpdate}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </>
      )}
    </main>
  );
}
