"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow } from "@/components/ClipResultRow";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignProfile, CampaignRunInfo, CampaignStatus } from "@/lib/types";

const SECTIONS: { status: CampaignStatus; title: string; description: string; empty: string }[] = [
  {
    status: "godkand",
    title: "Att posta",
    description: "Ladda ner filen, posta den med captionen och klistra in länken här.",
    empty: "Inget att posta just nu.",
  },
  {
    status: "postad",
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
        // Formaten och länken till projektet är extra; listan ska visas ändå.
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
    // Senaste projektet för samma VOD och kampanj.
    return runs
      .filter((r) => r.vod_id === clip.vod_id && r.profile === clip.profile)
      .reduce<number | undefined>((latest, r) => Math.max(latest ?? 0, r.run_id), undefined);
  }

  const toReview = clips?.filter((c) => c.status === "renderad" || c.status === "underkand").length ?? 0;

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Att posta</h1>
          <p className="mt-1.5 text-muted-foreground">
            Godkända klipp går härifrån till postning och inskickning. Du granskar klippen på{" "}
            <Link href="/projects" className="text-brand underline-offset-4 hover:underline">
              projektsidan
            </Link>
            {toReview > 0 ? `, där ${toReview} klipp väntar på dig` : ""}.
          </p>
        </div>
        <Button variant="outline" onClick={load} disabled={refreshing}>
          <RefreshCw className={refreshing ? "animate-spin" : undefined} aria-hidden />
          Uppdatera
        </Button>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4 text-sm text-[#fca5a5]"
        >
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={load}>
            Försök igen
          </Button>
        </div>
      )}

      {clips === null && !error && <p className="mt-6 text-sm text-muted-foreground">Hämtar klipp…</p>}

      {clips &&
        SECTIONS.map((section) => {
          const items = clips.filter((c) => c.status === section.status);
          return (
            <section key={section.status} className="mt-9">
              <h2 className="text-lg font-semibold">
                {section.title} <span className="font-normal text-muted-foreground">({items.length})</span>
              </h2>
              <p className="text-sm text-muted-foreground">{section.description}</p>
              {items.length === 0 ? (
                <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">
                  {section.empty}
                </p>
              ) : (
                items.map((clip, i) => (
                  <ClipResultRow
                    // Ny nyckel när servern har sparat, så att fälten visar det sparade.
                    key={`${clip.id}-${clip.updated}`}
                    clip={clip}
                    number={i + 1}
                    formats={profiles.find((p) => p.name === clip.profile)?.formats ?? []}
                    runId={runIdFor(clip)}
                    onUpdate={handleUpdate}
                  />
                ))
              )}
            </section>
          );
        })}
    </main>
  );
}
