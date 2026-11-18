"use client";

import { useState } from "react";
import { ClipResultRow } from "@/components/ClipResultRow";
import type { CampaignClip, CampaignFormat, CampaignProfile, CampaignStatus } from "@/lib/types";

// Flikar som på projektsidan, så att ett nekat klipp går att hitta och ångra.
const TABS: { id: string; label: string; statuses: CampaignStatus[] }[] = [
  { id: "granska", label: "Att granska", statuses: ["renderad", "underkand"] },
  { id: "godkanda", label: "Godkända", statuses: ["godkand"] },
  { id: "postade", label: "Postade", statuses: ["postad", "inskickad", "analytics_inskickad", "betald"] },
  { id: "nekade", label: "Nekade", statuses: ["nekad"] },
];

function emptyText(loadError: string | null, tab: string): string {
  if (loadError) return "Klippen går inte att visa just nu.";
  return tab === "granska" ? "Inga egna videor väntar på granskning." : "Inga egna klipp med den statusen.";
}

/** Dina egna klipp: flikarna och klippen i den valda fliken. */
export function OwnClips({
  clips,
  profiles,
  formats,
  loadError,
  onUpdate,
  onReload,
}: {
  clips: CampaignClip[];
  profiles: CampaignProfile[];
  formats: CampaignFormat[];
  loadError: string | null;
  onUpdate: (clip: CampaignClip) => void;
  onReload: () => void;
}) {
  const [tab, setTab] = useState("granska");
  const activeTab = TABS.find((t) => t.id === tab) ?? TABS[0];
  const shown = clips.filter((c) => activeTab.statuses.includes(c.status));
  return (
    <section className="mt-8">
      <h2 className="text-xl font-semibold">Dina egna klipp</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Säg ja till klippet för att skicka det till Att posta. Ett nekat klipp ligger under Nekade, där det går att ångra.
      </p>
      <div role="tablist" aria-label="Visa egna klipp" className="mt-3 flex w-fit max-w-full flex-wrap overflow-hidden rounded-[9px] border border-line text-[13px]">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className="px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
          >
            {t.label} <span className="tabular-nums">{clips.filter((c) => t.statuses.includes(c.status)).length}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          {emptyText(loadError, tab)}
        </p>
      ) : (
        shown.map((clip, index) => (
          <ClipResultRow
            key={`${clip.id}-${clip.updated}`}
            clip={clip}
            number={index + 1}
            formats={profiles.find((p) => p.name === clip.profile)?.formats ?? formats}
            onUpdate={onUpdate}
            onReload={onReload}
          />
        ))
      )}
    </section>
  );
}
