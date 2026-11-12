import Link from "next/link";
import { serverTime } from "@/lib/utils";
import type { CampaignRunInfo } from "@/lib/types";

// Samma namn som stegen på projektsidan (GET /campaign/runs/{id}).
export const STAGE_LABEL: Record<string, string> = {
  download: "Hämtar VOD:en",
  transcribe: "Transkriberar",
  moments: "Hittar ögonblick",
  select: "Claude väljer",
  clip: "Klipper",
  finish: "Gör kampanjklart",
};

const day = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });

export function clipTotal(run: CampaignRunInfo): number {
  return Object.values(run.clip_counts ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
}

function statusTag(run: CampaignRunInfo): string {
  if (run.status === "failed") return "Misslyckades";
  if (run.status === "pending") return "I kö";
  if (run.status === "running") {
    const stage = run.stage ? (STAGE_LABEL[run.stage] ?? run.stage) : "Startar";
    return run.pct != null ? `${stage} ${Math.round(run.pct)} %` : stage;
  }
  const n = clipTotal(run);
  return `Klar, ${n} klipp`;
}

export function ProjectCard({ run }: { run: CampaignRunInfo }) {
  const active = run.status === "pending" || run.status === "running";
  const meta = [run.channel_name ?? run.profile, run.game, day.format(serverTime(run.created))].filter(Boolean);
  return (
    <Link
      href={`/runs/${run.run_id}`}
      className="group overflow-hidden rounded-2xl border border-line bg-panel transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand"
    >
      <div className="relative aspect-video bg-[#1f1f26]">
        {run.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- Twitch-bilder, ingen bildoptimering behövs.
          <img
            src={run.thumbnail_url}
            alt=""
            className={`h-full w-full object-cover ${active ? "grayscale-[.6]" : ""}`}
          />
        ) : (
          <div className="grid h-full place-items-center text-sm text-muted-foreground tabular-nums">
            VOD {run.vod_id}
          </div>
        )}
        <span
          className={`absolute top-2.5 left-2.5 rounded-md bg-black/70 px-2 py-0.5 text-xs ${
            run.status === "failed" ? "text-bad" : ""
          }`}
        >
          {statusTag(run)}
        </span>
      </div>
      <div className="space-y-1 px-3.5 py-3">
        <p className="line-clamp-2 font-semibold leading-snug break-words">{run.title || run.vod_id}</p>
        <p className="text-[13px] text-muted-foreground">
          {meta.join(", ")}
          <span className="block">Kampanj: {run.profile}</span>
        </p>
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
      </div>
    </Link>
  );
}
