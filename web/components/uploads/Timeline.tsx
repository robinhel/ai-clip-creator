"use client";

import { useRef, useState } from "react";
import { EXTRA_S, formatSeconds, type LengthCheck } from "@/lib/uploads";
import { cn } from "@/lib/utils";

type Handle = "start" | "end";

const STEP = 0.1;
const tenth = (s: number) => Math.round(s * 10) / 10;
const clamp = (t: number, lo: number, hi: number) => Math.min(Math.max(t, lo), hi);
const fmt = (s: number) => `${formatSeconds(s)} s`;

/** Ungefär tio streck, på jämna sekunder. */
function ticksFor(duration: number): number[] {
  const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => duration / s <= 10) ?? 1200;
  return Array.from({ length: Math.floor(duration / step) + 1 }, (_, i) => i * step);
}

interface Props {
  duration: number;
  startS: number;
  /** 0 betyder videons slut. */
  endS: number;
  /** Bildens tid i källvideon. */
  atS: number;
  speed: number;
  min: number;
  max: number | null | undefined;
  check: LengthCheck;
  disabled: boolean;
  /** Start och slut (0: videons slut) och bildens nya tid. */
  onTimes: (startS: number, endS: number, atS: number) => void;
  onAt: (atS: number) => void;
}

/** Där slutet får ligga för kampanjens gränser, givet starten och hastigheten. */
function allowedEnd({ startS, speed, min, max, duration }: Pick<Props, "startS" | "speed" | "min" | "max" | "duration">) {
  const lo = clamp(startS + (min - EXTRA_S) * speed, 0, duration);
  const hi = max == null ? duration : clamp(startS + (max - EXTRA_S) * speed, 0, duration);
  return { lo, hi };
}

const isArrow = (key: string) => key === "ArrowLeft" || key === "ArrowRight";

function LimitStrip({ lo, hi, pct, min, max }: { lo: number; hi: number; pct: (t: number) => string; min: number; max: number | null | undefined }) {
  const limits = max == null ? `at least ${formatSeconds(min)} s` : `${formatSeconds(min)}–${formatSeconds(max)} s`;
  return (
    <div className="mx-2 mt-1.5">
      <div className="relative h-1.5 rounded bg-white/5" aria-hidden>
        {hi > lo && <div data-testid="allowed-end" className="absolute inset-y-0 rounded bg-good/60" style={{ left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})` }} />}
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">Green: where the end can be for the campaign's {limits}.</p>
    </div>
  );
}

/** Tidslinjen för en video: start, slut och markören för bildens tid. */
export function Timeline(props: Props) {
  const { duration, startS, endS, atS, min, max, check, disabled, onTimes, onAt } = props;
  const end = endS > 0 ? endS : duration;
  const total = tenth(duration);
  const track = useRef<HTMLDivElement>(null);
  const drag = useRef<Handle | "seek" | null>(null);
  const [selected, setSelected] = useState<Handle>("start");
  const pct = (t: number) => `${(clamp(t, 0, duration) / duration) * 100}%`;
  const { lo, hi } = allowedEnd(props);

  function setHandle(handle: Handle, t: number) {
    if (handle === "start") {
      const next = tenth(clamp(t, 0, end - STEP));
      onTimes(next, endS, next);
    } else {
      const next = tenth(clamp(t, startS + STEP, total));
      // Längst ut är videons slut, som servern får som 0.
      onTimes(startS, next >= total ? 0 : next, Math.max(next - STEP, startS));
    }
    setSelected(handle);
  }

  function timeAt(clientX: number): number {
    const box = track.current!.getBoundingClientRect();
    return clamp(((clientX - box.left) / box.width) * duration, 0, duration);
  }

  function onPointerDown(e: React.PointerEvent, what: Handle | "seek") {
    if (disabled) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = what;
    if (what === "seek") onAt(tenth(clamp(timeAt(e.clientX), startS, end)));
    else {
      (e.currentTarget as HTMLElement).focus();
      setSelected(what);
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current) return;
    const t = timeAt(e.clientX);
    if (drag.current === "seek") onAt(tenth(clamp(t, startS, end)));
    else setHandle(drag.current, t);
  }

  function onKeyDown(e: React.KeyboardEvent, handle: Handle) {
    if (disabled || !isArrow(e.key)) return;
    e.preventDefault();
    const delta = (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : STEP);
    setHandle(handle, (handle === "start" ? startS : end) + delta);
  }

  const ticks = ticksFor(duration);
  return (
    <div className={cn("select-none", disabled && "opacity-60")}>
      <p className="flex flex-wrap justify-between gap-x-3 text-[13px] text-muted-foreground">
        <span>Start and end</span>
        <span className="tabular-nums">
          Start {fmt(startS)} · End {fmt(end)} · Frame {fmt(atS)}
        </span>
      </p>
      <div
        ref={track}
        data-testid="timeline"
        onPointerDown={(e) => onPointerDown(e, "seek")}
        onPointerMove={onPointerMove}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
        className="relative mx-2 mt-1.5 h-12 cursor-pointer touch-none rounded-lg bg-[#1d1d23]"
      >
        {/* Grått utanför klippet, lila ram runt det som blir klippet. */}
        <div className="absolute inset-y-0 left-0 rounded-l-lg bg-black/55" style={{ width: pct(startS) }} />
        <div className="absolute inset-y-0 right-0 rounded-r-lg bg-black/55" style={{ width: `calc(100% - ${pct(end)})` }} />
        <div className="absolute inset-y-0 border-y-2 border-brand bg-brand/15" style={{ left: pct(startS), width: `calc(${pct(end)} - ${pct(startS)})` }} />
        {ticks.map((t) => (
          <span key={t} className="absolute bottom-0 h-2 w-px bg-white/25" style={{ left: pct(t) }} />
        ))}
        <span data-testid="frame-marker" className="pointer-events-none absolute -inset-y-1 w-0.5 rounded bg-white" style={{ left: pct(atS) }} aria-hidden />
        {(["start", "end"] as const).map((handle) => {
          const t = handle === "start" ? startS : end;
          return (
            <div
              key={handle}
              role="slider"
              tabIndex={disabled ? -1 : 0}
              aria-label={handle === "start" ? "Start" : "End"}
              aria-valuemin={handle === "start" ? 0 : tenth(startS + STEP)}
              aria-valuemax={handle === "start" ? tenth(end - STEP) : total}
              aria-valuenow={tenth(t)}
              aria-valuetext={fmt(t)}
              aria-disabled={disabled}
              onFocus={() => setSelected(handle)}
              onKeyDown={(e) => onKeyDown(e, handle)}
              onPointerDown={(e) => onPointerDown(e, handle)}
              className={cn(
                "absolute -inset-y-1 grid w-3.5 cursor-ew-resize place-items-center rounded-md bg-brand outline-none focus-visible:ring-2 focus-visible:ring-white",
                handle === "start" ? "-translate-x-full" : "",
                selected === handle && "ring-2 ring-white/80"
              )}
              style={{ left: pct(t) }}
            >
              <span className="h-5 w-0.5 rounded bg-white/80" aria-hidden />
            </div>
          );
        })}
      </div>
      <div className="relative mx-2 mt-1 h-4 text-[11px] text-muted-foreground tabular-nums" aria-hidden>
        {ticks.map((t) => (
          <span key={t} className="absolute -translate-x-1/2" style={{ left: pct(t) }}>
            {t}
          </span>
        ))}
      </div>
      <LimitStrip lo={lo} hi={hi} pct={pct} min={min} max={max} />
      <p role={check.ok ? "status" : "alert"} className={cn("mt-2 text-sm", check.ok ? "text-foreground" : "text-[#fca5a5]")}>
        {check.text}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Drag the handles, or choose one and move it with the arrow keys (0.1 s, with Shift 1 s). Click the timeline to show the frame there.
        Start replaces Trim at start.
      </p>
    </div>
  );
}
