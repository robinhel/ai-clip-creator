"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FlipHorizontal2, Loader2, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StackedPreview } from "@/components/StackedPreview";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignRunInfo, Rect, ReframeInfo, StreamInfo } from "@/lib/types";

const W = 1920;
const H = 1080;
const MIN = 50;
const SPLIT_MIN = 0.25;
const SPLIT_MAX = 0.6;
const MAX_CAMS = 4;
const day = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short" });

type Kind = "webcam" | "gameplay";
/** En kamerarutas plats i listan, eller spelrutan. */
type Target = number | "gameplay";
type Rects = { webcams: Rect[]; gameplay: Rect };
type Corner = "nw" | "ne" | "sw" | "se";
type Drag = { target: Target; op: "move" | Corner; startX: number; startY: number; start: Rect };

const kindOf = (t: Target): Kind => (t === "gameplay" ? "gameplay" : "webcam");
const rectOf = (r: Rects, t: Target): Rect => (t === "gameplay" ? r.gameplay : r.webcams[t]);
const withRect = (r: Rects, t: Target, next: Rect): Rects =>
  t === "gameplay" ? { ...r, gameplay: next } : { ...r, webcams: r.webcams.map((c, i) => (i === t ? next : c)) };

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
  webcam: { label: "Camera", box: "border-brand bg-brand/10", handle: "bg-brand", text: "text-brand" },
  gameplay: { label: "Game", box: "border-good bg-good/10", handle: "bg-good", text: "text-good" },
};

interface Props {
  id: string;
  /** Kampanjens Twitch-kanal. Dess streams går att välja som bild. Tom för egna videor. */
  channel?: string;
  onSaved: () => void;
  /** true när rutorna har ändringar som inte är sparade. */
  onDirty?: (dirty: boolean) => void;
}

export function CampaignFacecam({ id, channel, onSaved, onDirty }: Props) {
  const [server, setServer] = useState<ReframeInfo | null>(null);
  const [split, setSplit] = useState(0.4);
  const [rects, setRects] = useState<Rects | null>(null);
  const [sel, setSel] = useState(0);
  const [saved, setSaved] = useState<string>("");
  const [vodChoice, setVodChoice] = useState("");
  const [loaded, setLoaded] = useState<{ src: string; ok: boolean; detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState(false);
  const area = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  function apply(info: ReframeInfo) {
    setServer(info);
    const fallback = defaults(info.split_ratio);
    const webcams = info.webcam_rects?.length ? info.webcam_rects : [info.webcam_rect ?? fallback.webcam];
    const next = { webcams, gameplay: info.gameplay_rect ?? fallback.gameplay };
    setSplit(info.split_ratio);
    setRects(next);
    setSel(0);
    setSaved(JSON.stringify([info.split_ratio, next]));
  }

  useEffect(() => {
    api.campaign.profile
      .reframe(id)
      .then(apply)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the boxes"));
  }, [id]);

  const frameSrc = api.campaign.profile.frameUrl(id, vodChoice || undefined);
  const image = loaded?.src !== frameSrc ? "loading" : loaded.ok ? "ok" : "error";

  const dirty = rects !== null && JSON.stringify([split, rects]) !== saved;
  // Servern har inga rutor än: de förvalda rutorna går att spara utan att flyttas först.
  const unsaved = server !== null && (!server.webcam_rects?.length || !server.gameplay_rect);

  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  /** Bilden kom inte: hämta serverns förklaring, till exempel att kampanjen saknar projekt. */
  function frameFailed(src: string) {
    setLoaded({ src, ok: false });
    fetch(src)
      .then((res) => res.json())
      .then((body) => {
        if (typeof body?.detail === "string") setLoaded((prev) => (prev?.src === src ? { ...prev, detail: body.detail } : prev));
      })
      .catch(() => {});
  }

  function toSource(e: React.PointerEvent): [number, number] {
    const box = area.current!.getBoundingClientRect();
    return [((e.clientX - box.left) / box.width) * W, ((e.clientY - box.top) / box.height) * H];
  }

  function startDrag(e: React.PointerEvent, target: Target, op: Drag["op"]) {
    if (!rects) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const [startX, startY] = toSource(e);
    drag.current = { target, op, startX, startY, start: rectOf(rects, target) };
    if (target !== "gameplay") setSel(target);
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
        : resize(d.start, d.op, px, py, aspectFor(kindOf(d.target), split));
    setRects((prev) => prev && withRect(prev, d.target, next));
  }

  function onKey(e: React.KeyboardEvent, target: Target) {
    const step = e.shiftKey ? 1 : 10;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!delta || !rects) return;
    e.preventDefault();
    const [x, y, w, h] = rectOf(rects, target);
    setRects(withRect(rects, target, [clamp(x + delta[0], 0, W - w), clamp(y + delta[1], 0, H - h), w, h]));
    setSavedMsg(false);
  }

  /** Ny kameraposition efter den valda: i mitten i sidled, eller speglad (x = 1920 - x - b). */
  function newCam(mirror: boolean): Rect | null {
    if (!rects?.webcams[sel]) return null;
    const [x, y, w, h] = rects.webcams[sel];
    return [mirror ? W - x - w : Math.round((W - w) / 2), y, w, h];
  }

  /** Finns rutan redan? Två lika positioner går inte att spara. */
  const taken = (next: Rect | null) => !!next && !!rects?.webcams.some((r) => r.every((v, i) => v === next[i]));
  const copies = [taken(newCam(false)) && "Add camera position", taken(newCam(true)) && "Mirror"].filter(Boolean);

  function addCam(mirror: boolean) {
    const next = newCam(mirror);
    if (!rects || !next || rects.webcams.length >= MAX_CAMS || taken(next)) return;
    setRects({ ...rects, webcams: [...rects.webcams, next] });
    setSel(rects.webcams.length);
    setSavedMsg(false);
  }

  function removeCam(index: number) {
    if (!rects || rects.webcams.length <= 1) return;
    setRects({ ...rects, webcams: rects.webcams.filter((_, i) => i !== index) });
    setSel((s) => (s > index || s === rects.webcams.length - 1 ? Math.max(0, s - 1) : s));
    setSavedMsg(false);
  }

  function changeSplit(value: number) {
    setSplit(value);
    setSavedMsg(false);
    setRects((prev) =>
      prev && {
        webcams: prev.webcams.map((r) => fit(r, aspectFor("webcam", value))),
        gameplay: fit(prev.gameplay, aspectFor("gameplay", value)),
      }
    );
  }

  async function save() {
    if (!rects) return;
    setBusy(true);
    setError(null);
    try {
      apply(await api.campaign.profile.saveReframe(id, { webcam_rects: rects.webcams, gameplay_rect: rects.gameplay, split_ratio: split }));
      setSavedMsg(true);
      onSaved(); // kampanjens problem ändras när rutorna är inställda
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the boxes");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Drag the purple frame over the streamer's camera and the green one over the game. Drag the corners to resize. The shape
        follows the panels in the clip, so the picture is never stretched.
      </p>
      <p className="text-sm text-muted-foreground">
        Does the streamer have the camera in different places? Add one position per place. The app picks the right position for each clip.
      </p>
      {server && server.mode !== "stacked" && (
        <p className="text-sm text-muted-foreground">
          The campaign does not use the boxes today (mode {server.mode} in the config file). When you save, the mode is set to camera
          on top and game below.
        </p>
      )}
      {server && (server.source_width !== W || server.source_height !== H) && (
        <p role="alert" className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          The server measures the boxes in {server.source_width} x {server.source_height}, but the frames here are drawn in {W} x {H}. The boxes
          may end up wrong. Check them in a new clip before you post.
        </p>
      )}

      <FramePicker id={id} channel={channel} vodChoice={vodChoice} onChoose={setVodChoice} />

      {rects && (
        <div className="flex flex-wrap items-center gap-2 text-sm" aria-label="Camera positions">
          {rects.webcams.map((_, i) => (
            <span
              key={i}
              className={cn(
                "inline-flex h-8 items-center rounded-full border text-[13px]",
                i === sel ? "border-brand bg-brand/15 text-foreground" : "border-line text-muted-foreground"
              )}
            >
              <button type="button" aria-pressed={i === sel} onClick={() => setSel(i)} className="h-full px-3 hover:text-foreground">
                Camera {i + 1}
                {i === 0 && <span className="text-muted-foreground"> (default)</span>}
              </button>
              {rects.webcams.length > 1 && (
                <button
                  type="button"
                  aria-label={`Remove camera ${i + 1}`}
                  onClick={() => removeCam(i)}
                  className="-ml-1.5 mr-1 rounded-full p-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              )}
            </span>
          ))}
          <Button
            variant="outline"
            className="h-8"
            disabled={rects.webcams.length >= MAX_CAMS || taken(newCam(false))}
            onClick={() => addCam(false)}
          >
            <Plus aria-hidden />
            Add camera position
          </Button>
          <Button
            variant="outline"
            className="h-8"
            disabled={rects.webcams.length >= MAX_CAMS || taken(newCam(true))}
            onClick={() => addCam(true)}
            title={`Adds camera ${sel + 1} mirrored horizontally`}
          >
            <FlipHorizontal2 aria-hidden />
            Mirror
          </Button>
        </div>
      )}
      {/* Egen rad med fast höjd: texten kommer och går medan en ruta dras, och bilden under får inte hoppa. */}
      {rects && (
        <p aria-live="polite" className="-mt-3 min-h-4 text-xs text-muted-foreground">
          {rects.webcams.length >= MAX_CAMS
            ? `At most ${MAX_CAMS} positions.`
            : copies.length > 0 && `${copies.join(" and ")} gives a box that already exists. Move camera ${sel + 1} first.`}
        </p>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_240px]">
        <div className="space-y-2">
          {/* Ovanför bilden, så att rutorna inte täcker texten. */}
          {image !== "ok" && (
            <p role={image === "error" ? "alert" : "status"} className="text-sm text-muted-foreground">
              {image === "loading"
                ? "Loading a frame from the VOD…"
                : (loaded?.detail ?? "The server found no frame for the VOD. Choose another project or a stream, or enter a VOD ID.")}
            </p>
          )}
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
              alt="Frame from the VOD"
              draggable={false}
              onLoad={() => setLoaded({ src: frameSrc, ok: true })}
              onError={() => frameFailed(frameSrc)}
              className={cn("absolute inset-0 h-full w-full", image !== "ok" && "invisible")}
            />
            {rects &&
              // Spelrutan först och den valda kameran sist, så att den valda ligger överst.
              (["gameplay", ...rects.webcams.map((_, i) => i).filter((i) => i !== sel), sel] as Target[]).map((target) => {
                const [x, y, w, h] = rectOf(rects, target);
                const s = STYLE[kindOf(target)];
                const label = target === "gameplay" ? s.label : `${s.label} ${target + 1}`;
                const dim = target !== "gameplay" && target !== sel;
                return (
                  <div
                    key={target}
                    role="group"
                    tabIndex={0}
                    aria-label={`${label}, box. Move with the arrow keys, Shift for one pixel at a time.`}
                    data-rect={target === "gameplay" ? "gameplay" : `webcam-${target}`}
                    data-selected={target === sel || undefined}
                    onPointerDown={(e) => startDrag(e, target, "move")}
                    onFocus={() => target !== "gameplay" && setSel(target)}
                    onKeyDown={(e) => onKey(e, target)}
                    className={cn(
                      "absolute cursor-move border-2 outline-none focus-visible:ring-2 focus-visible:ring-white",
                      s.box,
                      dim && "border-dashed bg-transparent opacity-70"
                    )}
                    style={{ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%`, width: `${(w / W) * 100}%`, height: `${(h / H) * 100}%` }}
                  >
                    <span className={cn("absolute top-0 left-0 px-1.5 py-0.5 text-[11px] font-semibold text-white", s.handle)}>{label}</span>
                    {!dim && (["nw", "ne", "sw", "se"] as const).map((corner) => (
                      <span
                        key={corner}
                        data-corner={corner}
                        onPointerDown={(e) => startDrag(e, target, corner)}
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
              {([...rects.webcams.map((_, i) => i), "gameplay"] as Target[]).map((target) => {
                const [x, y, w, h] = rectOf(rects, target);
                const s = STYLE[kindOf(target)];
                return (
                  <p key={target} data-numbers={target === "gameplay" ? "gameplay" : `webcam-${target}`}>
                    <span className={cn("font-medium", s.text)}>{target === "gameplay" ? s.label : `${s.label} ${target + 1}`}:</span>{" "}
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
              <span>Camera's share of the height</span>
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

        {/* Mindre på smal skärm, så att Spara inte hamnar långt ner. */}
        <figure className="mx-auto w-full max-w-[200px] lg:max-w-none">
          <StackedPreview
            src={image === "ok" ? frameSrc : null}
            webcam={rects?.webcams[sel] ?? null}
            gameplay={rects?.gameplay ?? null}
            split={split}
          />
          <figcaption className="mt-2 text-xs text-muted-foreground">
            This is the clip with camera {sel + 1}: camera on top and game below.
          </figcaption>
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
          The boxes are saved. The mode is set to camera on top and game below, and new clips use the boxes.
        </p>
      )}
      {/* Första gången måste rutorna jämföras med en riktig bild. Annars sparas de förvalda rutorna blint. */}
      {unsaved && image !== "ok" && (
        <p className="text-sm text-warn">Choose a frame from the stream first. Then you can see the boxes are right before you save.</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button className="h-9" disabled={!(dirty || unsaved) || busy || (unsaved && image !== "ok")} onClick={save}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          Save the boxes
        </Button>
        <Button variant="ghost" className="h-9" disabled={!dirty || busy} onClick={() => server && apply(server)}>
          Undo changes
        </Button>
      </div>
    </div>
  );
}

interface FramePickerProps {
  id: string;
  channel?: string;
  vodChoice: string;
  onChoose: (vod: string) => void;
}

/** Var bilden kommer från: ett projekt, en av kanalens streams eller ett VOD-id. */
/** Kampanjens projekt (null medan de hämtas) och kanalens senaste streams. */
function useFrameSources(id: string, channel?: string) {
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [streams, setStreams] = useState<StreamInfo[]>([]);
  useEffect(() => {
    api.campaign.runs
      .list()
      .then((all) => setRuns(all.filter((r) => r.profile === id)))
      .catch(() => setRuns([]));
    // Kanalens streams, så att Robin inte behöver leta upp ett VOD-id på Twitch.
    if (channel) {
      api.campaign
        .streams()
        .then((all) => setStreams(latestStreams(all, id)))
        .catch(() => {});
    }
  }, [id, channel]);
  return { runs, streams };
}

function FramePicker({ id, channel, vodChoice, onChoose }: FramePickerProps) {
  const { runs, streams } = useFrameSources(id, channel);
  // Utan projekt: visa bilden från kanalens senaste stream direkt.
  const firstStream = runs?.length === 0 ? streams[0]?.vod_id : undefined;
  useEffect(() => {
    if (firstStream && !vodChoice) onChoose(firstStream);
  }, [firstStream, vodChoice, onChoose]);
  const projects = runs ?? [];
  const otherStreams = streams.filter((s) => !projects.some((r) => r.vod_id === s.vod_id));
  const known = [...projects, ...otherStreams].some((x) => x.vod_id === vodChoice);

  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <label className="min-w-0 space-y-1">
        <span className="block text-[13px] text-muted-foreground">Frame from</span>
        <select
          value={known || !vodChoice ? vodChoice : "annat"}
          onChange={(e) => e.target.value !== "annat" && onChoose(e.target.value)}
          // Långa VOD-titlar får inte göra sidan bredare än skärmen.
          className="h-9 w-full max-w-72 truncate rounded-lg border border-line bg-panel px-2.5 outline-none focus:border-brand"
        >
          <FrameOptions projects={projects} streams={otherStreams} other={known ? "" : vodChoice} />
        </select>
      </label>
      <VodIdForm onChoose={onChoose} />
    </div>
  );
}

interface FrameOptionsProps {
  projects: CampaignRunInfo[];
  streams: StreamInfo[];
  /** Ett VOD-id som Robin skrev, som inte finns i listorna. */
  other: string;
}

function FrameOptions({ projects, streams, other }: FrameOptionsProps) {
  return (
    <>
      {projects.length > 0 && <option value="">Latest project</option>}
      {projects.length === 0 && streams.length === 0 && <option value="">No project yet</option>}
      {projects.length > 0 && (
        <optgroup label="Projects">
          {projects.map((r) => (
            <option key={r.run_id} value={r.vod_id}>
              Project #{r.run_id}
              {r.title ? `, ${r.title.slice(0, 40)}` : `, VOD ${r.vod_id}`}
            </option>
          ))}
        </optgroup>
      )}
      {streams.length > 0 && (
        <optgroup label="The channel's streams">
          {streams.map((s) => (
            <option key={s.vod_id} value={s.vod_id}>
              {s.created_at ? `${day.format(new Date(s.created_at))}, ` : ""}
              {s.title.slice(0, 40)}
            </option>
          ))}
        </optgroup>
      )}
      {other && <option value="annat">VOD {other}</option>}
    </>
  );
}

/** Ett VOD-id som Robin skriver själv. */
function VodIdForm({ onChoose }: { onChoose: (vod: string) => void }) {
  const [vodInput, setVodInput] = useState("");
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (/^\d{6,15}$/.test(vodInput.trim())) onChoose(vodInput.trim());
      }}
    >
      <label className="space-y-1">
        <span className="block text-[13px] text-muted-foreground">Or a VOD ID from Twitch</span>
        <input
          value={vodInput}
          onChange={(e) => setVodInput(e.target.value)}
          inputMode="numeric"
          placeholder="2925991945"
          className="h-9 w-40 rounded-lg border border-line bg-panel px-2.5 tabular-nums outline-none focus:border-brand"
        />
      </label>
      <Button type="submit" variant="outline" className="h-9" disabled={!/^\d{6,15}$/.test(vodInput.trim())}>
        Show the frame
      </Button>
    </form>
  );
}

/** Kampanjens 8 senaste streams, nyast först. */
function latestStreams(all: StreamInfo[], id: string): StreamInfo[] {
  return all
    .filter((s) => s.campaigns.includes(id))
    .sort((x, y) => (y.created_at ?? "").localeCompare(x.created_at ?? ""))
    .slice(0, 8);
}
