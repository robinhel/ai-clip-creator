"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { hms, timelineLayout, type Timeline } from "@/lib/timeline";
import { cn } from "@/lib/utils";

const W = 1000;
const PLOT_H = 120;
// Room above the plot for the pick labels, and below it for hype dots, viewer clips and the time axis.
const TOP = 22;
const MARKS = 14;
const AXIS = 18;
const H = TOP + PLOT_H + MARKS + AXIS;

const SOURCE_LABEL: Record<string, string> = {
  existing_clip: "viewer clip",
  clip_command: "!clip in chat",
  chat_density: "chat peak",
  transcript_hype: "hype words",
  audio_energy: "loud audio",
  scene_change: "picture cut",
  transcript_sample: "sample",
};
const SOURCE_COLOR: Record<string, string> = {
  existing_clip: "#f5b83d",
  clip_command: "#fb923c",
  chat_density: "#7c5cff",
  transcript_hype: "#f472b6",
  audio_energy: "#38bdf8",
  scene_change: "#a3a3a3",
  transcript_sample: "#525252",
};

function Legend({ audio, chat }: { audio: boolean; chat: boolean }) {
  const items: [string, string, string?][] = [
    ["Chat density", "bg-brand"],
    ["Audio energy", "bg-sky-400"],
    ["Hype words", "bg-pink-400 rounded-full"],
    ["Viewer clips", "bg-warn"],
    ["AI pick", "bg-brand/30 border border-brand"],
  ];
  return (
    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
      {items.map(([label, dot]) => (
        <span
          key={label}
          className={cn(
            "flex items-center gap-1.5",
            ((label === "Audio energy" && !audio) || (label === "Chat density" && !chat)) && "line-through opacity-60"
          )}
        >
          <i className={cn("inline-block size-2.5 rounded-[3px]", dot)} aria-hidden />
          {label}
        </span>
      ))}
      {!audio && <span>No audio energy saved for this VOD.</span>}
      {!chat && <span>No chat for this video.</span>}
    </div>
  );
}

/** The SVG drawing: chat area, audio line, hype dots, viewer clip marks, AI picks and the h:mm axis. */
export function TimelineChart({ data }: { data: Timeline }) {
  const layout = useMemo(() => timelineLayout(data, { width: W, height: PLOT_H }), [data]);
  const [hover, setHover] = useState<number | null>(null);
  const shown = hover != null ? layout.picks[hover] : null;
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-[190px] w-full overflow-visible md:h-[210px]"
        role="img"
        aria-label={`Signal timeline with ${data.picks.length} AI picks`}
      >
        <defs>
          <linearGradient id="chat-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#7c5cff" stopOpacity=".5" />
            <stop offset="1" stopColor="#7c5cff" stopOpacity="0" />
          </linearGradient>
        </defs>
        <g transform={`translate(0 ${TOP})`}>
          {layout.ticks.map((t) => (
            <line key={t.label} x1={t.x} x2={t.x} y1={0} y2={PLOT_H} stroke="#26262d" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          <line x1={0} x2={W} y1={PLOT_H} y2={PLOT_H} stroke="#26262d" vectorEffect="non-scaling-stroke" />
          {layout.chatArea && <path d={layout.chatArea} fill="url(#chat-fill)" />}
          {layout.chatLine && <path d={layout.chatLine} fill="none" stroke="#7c5cff" strokeWidth={2} vectorEffect="non-scaling-stroke" />}
          {layout.audioLine && (
            <path d={layout.audioLine} fill="none" stroke="#38bdf8" strokeWidth={1.25} opacity={0.7} vectorEffect="non-scaling-stroke" />
          )}
          {layout.picks.map((p, i) => (
            <rect
              key={i}
              x={p.x}
              y={0}
              width={p.width}
              height={PLOT_H}
              rx={3}
              fill={hover === i ? "rgba(124,92,255,.38)" : "rgba(124,92,255,.2)"}
              stroke="#7c5cff"
              vectorEffect="non-scaling-stroke"
              className="cursor-pointer"
              tabIndex={0}
              aria-label={`${p.title}, ${p.time}`}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            />
          ))}
          {layout.hype.map((x, i) => (
            <rect key={`h${i}`} x={x - 2} y={PLOT_H + 4} width={4} height={6} rx={2} fill="#f472b6" />
          ))}
          {layout.viewerClips.map((x, i) => (
            <rect key={`v${i}`} x={x - 3} y={PLOT_H + 2} width={6} height={10} rx={1.5} fill="#f5b83d" />
          ))}
        </g>
      </svg>
      {/* Text in HTML, so that it does not stretch with the drawing. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[22px]">
        {layout.picks.map((p, i) => (
          <span
            key={i}
            className={cn(
              "absolute top-0.5 hidden max-w-[22%] truncate text-[11px] text-[#c9b6ff]",
              p.showLabel && "sm:block",
              hover != null && hover !== i && "opacity-40"
            )}
            style={p.anchor === "start" ? { left: `${(p.x / W) * 100}%` } : { right: `${100 - ((p.x + p.width) / W) * 100}%` }}
          >
            {p.title}
          </span>
        ))}
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[18px] text-[11px] text-muted-foreground tabular-nums">
        {layout.ticks.map((t, i) => (
          <span
            key={t.label}
            className={cn("absolute", i > 0 && "-translate-x-1/2", i % 2 === 1 && "hidden sm:inline")}
            style={{ left: `${(t.x / W) * 100}%` }}
          >
            {t.label}
          </span>
        ))}
      </div>
      {shown && (
        <div
          role="tooltip"
          className="pointer-events-none absolute top-6 z-10 max-w-[260px] rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-lg"
          style={
            shown.anchor === "start"
              ? { left: `${(shown.x / W) * 100}%` }
              : { right: `${100 - ((shown.x + shown.width) / W) * 100}%` }
          }
        >
          <p className="font-semibold text-foreground">{shown.title || "Untitled pick"}</p>
          <p className="mt-0.5 text-muted-foreground tabular-nums">{shown.time}</p>
        </div>
      )}
    </div>
  );
}

/** The top candidates and the signals that scored them, as a stacked bar. */
function Candidates({ data }: { data: Timeline }) {
  const top = [...data.candidates].sort((a, b) => b.score - a.score).slice(0, 5);
  if (!top.length) return null;
  return (
    <div className="mt-5">
      <h3 className="mb-1 text-sm font-semibold">Top candidates, scored by the signals</h3>
      <ul>
        {top.map((c, i) => {
          const total = Object.values(c.breakdown).reduce((a, b) => a + b, 0) || 1;
          return (
            <li key={i} className="grid grid-cols-[64px_minmax(0,1fr)_40px] items-center gap-3 border-t border-line py-2 text-[13px]">
              <span className="text-muted-foreground tabular-nums">{hms((c.start_s + c.end_s) / 2)}</span>
              <div className="min-w-0">
                <p className="truncate text-xs text-muted-foreground">
                  {c.sources.map((s) => SOURCE_LABEL[s] ?? s).join(" · ")}
                </p>
                <div className="mt-1 flex h-2 overflow-hidden rounded-full bg-line" style={{ width: `${Math.max(8, c.score)}%` }}>
                  {Object.entries(c.breakdown).map(([source, n]) => (
                    <i key={source} style={{ width: `${(n / total) * 100}%`, background: SOURCE_COLOR[source] ?? "#9a9aa6" }} />
                  ))}
                </div>
              </div>
              <b className="text-right tabular-nums">{Math.round(c.score)}</b>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The signal timeline for a run. Hidden while it loads and when the run has no saved signals (404), for
 * example a run from before the timeline. Any other error shows a short text.
 */
export function SignalTimeline({ runId }: { runId: number }) {
  const [data, setData] = useState<Timeline | null>(null);
  const [missing, setMissing] = useState<string | null>(null);
  useEffect(() => {
    // A new run: the last run's timeline goes, and a late answer for it is ignored.
    let alive = true;
    setData(null);
    setMissing(null);
    api.campaign.runs
      .timeline(runId)
      .then((t) => alive && setData(t))
      .catch((err: unknown) => {
        if (!alive || (err as { status?: number } | null)?.status === 404) return;
        setMissing("The signal timeline could not be loaded. Reload the page to try again.");
      });
    return () => {
      alive = false;
    };
  }, [runId]);

  if (!data && !missing) return null;
  return (
    <section aria-labelledby={`timeline-${runId}`} className="mt-7 rounded-2xl border border-line bg-panel p-4 md:p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`timeline-${runId}`} className="font-semibold">
          Signal timeline
        </h2>
        <p className="text-xs text-muted-foreground">Where the pipeline found the action, and which moments the AI picked.</p>
      </div>
      {data ? (
        <>
          <TimelineChart data={data} />
          <Legend audio={data.audio_energy.length > 0} chat={data.chat_density.length > 0} />
          <Candidates data={data} />
        </>
      ) : (
        <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-sm text-muted-foreground">{missing}</p>
      )}
    </section>
  );
}
