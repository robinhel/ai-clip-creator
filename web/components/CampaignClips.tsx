"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ClipResultRow } from "@/components/ClipResultRow";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignProfileDetail, CampaignStatus, MaterialItem } from "@/lib/types";

const FILTERS: { id: string; label: string; statuses?: CampaignStatus[] }[] = [
  { id: "granska", label: "To review", statuses: ["renderad", "underkand"] },
  { id: "godkanda", label: "Approved", statuses: ["godkand"] },
  { id: "postade", label: "Posted", statuses: ["postad", "postad_utan_lank", "inskickad", "analytics_inskickad", "betald"] },
  { id: "alla", label: "All" },
];

/** Fliken Klipp: kampanjens klipp från alla källor, med samma rader som kön. */
export function CampaignClips({ profile, material }: { profile: CampaignProfileDetail; material: MaterialItem[] | null }) {
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState(FILTERS[0]);

  const load = useCallback(() => {
    api.campaign
      .profileClips(profile.name)
      .then((list) => {
        // En äldre server utan filtret profile skickar alla kampanjers klipp.
        setClips(list.filter((c) => c.profile === profile.name));
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the clips"));
  }, [profile.name]);
  useEffect(load, [load]);

  // En länk från chatten (#klipp-41) visar alla klipp och rullar fram klippet.
  useEffect(() => {
    const match = /^#klipp-(\d+)$/.exec(window.location.hash);
    if (!clips || !match) return;
    const clip = clips.find((c) => c.id === Number(match[1]));
    if (clip && !filter.statuses?.includes(clip.status)) setFilter(FILTERS[FILTERS.length - 1]);
    requestAnimationFrame(() => document.getElementById(`klipp-${match[1]}`)?.scrollIntoView({ block: "start" }));
    // Bara när klippen har kommit, inte när Robin byter filter själv.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips === null]);

  const count = (f: (typeof FILTERS)[number]) => clips?.filter((c) => !f.statuses || f.statuses.includes(c.status)).length ?? 0;
  const visible = clips?.filter((c) => !filter.statuses || filter.statuses.includes(c.status)) ?? [];
  const source = (vodId: string) => material?.find((m) => m.vod_id === vodId)?.title || vodId;

  if (error) return <p role="alert" className="text-sm text-bad">{error}</p>;
  if (!clips) return <p className="text-sm text-muted-foreground">Loading the clips…</p>;

  return (
    <section aria-label="The campaign's clips">
      <div role="tablist" aria-label="Show clips" className="flex max-w-full overflow-x-auto rounded-[9px] border border-line text-sm sm:w-fit">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={filter.id === f.id}
            onClick={() => setFilter(f)}
            className="shrink-0 px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
          >
            {f.label} <span className="tabular-nums">{count(f)}</span>
          </button>
        ))}
      </div>
      {visible.length === 0 ? (
        <p className="mt-6 rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">
          {clips.length === 0 ? (
            <>
              The campaign has no clips yet.{" "}
              <Link href={`/campaigns/${profile.name}?tab=skapa`} className="font-medium text-foreground underline underline-offset-4">
                Create clips in the chat
              </Link>
              .
            </>
          ) : (
            "No clips with that status."
          )}
        </p>
      ) : (
        visible.map((clip, i) => (
          // Ny nyckel när servern har sparat, så att fälten visar det sparade.
          <div key={`${clip.id}-${clip.updated}`} id={`klipp-${clip.id}`} className="scroll-mt-24">
            <p className="mt-5 -mb-3 truncate text-xs text-muted-foreground">From: {source(clip.vod_id)}</p>
            <ClipResultRow
              clip={clip}
              number={i + 1}
              formats={profile.formats}
              onUpdate={(updated) => setClips((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null)}
              onReload={load}
            />
          </div>
        ))
      )}
    </section>
  );
}
