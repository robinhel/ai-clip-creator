import Link from "next/link";
import { api } from "@/lib/api";
import { serverTime } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignRunInfo, CampaignStatus } from "@/lib/types";

// Samma namn som stegen på projektsidan (GET /campaign/runs/{id}). Kortet vet inte om kampanjen väljer med
// Claude eller med signalerna, så steget heter bara Väljer klipp här.
export const STAGE_LABEL: Record<string, string> = {
  download: "Hämtar VOD:en",
  transcribe: "Transkriberar",
  moments: "Hittar ögonblick",
  select: "Väljer klipp",
  clip: "Klipper",
  finish: "Gör kampanjklart",
};

const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });

export function clipTotal(run: CampaignRunInfo): number {
  return Object.values(run.clip_counts ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
}

export const isActive = (run: CampaignRunInfo) => run.status === "pending" || run.status === "running";

function statusTag(run: CampaignRunInfo): string {
  if (run.cancelled) return "Avbruten";
  if (run.status === "failed") return "Misslyckades";
  if (run.status === "pending") return "I kö";
  if (run.status === "running") {
    const stage = run.stage ? (STAGE_LABEL[run.stage] ?? run.stage) : "Startar";
    return run.pct != null ? `${stage} ${Math.round(run.pct)} %` : stage;
  }
  const n = clipTotal(run);
  return `Klar, ${n} klipp`;
}

/** "2 att granska, 1 att rätta": det som väntar på Robin i projektet. */
function waiting(counts: Partial<Record<CampaignStatus, number>>): string | null {
  const parts = [
    counts.renderad ? `${counts.renderad} att granska` : null,
    counts.underkand ? `${counts.underkand} att rätta` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

function Card({
  href,
  image,
  tag,
  tagTone,
  title,
  meta,
  children,
}: {
  href: string;
  image: React.ReactNode;
  tag: string;
  tagTone: string;
  title: string;
  meta: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="group overflow-hidden rounded-2xl border border-line bg-panel transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand"
    >
      <div className="relative aspect-video bg-[#1f1f26]">
        {image}
        <span className={`absolute top-2.5 left-2.5 rounded-md px-2 py-0.5 text-xs ${tagTone}`}>{tag}</span>
      </div>
      <div className="space-y-1 px-3.5 py-3">
        <p className="line-clamp-2 font-semibold leading-snug break-words">{title}</p>
        <div className="text-[13px] text-muted-foreground">{meta}</div>
        {children}
      </div>
    </Link>
  );
}

/** Twitch-bilden, annars en bild ur projektets första klipp, annars en text. */
function Picture({ src, dim, fallback }: { src: string | null; dim?: boolean; fallback: string }) {
  if (!src) {
    return <div className="grid h-full place-items-center px-3 text-center text-sm text-muted-foreground">{fallback}</div>;
  }
  // eslint-disable-next-line @next/next/no-img-element -- Twitch-bilder och klippets bild, ingen optimering behövs.
  return <img src={src} alt="" className={`h-full w-full object-cover ${dim ? "grayscale-[.6]" : ""}`} />;
}

export function ProjectCard({ run }: { run: CampaignRunInfo }) {
  const active = isActive(run);
  const todo = waiting(run.clip_counts ?? {});
  const meta = [run.channel_name ?? run.profile, run.game, run.created ? day.format(serverTime(run.created)) : null].filter(Boolean);
  const own = !/^\d+$/.test(run.vod_id); // en egen fil har filens namn som VOD-id
  const src = run.thumbnail_url ?? (run.poster_url ? api.campaign.proxied(run.poster_url) : null);
  // Felets första rad, så att Robin ser varför utan att öppna projektet.
  const why = run.status === "failed" && !run.cancelled ? run.error?.split("\n")[0] : null;
  return (
    <Card
      href={`/runs/${run.run_id}`}
      image={<Picture src={src} dim={active} fallback={own ? "Egen fil" : `VOD ${run.vod_id}`} />}
      tag={statusTag(run)}
      tagTone={run.status === "failed" && !run.cancelled ? "bg-bad font-semibold text-white" : "bg-black/70"}
      title={run.title || run.vod_id}
      meta={
        <>
          {meta.join(", ")}
          <span className="block">Kampanj: {run.profile}</span>
        </>
      }
    >
      {todo && <p className="text-[13px] font-medium text-brand">{todo}</p>}
      {why && <p className="line-clamp-2 text-[13px] break-words text-[#fca5a5]">{why}</p>}
      {active && (
        <div
          className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-line"
          role="progressbar"
          aria-label="Förlopp"
          aria-valuenow={Math.round(run.pct ?? 0)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(2, run.pct ?? 0)}%` }} />
        </div>
      )}
    </Card>
  );
}

/** Egna videor i en kampanj. De är inget projekt i servern, men visas som ett bredvid projekten. */
export interface UploadGroup {
  profile: string;
  streamer: string;
  counts: Partial<Record<CampaignStatus, number>>;
  total: number;
  /** Det senaste klippet: bilden på kortet och tiden att sortera efter. */
  latest: CampaignClip;
}

/** Egna videor per kampanj, senaste först. Klippen kommer från GET /campaign/clips?vod_id=upload. */
export function groupUploads(clips: CampaignClip[], profiles: CampaignProfile[]): UploadGroup[] {
  const groups = new Map<string, UploadGroup>();
  for (const clip of clips) {
    const group = groups.get(clip.profile) ?? {
      profile: clip.profile,
      streamer: profiles.find((p) => p.name === clip.profile)?.streamer || clip.profile,
      counts: {},
      total: 0,
      latest: clip,
    };
    group.counts[clip.status] = (group.counts[clip.status] ?? 0) + 1;
    group.total += 1;
    if (clip.id > group.latest.id) group.latest = clip;
    groups.set(clip.profile, group);
  }
  return [...groups.values()].sort((a, b) => b.latest.id - a.latest.id);
}

/** Tiden att sortera efter bland projekten: när klippet eller projektet kom till. */
export const groupTime = (g: UploadGroup) => (g.latest.created ? serverTime(g.latest.created) : 0);
export const runTime = (r: CampaignRunInfo) => (r.created ? serverTime(r.created) : 0);

export function UploadsCard({ group }: { group: UploadGroup }) {
  const todo = waiting(group.counts);
  return (
    <Card
      href={`/uploads?kampanj=${encodeURIComponent(group.profile)}`}
      image={<Picture src={api.campaign.posterUrl(group.latest) ?? null} fallback="Egen video" />}
      tag={`Egna videor, ${group.total} klipp`}
      tagTone="bg-black/70"
      title={`Egna videor för ${group.streamer}`}
      meta={
        <>
          {group.latest.created ? `Senast ${day.format(serverTime(group.latest.created))}` : "Uppladdade videor"}
          <span className="block">Kampanj: {group.profile}</span>
        </>
      }
    >
      {todo && <p className="text-[13px] font-medium text-brand">{todo}</p>}
    </Card>
  );
}
