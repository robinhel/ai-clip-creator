"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, Film, Plus, Radio, Scissors } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { groupByWeek } from "@/lib/material";
import { cn, duration, serverTime } from "@/lib/utils";
import type { CampaignProfileDetail, MaterialItem, StreamInfo } from "@/lib/types";

const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });
const link = "font-medium text-foreground underline underline-offset-4";

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
  const body = (
    <>
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{item.title || item.vod_id}</p>
        <p className="text-xs text-muted-foreground">
          {item.kind === "twitch" ? "Twitch-stream" : "Egen video"}
          {item.created && `, ${day.format(new Date(item.created))}`}
          {item.duration_s != null && `, ${duration(item.duration_s)}`}
          {item.run_id != null && `, projekt #${item.run_id}`}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {item.has_video && <Tag>Video</Tag>}
        {item.has_transcript ? <Tag tone="good">{item.has_video ? "Transkript" : "Bara transkript"}</Tag> : <Tag tone="muted">Inget transkript</Tag>}
        {item.clips > 0 && <Tag tone="muted">{item.clips} klipp</Tag>}
      </div>
    </>
  );
  return (
    <li data-material={item.vod_id} className="border-b border-line last:border-b-0">
      {item.run_id != null ? (
        <Link
          href={`/runs/${item.run_id}`}
          aria-label={`Öppna projektet för ${item.title || item.vod_id}`}
          className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3 hover:bg-panel/60"
        >
          {body}
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </Link>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">{body}</div>
      )}
      {/* Servern kan inte transkribera en stream för sig. Hämta klipp gör det, och tar fram klipp samtidigt. */}
      {!item.has_transcript && item.kind === "twitch" && (
        <p className="-mt-1.5 pb-3 pl-8 text-xs text-muted-foreground">
          Chatten kan inte leta här än.{" "}
          <Link href={`/?vod=${item.vod_id}`} className={link}>
            Hämta klipp
          </Link>{" "}
          transkriberar streamen.
        </p>
      )}
    </li>
  );
}

/** Kanalens senaste streams, med Hämta klipp eller länken till projektet. */
function Streams({ profile }: { profile: CampaignProfileDetail }) {
  const [streams, setStreams] = useState<StreamInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.campaign
      .streams()
      .then((list) => alive && setStreams(list.filter((s) => s.campaigns.includes(profile.name)).slice(0, 5)))
      .catch((err) => alive && setError(err instanceof Error ? err.message : "Kunde inte hämta streamsen"));
    return () => {
      alive = false;
    };
  }, [profile.name]);

  return (
    <section aria-label="Kanalens streams" className="mb-8">
      <h2 className="mb-2 font-semibold">Senaste streams på twitch.tv/{profile.channel}</h2>
      {error ? (
        <p className="text-sm text-bad">{error}</p>
      ) : streams === null ? (
        <p className="text-sm text-muted-foreground">Hämtar streamsen från Twitch…</p>
      ) : streams.length === 0 ? (
        <p className="text-sm text-muted-foreground">Kanalen har inga sparade streams på Twitch just nu.</p>
      ) : (
        <ul className="rounded-2xl border border-line px-3">
          {streams.map((s) => (
            <li key={s.vod_id} data-stream={s.vod_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-3 last:border-b-0">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{s.title}</p>
                <p className="text-xs text-muted-foreground">
                  {[s.created_at ? day.format(serverTime(s.created_at)) : null, duration(s.duration_s), s.game].filter(Boolean).join(", ")}
                  {s.live && ", sänds nu"}
                </p>
              </div>
              {s.run_id != null ? (
                <Link href={`/runs/${s.run_id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                  Öppna projektet{s.clip_count > 0 ? ` (${s.clip_count} klipp)` : ""}
                </Link>
              ) : (
                <Link href={`/?vod=${s.vod_id}`} className={buttonVariants({ size: "sm" })}>
                  <Scissors aria-hidden />
                  Hämta klipp
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Fliken Material: kanalens streams, och kampanjens streams och egna videor per vecka. */
export function CampaignMaterial({ profile, material, error }: {
  profile: CampaignProfileDetail;
  material: MaterialItem[] | null;
  error: string | null;
}) {
  const { recent, older } = groupByWeek(material ?? [], Date.now());
  const local = profile.source === "local" || !profile.channel;
  const uploads = `/uploads?kampanj=${profile.name}`;
  return (
    <section aria-label="Material" className="max-w-[860px]">
      {!local && <Streams profile={profile} />}

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Material</h2>
          <p className="text-sm text-muted-foreground">
            {local ? "Egna videor" : "Streams och egna videor"} som kampanjen har. Chatten letar bara i dem som har transkript.
          </p>
        </div>
        <Link href={uploads} className={buttonVariants({ variant: "outline" })}>
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
          {local ? (
            <>
              Inget material än.{" "}
              <Link href={uploads} className={link}>
                Lägg till en egen video
              </Link>
              .
            </>
          ) : (
            <>
              Inget material än. Tryck Hämta klipp på en stream ovan, eller{" "}
              <Link href={uploads} className={link}>
                lägg till en egen video
              </Link>
              .
            </>
          )}
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
