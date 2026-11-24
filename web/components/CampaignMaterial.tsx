"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, Film, Plus, Radio, Scissors } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { groupByWeek } from "@/lib/material";
import { cn, duration, serverTime } from "@/lib/utils";
import type { CampaignProfileDetail, MaterialItem, StreamInfo } from "@/lib/types";

const day = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short" });
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

/** Projekten för materialets körningar: run_id → project_id. Materialet säger bara det senaste projektet med källan. */
function useProjectIds(material: MaterialItem[] | null): Map<number, number> {
  const [ids, setIds] = useState<Map<number, number>>(new Map());
  const hasRuns = material?.some((m) => m.run_id != null) ?? false;
  useEffect(() => {
    if (!hasRuns) return;
    api.campaign.runs
      .list()
      .then((runs) => setIds(new Map(runs.flatMap((r) => (r.project_id != null ? [[r.run_id, r.project_id]] : [])))))
      .catch(() => {}); // utan listan går länken till körningen, som förut
  }, [hasRuns]);
  return ids;
}

/** Projektet för egna filer när materialet hör till ett, annars körningens sida. */
function itemLink(item: MaterialItem, projectIds: Map<number, number>): { href: string; label: string } | null {
  if (item.run_id == null) return null;
  const projectId = projectIds.get(item.run_id);
  if (projectId != null) return { href: `/projects/${projectId}`, label: "project for own files" };
  return { href: `/runs/${item.run_id}`, label: `project #${item.run_id}` };
}

function Item({ item, projectIds }: { item: MaterialItem; projectIds: Map<number, number> }) {
  const target = itemLink(item, projectIds);
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
          {target && `, ${target.label}`}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {item.has_video && <Tag>Video</Tag>}
        {item.has_transcript ? <Tag tone="good">{item.has_video ? "Transcript" : "Transcript only"}</Tag> : <Tag tone="muted">No transcript</Tag>}
        {item.clips > 0 && <Tag tone="muted">{item.clips} klipp</Tag>}
      </div>
    </>
  );
  return (
    <li data-material={item.vod_id} className="border-b border-line last:border-b-0">
      {target ? (
        <Link
          href={target.href}
          aria-label={`Open the project for ${item.title || item.vod_id}`}
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
          The chat cannot search here yet.{" "}
          <Link href={`/?vod=${item.vod_id}`} className={link}>
            Get clips
          </Link>{" "}
          transcribes the stream.
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
      .catch((err) => alive && setError(err instanceof Error ? err.message : "Could not load the streams"));
    return () => {
      alive = false;
    };
  }, [profile.name]);

  return (
    <section aria-label="The channel's streams" className="mb-8">
      <h2 className="mb-2 font-semibold">Latest streams on twitch.tv/{profile.channel}</h2>
      {error ? (
        <p className="text-sm text-bad">{error}</p>
      ) : streams === null ? (
        <p className="text-sm text-muted-foreground">Loading the streams from Twitch…</p>
      ) : streams.length === 0 ? (
        <p className="text-sm text-muted-foreground">The channel has no saved streams on Twitch right now.</p>
      ) : (
        <ul className="rounded-2xl border border-line px-3">
          {streams.map((s) => (
            <li key={s.vod_id} data-stream={s.vod_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-3 last:border-b-0">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{s.title}</p>
                <p className="text-xs text-muted-foreground">
                  {[s.created_at ? day.format(serverTime(s.created_at)) : null, duration(s.duration_s), s.game].filter(Boolean).join(", ")}
                  {s.live && ", live now"}
                </p>
              </div>
              {s.run_id != null ? (
                <Link href={`/runs/${s.run_id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                  Open the project{s.clip_count > 0 ? ` (${s.clip_count} ${s.clip_count === 1 ? "clip" : "clips"})` : ""}
                </Link>
              ) : (
                <Link href={`/?vod=${s.vod_id}`} className={buttonVariants({ size: "sm" })}>
                  <Scissors aria-hidden />
                  Get clips
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
  const projectIds = useProjectIds(material);
  const local = profile.source === "local" || !profile.channel;
  const uploads = `/uploads?kampanj=${profile.name}`;
  return (
    <section aria-label="Material" className="max-w-[860px]">
      {!local && <Streams profile={profile} />}

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Material</h2>
          <p className="text-sm text-muted-foreground">
            {local ? "Uploads" : "Streams and uploads"} the campaign has. The chat only searches the ones with a transcript.
          </p>
        </div>
        <Link href={uploads} className={buttonVariants({ variant: "outline" })}>
          <Plus aria-hidden />
          Add your own video
        </Link>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-bad">{error}</p>
      ) : material === null ? (
        <p className="text-sm text-muted-foreground">Loading the material…</p>
      ) : material.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">
          {local ? (
            <>
              No material yet.{" "}
              <Link href={uploads} className={link}>
                Add your own video
              </Link>
              .
            </>
          ) : (
            <>
              No material yet. Press Get clips on a stream above, or{" "}
              <Link href={uploads} className={link}>
                add your own video
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
                  <h3 className="font-semibold">Week {group.week}</h3>
                  <span className="text-xs text-muted-foreground">
                    {day.format(group.start)}–{day.format(end)}
                  </span>
                </div>
                <ul>
                  {group.items.map((item) => (
                    <Item key={item.vod_id} item={item} projectIds={projectIds} />
                  ))}
                </ul>
              </div>
            );
          })}
          {older.length > 0 && (
            <details className="rounded-xl border border-line px-4">
              <summary className="cursor-pointer py-3 text-sm text-muted-foreground hover:text-foreground">
                Older than 4 weeks ({older.length})
              </summary>
              <ul className="border-t border-line">
                {older.map((item) => (
                  <Item key={item.vod_id} item={item} projectIds={projectIds} />
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
