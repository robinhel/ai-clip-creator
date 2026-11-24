import Link from "next/link";
import { DeleteVod } from "@/components/DeleteVod";
import { api } from "@/lib/api";
import { campaignLabel } from "@/lib/campaignName";
import {
  activeFile,
  countClips,
  firstLine,
  isRunning,
  projectError,
  projectMeta,
  projectSize,
  stageText,
  waitingText,
  type ProjectEntry,
} from "@/lib/projects";
import { serverTime } from "@/lib/utils";
import type { CampaignProject, CampaignRunInfo } from "@/lib/types";

const day = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short" });

export const isActive = (run: CampaignRunInfo) => isRunning(run.status);

function statusTag(run: CampaignRunInfo): string {
  if (run.cancelled) return "Cancelled";
  if (run.status === "failed") return "Failed";
  if (run.status === "pending") return "Queued";
  if (run.status === "running") return stageText(run.stage, run.pct);
  return `Done, ${countClips(run.clip_counts)} clips`;
}

function Card({
  href,
  image,
  tag,
  tagTone,
  title,
  meta,
  children,
  footer,
}: {
  href: string;
  image: React.ReactNode;
  tag: string;
  tagTone: string;
  title: string;
  meta: React.ReactNode;
  children?: React.ReactNode;
  /** Knappar under kortet. De ligger utanför länken, så att ett klick inte öppnar projektet. */
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-line bg-panel transition-colors hover:border-[#3a3a44]">
      <Link href={href} className="group flex-1 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand">
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
      {footer && <div className="px-3.5 pb-3">{footer}</div>}
    </div>
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

/** Misslyckades av ett fel, inte för att Robin avbröt. */
const failedByError = (run: CampaignRunInfo) => run.status === "failed" && !run.cancelled;

/** "SkiiMaskTimmy, Bodycam, 17 nov." */
function metaLine(run: CampaignRunInfo): string {
  const created = run.created ? day.format(serverTime(run.created)) : null;
  return [run.channel_name ?? run.profile, run.game, created].filter(Boolean).join(", ");
}

function RunPicture({ run }: { run: CampaignRunInfo }) {
  const own = !/^\d+$/.test(run.vod_id); // en egen fil har filens namn som VOD-id
  const src = run.thumbnail_url ?? (run.poster_url ? api.campaign.proxied(run.poster_url) : null);
  return <Picture src={src} dim={isActive(run)} fallback={own ? "Own file" : `VOD ${run.vod_id}`} />;
}

export function Progress({ pct }: { pct: number | null | undefined }) {
  return (
    <div
      className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-line"
      role="progressbar"
      aria-label="Progress"
      aria-valuenow={Math.round(pct ?? 0)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(2, pct ?? 0)}%` }} />
    </div>
  );
}

/** VOD-filen finns på disken, och projektet använder den inte just nu. */
const canFreeSpace = (run: CampaignRunInfo) => Boolean(run.vod_bytes) && !isActive(run);

export function ProjectCard({ run }: { run: CampaignRunInfo }) {
  const todo = waitingText(run.clip_counts ?? {});
  // Felets första rad, så att Robin ser varför utan att öppna projektet.
  const why = failedByError(run) ? firstLine(run.error) : null;
  return (
    <Card
      href={`/runs/${run.run_id}`}
      image={<RunPicture run={run} />}
      tag={statusTag(run)}
      tagTone={failedByError(run) ? "bg-bad font-semibold text-white" : "bg-black/70"}
      title={run.title || run.vod_id}
      footer={canFreeSpace(run) ? <DeleteVod run={run} small /> : null}
      meta={
        <>
          {metaLine(run)}
          <span className="block">Kampanj: {campaignLabel(run.profile)}</span>
        </>
      }
    >
      {todo && <p className="text-[13px] font-medium text-brand">{todo}</p>}
      {why && <p className="line-clamp-2 text-[13px] break-words text-[#fca5a5]">{why}</p>}
      {isActive(run) && <Progress pct={run.pct} />}
    </Card>
  );
}

/** "Klipper 40 %" medan en fil körs, annars filerna och klippen. */
function projectTag(project: CampaignProject): string {
  const file = activeFile(project);
  if (!file) return projectSize(project);
  return file.status === "pending" ? "Queued" : stageText(file.stage, project.pct ?? file.pct);
}

/** Kampanjens projekt för egna filer och uppladdningar: ett kort i stället för ett per fil. */
export function CampaignProjectCard({ project }: { project: CampaignProject }) {
  const todo = waitingText(project.clip_counts);
  const why = projectError(project);
  const poster = project.poster_url ? api.campaign.proxied(project.poster_url) : null;
  return (
    <Card
      href={`/projects/${project.id}`}
      image={<Picture src={poster} dim={project.active} fallback="Own file" />}
      tag={projectTag(project)}
      tagTone="bg-black/70"
      title={project.title || `Own files for ${project.streamer || project.profile}`}
      meta={
        <>
          {projectMeta(project)}
          <span className="block">Kampanj: {campaignLabel(project.profile)}</span>
        </>
      }
    >
      {todo && <p className="text-[13px] font-medium text-brand">{todo}</p>}
      {why && <p className="line-clamp-2 text-[13px] break-words text-[#fca5a5]">{why}</p>}
      {project.active && <Progress pct={project.pct ?? activeFile(project)?.pct} />}
    </Card>
  );
}

/** Ett kort på Hem och Projekt: en Twitch-körning eller kampanjens projekt för egna filer. */
export function EntryCard({ entry }: { entry: ProjectEntry }) {
  return entry.kind === "run" ? <ProjectCard run={entry.run} /> : <CampaignProjectCard project={entry.project} />;
}
