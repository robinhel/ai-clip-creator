"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StackedPreview } from "@/components/StackedPreview";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignRunInfo, Rect, ReframeInfo } from "@/lib/types";

const W = 1920;
const H = 1080;
const MIN = 50;
const SPLIT_MIN = 0.25;
const SPLIT_MAX = 0.6;

type Kind = "webcam" | "gameplay";
type Corner = "nw" | "ne" | "sw" | "se";
type Drag = { kind: Kind; op: "move" | Corner; startX: number; startY: number; start: Rect };

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

/** Bredd/höjd som rutan måste ha: panelen är 1080 bred och split (eller resten) av 1920 hög. */
function aspectFor(kind: Kind, split: number): number {
  return kind === "webcam" ? 1080 / (1920 * split) : 1080 / (1920 * (1 - split));
}

/** Rutan med rätt form, samma mitt och bredd, inom bilden och minst 50 px åt båda håll. */
function fit([x, y, w, h]: Rect, aspect: number, center = true): Rect {
  const cx = x + w / 2;
  const cy = y + h / 2;
  const minW = Math.max(MIN, MIN * aspect);
  const nw = Math.round(clamp(w, minW, Math.min(W, H * aspect)));
  const nh = Math.min(H, Math.round(nw / aspect));
  const nx = center ? Math.round(cx - nw / 2) : x;
  const ny = center ? Math.round(cy - nh / 2) : y;
  return [clamp(nx, 0, W - nw), clamp(ny, 0, H - nh), nw, nh];
}

/** Ny ruta när hörnet `corner` dras till (px, py). Motsatt hörn står still. */
function resize(start: Rect, corner: Corner, px: number, py: number, aspect: number): Rect {
  const [x, y, w, h] = start;
  const dirX = corner.endsWith("w") ? -1 : 1;
  const dirY = corner.startsWith("n") ? -1 : 1;
  const ax = dirX > 0 ? x : x + w;
  const ay = dirY > 0 ? y : y + h;
  const maxW = Math.min(dirX > 0 ? W - ax : ax, (dirY > 0 ? H - ay : ay) * aspect);
  const minW = Math.max(MIN, MIN * aspect);
  const want = Math.max((px - ax) * dirX, (py - ay) * dirY * aspect);
  const nw = Math.round(Math.max(minW, Math.min(want, maxW)));
  const nh = Math.round(nw / aspect);
  return fit([dirX > 0 ? ax : ax - nw, dirY > 0 ? ay : ay - nh, nw, nh], aspect, false);
}

function defaults(split: number): { webcam: Rect; gameplay: Rect } {
  const gw = Math.round(H * aspectFor("gameplay", split));
  return {
    webcam: fit([1440, 760, 400, 400], aspectFor("webcam", split)),
    gameplay: fit([Math.round((W - gw) / 2), 0, gw, H], aspectFor("gameplay", split)),
  };
}

const STYLE: Record<Kind, { label: string; box: string; handle: string; text: string }> = {
  webcam: { label: "Kamera", box: "border-brand bg-brand/10", handle: "bg-brand", text: "text-brand" },
  gameplay: { label: "Spel", box: "border-good bg-good/10", handle: "bg-good", text: "text-good" },
};

export function CampaignFacecam({ id }: { id: string }) {
  const [server, setServer] = useState<ReframeInfo | null>(null);
  const [split, setSplit] = useState(0.4);
  const [rects, setRects] = useState<Record<Kind, Rect> | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [runs, setRuns] = useState<CampaignRunInfo[]>([]);
  const [vodChoice, setVodChoice] = useState("");
  const [vodInput, setVodInput] = useState("");
  const [loaded, setLoaded] = useState<{ src: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState(false);
  const area = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  function apply(info: ReframeInfo) {
    setServer(info);
    const fallback = defaults(info.split_ratio);
    const next = { webcam: info.webcam_rect ?? fallback.webcam, gameplay: info.gameplay_rect ?? fallback.gameplay };
    setSplit(info.split_ratio);
    setRects(next);
    setSaved(JSON.stringify([info.split_ratio, next]));
  }

  useEffect(() => {
    api.campaign.profile
      .reframe(id)
      .then(apply)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte läsa rutorna"));
    api.campaign.runs
      .list()
      .then((all) => setRuns(all.filter((r) => r.profile === id)))
      .catch(() => {});
  }, [id]);

  const frameSrc = api.campaign.profile.frameUrl(id, vodChoice || undefined);
  const image = loaded?.src !== frameSrc ? "loading" : loaded.ok ? "ok" : "error";

  const dirty = rects !== null && JSON.stringify([split, rects]) !== saved;

  function toSource(e: React.PointerEvent): [number, number] {
    const box = area.current!.getBoundingClientRect();
    return [((e.clientX - box.left) / box.width) * W, ((e.clientY - box.top) / box.height) * H];
  }

  function startDrag(e: React.PointerEvent, kind: Kind, op: Drag["op"]) {
    if (!rects) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const [startX, startY] = toSource(e);
    drag.current = { kind, op, startX, startY, start: rects[kind] };
    setSavedMsg(false);
  }

  function onMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const [px, py] = toSource(e);
    const [x, y, w, h] = d.start;
    const next: Rect =
      d.op === "move"
        ? [clamp(Math.round(x + px - d.startX), 0, W - w), clamp(Math.round(y + py - d.startY), 0, H - h), w, h]
        : resize(d.start, d.op, px, py, aspectFor(d.kind, split));
    setRects((prev) => (prev ? { ...prev, [d.kind]: next } : prev));
  }

  function onKey(e: React.KeyboardEvent, kind: Kind) {
    const step = e.shiftKey ? 1 : 10;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!delta || !rects) return;
    e.preventDefault();
    const [x, y, w, h] = rects[kind];
    setRects({ ...rects, [kind]: [clamp(x + delta[0], 0, W - w), clamp(y + delta[1], 0, H - h), w, h] });
    setSavedMsg(false);
  }

  function changeSplit(value: number) {
    setSplit(value);
    setSavedMsg(false);
    setRects((prev) =>
      prev && {
        webcam: fit(prev.webcam, aspectFor("webcam", value)),
        gameplay: fit(prev.gameplay, aspectFor("gameplay", value)),
      }
    );
  }

  async function save() {
    if (!rects) return;
    setBusy(true);
    setError(null);
    try {
      apply(await api.campaign.profile.saveReframe(id, { webcam_rect: rects.webcam, gameplay_rect: rects.gameplay, split_ratio: split }));
      setSavedMsg(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara rutorna");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Dra den lila ramen över streamerns kamera och den gröna över spelet. Dra i hörnen för att ändra storlek. Formen
        följer panelerna i klippet, så bilden blir aldrig utdragen.
      </p>

      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="space-y-1">
          <span className="block text-[13px] text-muted-foreground">Bild från</span>
          <select
            value={runs.some((r) => r.vod_id === vodChoice) || !vodChoice ? vodChoice : "annat"}
            onChange={(e) => e.target.value !== "annat" && setVodChoice(e.target.value)}
            className="h-9 rounded-lg border border-line bg-panel px-2.5 outline-none focus:border-brand"
          >
            <option value="">Senaste projektet</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.vod_id}>
                VOD {r.vod_id}
                {r.title ? `, ${r.title.slice(0, 40)}` : ""}
              </option>
            ))}
            {vodChoice && !runs.some((r) => r.vod_id === vodChoice) && <option value="annat">VOD {vodChoice}</option>}
          </select>
        </label>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (/^\d{6,15}$/.test(vodInput.trim())) setVodChoice(vodInput.trim());
          }}
        >
          <label className="space-y-1">
            <span className="block text-[13px] text-muted-foreground">Eller ett VOD-id</span>
            <input
              value={vodInput}
              onChange={(e) => setVodInput(e.target.value)}
              inputMode="numeric"
              placeholder="2925991945"
              className="h-9 w-40 rounded-lg border border-line bg-panel px-2.5 tabular-nums outline-none focus:border-brand"
            />
          </label>
          <Button type="submit" variant="outline" className="h-9" disabled={!/^\d{6,15}$/.test(vodInput.trim())}>
            Visa bilden
          </Button>
        </form>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_240px]">
        <div className="space-y-2">
          <div
            ref={area}
            onPointerMove={onMove}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
            className="relative aspect-video w-full touch-none overflow-hidden rounded-xl bg-[#1f1f26] select-none"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- bildrutan ritas i källans koordinater. */}
            <img
              src={frameSrc}
              alt="Bildruta ur VOD:en"
              draggable={false}
              onLoad={() => setLoaded({ src: frameSrc, ok: true })}
              onError={() => setLoaded({ src: frameSrc, ok: false })}
              className={cn("absolute inset-0 h-full w-full", image !== "ok" && "invisible")}
            />
            {image !== "ok" && (
              <p className="absolute inset-0 grid place-items-center px-6 text-center text-sm text-muted-foreground">
                {image === "loading"
                  ? "Hämtar en bildruta ur VOD:en…"
                  : "Servern hittade ingen bild för VOD:en. Välj ett annat projekt eller skriv ett VOD-id."}
              </p>
            )}
            {rects &&
              (["gameplay", "webcam"] as const).map((kind) => {
                const [x, y, w, h] = rects[kind];
                const s = STYLE[kind];
                return (
                  <div
                    key={kind}
                    role="group"
                    tabIndex={0}
                    aria-label={`${s.label}ruta. Flytta med piltangenterna, Skift för en pixel i taget.`}
                    data-rect={kind}
                    onPointerDown={(e) => startDrag(e, kind, "move")}
                    onKeyDown={(e) => onKey(e, kind)}
                    className={cn("absolute cursor-move border-2 outline-none focus-visible:ring-2 focus-visible:ring-white", s.box)}
                    style={{ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`, width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%` }}
                  >
                    <span className={cn("absolute top-0 left-0 px-1.5 py-0.5 text-[11px] font-semibold text-white", s.handle)}>{s.label}</span>
                    {(["nw", "ne", "sw", "se"] as const).map((corner) => (
                      <span
                        key={corner}
                        data-corner={corner}
                        onPointerDown={(e) => startDrag(e, kind, corner)}
                        className={cn(
                          "absolute size-3.5 rounded-sm border-2 border-white",
                          s.handle,
                          corner.startsWith("n") ? "-top-2" : "-bottom-2",
                          corner.endsWith("w") ? "-left-2" : "-right-2",
                          corner === "nw" || corner === "se" ? "cursor-nwse-resize" : "cursor-nesw-resize"
                        )}
                      />
                    ))}
                  </div>
                );
              })}
          </div>

          {rects && (
            <div className="grid gap-x-6 gap-y-1 text-[13px] tabular-nums sm:grid-cols-2">
              {(["webcam", "gameplay"] as const).map((kind) => {
                const [x, y, w, h] = rects[kind];
                return (
                  <p key={kind} data-numbers={kind}>
                    <span className={cn("font-medium", STYLE[kind].text)}>{STYLE[kind].label}:</span>{" "}
                    <span className="text-muted-foreground">
                      x {x}, y {y}, b {w}, h {h}
                    </span>
                  </p>
                );
              })}
            </div>
          )}

          <label className="block max-w-md pt-3 text-sm">
            <span className="flex justify-between">
              <span>Kamerans del av höjden</span>
              <span className="tabular-nums text-muted-foreground">{Math.round(split * 100)} %</span>
            </span>
            <input
              type="range"
              min={SPLIT_MIN}
              max={SPLIT_MAX}
              step={0.01}
              value={split}
              onChange={(e) => changeSplit(Number(e.target.value))}
              className="mt-2 w-full accent-[#7c5cff]"
            />
          </label>
        </div>

        <figure>
          <StackedPreview
            src={image === "ok" ? frameSrc : null}
            webcam={rects?.webcam ?? null}
            gameplay={rects?.gameplay ?? null}
            split={split}
          />
          <figcaption className="mt-2 text-xs text-muted-foreground">Så blir klippet: kameran överst och spelet under.</figcaption>
        </figure>
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {savedMsg && !dirty && (
        <p role="status" className="flex items-center gap-2 text-sm text-good">
          <CheckCircle2 className="size-4" aria-hidden />
          Rutorna är sparade. Nya klipp använder dem.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button className="h-9" disabled={!dirty || busy} onClick={save}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          Spara rutorna
        </Button>
        <Button variant="ghost" className="h-9" disabled={!dirty || busy} onClick={() => server && apply(server)}>
          Ångra ändringar
        </Button>
      </div>
    </div>
  );
}
