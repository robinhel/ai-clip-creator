"use client";

import Link from "next/link";
import { Film, Plus, Radio } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { groupByWeek } from "@/lib/material";
import { cn, duration } from "@/lib/utils";
import type { MaterialItem } from "@/lib/types";

const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });

function Tag({ children, tone = "plain" }: { children: React.ReactNode; tone?: "plain" | "good" | "muted" }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-xs whitespace-nowrap",
        tone === "good" ? "border-good/40 text-good" : tone === "muted" ? "border-line text-muted-foreground" : "border-line text-foreground"
      )}
    >
      {children}
    </span>
  );
}

function Item({ item }: { item: MaterialItem }) {
  const Icon = item.kind === "twitch" ? Radio : Film;
  return (
    <li data-material={item.vod_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-3 last:border-b-0">
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{item.title || item.vod_id}</p>
        <p className="text-xs text-muted-foreground">
          {item.kind === "twitch" ? "Twitch-stream" : "Egen video"}
          {item.created && `, ${day.format(new Date(item.created))}`}
          {item.duration_s != null && `, ${duration(item.duration_s)}`}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {item.has_video && <Tag>Video</Tag>}
        {item.has_transcript ? <Tag tone="good">{item.has_video ? "Transkript" : "Bara transkript"}</Tag> : <Tag tone="muted">Inget transkript</Tag>}
        {item.clips > 0 && <Tag tone="muted">{item.clips} klipp</Tag>}
      </div>
    </li>
  );
}

/** Fliken Material: kampanjens streams och egna videor, per vecka. */
export function CampaignMaterial({ material, error }: { material: MaterialItem[] | null; error: string | null }) {
  const { recent, older } = groupByWeek(material ?? [], Date.now());
  return (
    <section aria-label="Material" className="max-w-[860px]">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Streams och egna videor som kampanjen har. Chatten letar bara i dem som har transkript.
        </p>
        <Link href="/uploads" className={buttonVariants({ variant: "outline" })}>
          <Plus aria-hidden />
          Lägg till egen video
        </Link>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-bad">{error}</p>
      ) : material === null ? (
        <p className="text-sm text-muted-foreground">Hämtar materialet…</p>
      ) : material.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">
          Inget material än. Hämta klipp från en stream på startsidan, eller lägg till en egen video.
        </p>
      ) : (
        <div className="space-y-7">
          {recent.map((group) => {
            const end = new Date(group.start.getFullYear(), group.start.getMonth(), group.start.getDate() + 6);
            return (
              <div key={group.key}>
                <div className="flex items-baseline gap-3 border-b border-line pb-1.5">
                  <h3 className="font-semibold">Vecka {group.week}</h3>
                  <span className="text-xs text-muted-foreground">
                    {day.format(group.start)}–{day.format(end)}
                  </span>
                </div>
                <ul>
                  {group.items.map((item) => (
                    <Item key={item.vod_id} item={item} />
                  ))}
                </ul>
              </div>
            );
          })}
          {older.length > 0 && (
            <details className="rounded-xl border border-line px-4">
              <summary className="cursor-pointer py-3 text-sm text-muted-foreground hover:text-foreground">
                Äldre än 4 veckor ({older.length})
              </summary>
              <ul className="border-t border-line">
                {older.map((item) => (
                  <Item key={item.vod_id} item={item} />
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
