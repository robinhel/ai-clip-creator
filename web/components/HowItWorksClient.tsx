"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Brain,
  Download,
  Layers,
  Mic,
  ShieldCheck,
  Scissors,
  UserCheck,
  type LucideIcon,
} from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { pipelineStats, type PipelineStats } from "@/lib/howItWorks";
import { cn, usd } from "@/lib/utils";
import type { CampaignRunInfo } from "@/lib/types";

type Layer = "core" | "campaign";

interface Step {
  title: string;
  sub: string;
  icon: LucideIcon;
  layer: Layer;
  text: string;
}

const STEPS: Step[] = [
  {
    title: "Download",
    sub: "VOD + chat",
    icon: Download,
    layer: "core",
    text: "The Twitch VOD is downloaded with twitch-dl, and the chat replay is saved next to it. Your own video files skip this step.",
  },
  {
    title: "Transcribe",
    sub: "speech to text",
    icon: Mic,
    layer: "core",
    text: "The audio is cut into parts and transcribed in parallel, with a time stamp on every word. Finished parts are kept, so a stopped run continues where it was.",
  },
  {
    title: "Find signals",
    sub: "chat · audio · hype",
    icon: Activity,
    layer: "core",
    text: "Five signals score every moment: viewer clips, !clip in chat, chat density peaks, hype words in speech and loud audio. Moments close together are merged into one candidate.",
  },
  {
    title: "AI picks",
    sub: "Claude ranks",
    icon: Brain,
    layer: "core",
    text: "The best candidates and their transcript go to Claude, which picks the moments worth a clip, sets start and end, and writes a title.",
  },
  {
    title: "Render 9:16",
    sub: "FFmpeg + facecam",
    icon: Scissors,
    layer: "core",
    text: "FFmpeg cuts each pick and reframes it to vertical 9:16, with the facecam on top and the gameplay below, and burns in word-by-word captions.",
  },
  {
    title: "Overlay + caption",
    sub: "campaign layer",
    icon: Layers,
    layer: "campaign",
    text: "The campaign's overlay and on-screen headline are added, and a caption with the required tags is written for each platform.",
  },
  {
    title: "Rule check",
    sub: "+ frame review",
    icon: ShieldCheck,
    layer: "campaign",
    text: "Every clip is checked against the campaign's rules: length, tags, overlay, safe zone and facecam. A clip that fails is stopped here with the reason.",
  },
  {
    title: "Human yes",
    sub: "then post",
    icon: UserCheck,
    layer: "campaign",
    text: "Nothing is posted without a person saying yes. Approved clips go to the queue for TikTok and YouTube, and their views are logged afterwards.",
  },
];

const CREDIT: Record<Layer, string> = {
  core: "clipsmith core by Ricardo García Ramírez",
  campaign: "Built by Robin",
};

/** The line between two steps, with a dot that runs along it. Down on a narrow screen, right on a wide one. */
function FlowArrow() {
  return (
    <div aria-hidden className="relative mx-auto h-5 w-0.5 shrink-0 overflow-hidden bg-gradient-to-b from-brand to-sky-400 lg:mx-0 lg:h-0.5 lg:w-4 lg:self-center lg:bg-gradient-to-r">
      <span className="flow-dot absolute left-[-2px] h-2 w-1.5 rounded-full bg-white animate-[flow-y_1.6s_linear_infinite] lg:hidden" />
      <span className="flow-dot absolute top-[-2px] hidden h-1.5 w-2 rounded-full bg-white animate-[flow-x_1.6s_linear_infinite] lg:block" />
    </div>
  );
}

function StepButton({ step, index, selected, onSelect }: { step: Step; index: number; selected: boolean; onSelect: () => void }) {
  const Icon = step.icon;
  const own = step.layer === "campaign";
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "group flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors lg:min-w-0 lg:flex-1 lg:flex-col lg:gap-1 lg:px-2 lg:py-3 lg:text-center",
        own ? "border-[#4b3699] bg-gradient-to-b from-[#211a3d] to-panel" : "border-line bg-[#18181d]",
        "hover:border-brand focus-visible:outline-2 focus-visible:outline-brand aria-pressed:border-brand aria-pressed:shadow-[0_0_0_3px_rgba(124,92,255,.25)]"
      )}
    >
      <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg", own ? "bg-brand/25 text-[#c9b6ff]" : "bg-white/5 text-sky-300")}>
        <Icon className="size-[18px]" aria-hidden />
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold leading-tight">
          <span className="mr-1 text-muted-foreground tabular-nums lg:hidden">{index + 1}.</span>
          {step.title}
        </span>
        <span className="block text-[11px] text-muted-foreground">{step.sub}</span>
      </span>
    </button>
  );
}

function Pipeline() {
  const [selected, setSelected] = useState(0);
  const step = STEPS[selected];
  return (
    <section aria-labelledby="pipeline-title" className="rounded-2xl border border-line bg-panel p-4 md:p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pipeline-title" className="font-semibold">
          The pipeline, step by step
        </h2>
        <p className="text-xs text-muted-foreground">Click a step to read what it does.</p>
      </div>
      <ol className="flex flex-col lg:flex-row lg:items-stretch">
        {STEPS.map((s, i) => (
          <li key={s.title} className="contents">
            <div className="lg:flex lg:min-w-0 lg:flex-1">
              <StepButton step={s} index={i} selected={i === selected} onSelect={() => setSelected(i)} />
            </div>
            {i < STEPS.length - 1 && <FlowArrow />}
          </li>
        ))}
      </ol>
      <div
        key={selected}
        aria-live="polite"
        className="mt-4 rounded-xl border border-line bg-background/60 p-4 animate-in fade-in slide-in-from-bottom-1 duration-300"
      >
        <p className="flex flex-wrap items-center gap-2 font-semibold">
          {selected + 1}. {step.title}
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[11px] font-medium",
              step.layer === "campaign" ? "bg-brand/20 text-[#c9b6ff]" : "bg-white/5 text-muted-foreground"
            )}
          >
            {CREDIT[step.layer]}
          </span>
        </p>
        <p className="mt-1.5 max-w-[720px] text-sm text-muted-foreground">{step.text}</p>
      </div>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <i className="inline-block size-2.5 rounded-[3px] border border-line bg-[#18181d]" aria-hidden />
          Steps 1–5: {CREDIT.core}
        </span>
        <span className="flex items-center gap-1.5">
          <i className="inline-block size-2.5 rounded-[3px] border border-[#4b3699] bg-[#211a3d]" aria-hidden />
          Steps 6–8: {CREDIT.campaign}, the campaign layer
        </span>
      </div>
    </section>
  );
}

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

function Kpi({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-[26px] leading-tight font-bold tabular-nums">{value}</p>
      <p className="mt-0.5 text-xs text-good">{detail}</p>
    </div>
  );
}

function Kpis({ stats, latestRun }: { stats: PipelineStats; latestRun: number | null }) {
  if (stats.empty) {
    return (
      <div className="rounded-2xl border border-dashed border-line px-4 py-10 text-center">
        <p className="font-medium">No VODs processed yet</p>
        <p className="mx-auto mt-1 max-w-[420px] text-sm text-muted-foreground">
          Start a project from Home. The numbers for processed VODs, clips and cost show up here when it is done.
        </p>
        <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "mt-4")}>
          Go to Home
        </Link>
      </div>
    );
  }
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="VODs processed" value={String(stats.vods)} detail={`${number.format(stats.hours)} h of video`} />
        <Kpi label="Clips made" value={String(stats.clips)} detail="rendered in 9:16" />
        <Kpi
          label="Approved"
          value={String(stats.approved)}
          detail={stats.clips ? `${Math.round((stats.approved / stats.clips) * 100)} % of the clips` : "no clips yet"}
        />
        <Kpi
          label="AI cost per clip"
          value={stats.costPerClip == null ? "–" : usd.format(stats.costPerClip)}
          detail="this month, budget guard on"
        />
      </div>
      {latestRun != null && (
        <Link href={`/runs/${latestRun}`} className={cn(buttonVariants({ variant: "outline" }), "mt-4")}>
          See the signals in the latest project
          <ArrowRight aria-hidden />
        </Link>
      )}
    </>
  );
}

function useStats() {
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [latestRun, setLatestRun] = useState<number | null>(null);
  useEffect(() => {
    // Each call may fail on its own; a missing server gives the empty view, not an error.
    Promise.all([api.campaign.runs.list().catch(() => [] as CampaignRunInfo[]), api.campaign.summary().catch(() => null)]).then(
      ([runs, summary]) => {
        setStats(pipelineStats(runs, summary));
        const done = runs.filter((r) => r.status === "done" && !r.project_id).sort((a, b) => b.run_id - a.run_id);
        setLatestRun(done[0]?.run_id ?? null);
      }
    );
  }, []);
  return { stats, latestRun };
}

export function HowItWorksClient() {
  const { stats, latestRun } = useStats();
  return (
    <main className="w-full max-w-[1180px] px-4 py-8 md:px-11 md:py-9">
      <p className="text-xs font-semibold tracking-wide text-brand uppercase">How it works</p>
      <h1 className="mt-1 text-[28px] leading-tight font-bold tracking-tight md:text-3xl">
        From a 4-hour stream to ready-to-post Shorts
      </h1>
      <p className="mt-2 mb-6 max-w-[720px] text-muted-foreground">
        The app finds the best moments in a Twitch VOD, cuts them to vertical 9:16, adds the campaign&apos;s overlay and
        caption, checks the campaign&apos;s rules and waits for a human yes before anything is posted.
      </p>
      <Pipeline />
      <section aria-labelledby="numbers-title" className="mt-6">
        <h2 id="numbers-title" className="mb-3 font-semibold">
          What it has done
        </h2>
        {stats ? (
          <Kpis stats={stats} latestRun={latestRun} />
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Loading the numbers">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[98px] animate-pulse rounded-2xl border border-line bg-panel" />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
