// The signal timeline on a project page: GET /campaign/runs/{id}/timeline, and where everything goes in the drawing.

/** A candidate moment: the window around it, its score 1–100 and how many events of each signal it holds. */
export interface TimelineCandidate {
  start_s: number;
  end_s: number;
  score: number;
  sources: string[];
  breakdown: Record<string, number>;
}

export interface TimelinePick {
  start_s: number;
  end_s: number;
  title: string;
}

/** GET /campaign/runs/{id}/timeline. Values in chat_density and audio_energy are 0–1. */
export interface Timeline {
  duration_s: number;
  chat_density: [number, number][];
  /** Empty when the audio signal was not computed for the VOD. */
  audio_energy: [number, number][];
  hype: number[];
  viewer_clips: number[];
  candidates: TimelineCandidate[];
  picks: TimelinePick[];
}

export interface PickBox {
  x: number;
  width: number;
  title: string;
  /** "0:41:10–0:41:55" */
  time: string;
  /** Picks in the right part of the VOD have their label to the left, so that it stays inside. */
  anchor: "start" | "end";
  /** False when the label would run into another one; the title then shows on hover. */
  showLabel: boolean;
}

export interface TimelineLayout {
  x: (t: number) => number;
  chatLine: string;
  chatArea: string;
  audioLine: string;
  hype: number[];
  viewerClips: number[];
  picks: PickBox[];
  ticks: { x: number; label: string }[];
}

const TICK_STEPS = [60, 300, 600, 900, 1800, 3600, 7200, 14_400];
const MAX_TICKS = 8;
const MIN_PICK_WIDTH = 4;
// A label is about 6.5 px per letter in a plot about 900 px wide, at most 22 % of it (max-w-[22%]).
const LABEL_UNITS_PER_CHAR = 7.2;
const LABEL_MAX = 0.22;
const LABEL_GAP = 12;

/** "h:mm", as on the time axis. */
export function hm(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

/** "h:mm:ss" */
export function hms(seconds: number): string {
  const s = Math.floor(seconds);
  return `${hm(s)}:${String(s % 60).padStart(2, "0")}`;
}

const num = (v: number) => String(Math.round(v * 10) / 10);

function line(points: [number, number][], x: (t: number) => number, height: number): string {
  return points.map(([t, v], i) => `${i ? "L" : "M"}${num(x(t))},${num(height - v * height)}`).join(" ");
}

/** Where everything in the timeline goes, in a plot `width` wide and `height` high. */
export function timelineLayout(data: Timeline, { width, height }: { width: number; height: number }): TimelineLayout {
  const d = data.duration_s;
  const x = (t: number) => (d > 0 ? (Math.min(Math.max(t, 0), d) / d) * width : 0);
  if (d <= 0) return { x, chatLine: "", chatArea: "", audioLine: "", hype: [], viewerClips: [], picks: [], ticks: [] };

  const chatLine = line(data.chat_density, x, height);
  const chat = data.chat_density;
  const chatArea = chat.length
    ? `${chatLine} L${num(x(chat[chat.length - 1][0]))},${height} L${num(x(chat[0][0]))},${height} Z`
    : "";

  const step = TICK_STEPS.find((s) => Math.floor(d / s) + 1 <= MAX_TICKS) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const ticks = Array.from({ length: Math.floor(d / step) + 1 }, (_, i) => ({ x: x(i * step), label: hm(i * step) }));

  const picks = data.picks.map((p): PickBox => {
    const w = Math.max(MIN_PICK_WIDTH, x(p.end_s) - x(p.start_s));
    const left = Math.min(x(p.start_s), width - w);
    return {
      x: left,
      width: w,
      title: p.title,
      time: `${hms(p.start_s)}–${hms(p.end_s)}`,
      anchor: left + w / 2 > width * 0.7 ? "end" : "start",
      showLabel: true,
    };
  });
  // Left to right: a label that overlaps one already shown is hidden.
  const shown: [number, number][] = [];
  for (const p of [...picks].sort((a, b) => a.x - b.x)) {
    const w = Math.min(p.title.length * LABEL_UNITS_PER_CHAR, width * LABEL_MAX);
    const from = p.anchor === "start" ? p.x : p.x + p.width - w;
    const span: [number, number] = [from - LABEL_GAP, from + w + LABEL_GAP];
    p.showLabel = !shown.some(([a, b]) => span[0] < b && a < span[1]);
    if (p.showLabel) shown.push(span);
  }

  return {
    x,
    chatLine,
    chatArea,
    audioLine: line(data.audio_energy, x, height),
    hype: data.hype.map(x),
    viewerClips: data.viewer_clips.map(x),
    picks,
    ticks,
  };
}
