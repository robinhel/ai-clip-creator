"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FlipHorizontal2, Loader2, Plus, X } from "lucide-react";
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
const MAX_CAMS = 4;

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
  webcam: { label: "Kamera", box: "border-brand bg-brand/10", handle: "bg-brand", text: "text-brand" },
  gameplay: { label: "Spel", box: "border-good bg-good/10", handle: "bg-good", text: "text-good" },
};

export function CampaignFacecam({ id, onSaved }: { id: string; onSaved: () => void }) {
  const [server, setServer] = useState<ReframeInfo | null>(null);
  const [split, setSplit] = useState(0.4);
  const [rects, setRects] = useState<Rects | null>(null);
  const [sel, setSel] = useState(0);
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
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte läsa rutorna"));
    api.campaign.runs
      .list()
      .then((all) => setRuns(all.filter((r) => r.profile === id)))
      .catch(() => {});
  }, [id]);

  const frameSrc = api.campaign.profile.frameUrl(id, vodChoice || undefined);
  const image = loaded?.src !== frameSrc ? "loading" : loaded.ok ? "ok" : "error";

  const dirty = rects !== null && JSON.stringify([split, rects]) !== saved;
  // Servern har inga rutor än: de förvalda rutorna går att spara utan att flyttas först.
  const unsaved = server !== null && (!server.webcam_rects?.length || !server.gameplay_rect);

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
  const copies = [taken(newCam(false)) && "Lägg till kameraposition", taken(newCam(true)) && "Spegla"].filter(Boolean);

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
      <p className="text-sm text-muted-foreground">
        Har streamern kameran på olika ställen? Lägg till en position per ställe. Appen väljer rätt position för varje klipp.
      </p>
      {server && server.mode !== "stacked" && (
        <p className="text-sm text-muted-foreground">
          Kampanjen använder inte rutorna i dag (läget {server.mode} i config-filen). När du sparar sätts läget till kameran
          överst och spelet under.
        </p>
      )}
      {server && (server.source_width !== W || server.source_height !== H) && (
        <p role="alert" className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          Servern mäter rutorna i {server.source_width} x {server.source_height}, men ramarna här ritas i {W} x {H}. Rutorna
          kan hamna fel. Kontrollera dem i ett nytt klipp innan du postar.
        </p>
      )}

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

      {rects && (
        <div className="flex flex-wrap items-center gap-2 text-sm" aria-label="Kamerapositioner">
          {rects.webcams.map((_, i) => (
            <span
              key={i}
              className={cn(
                "inline-flex h-8 items-center rounded-full border text-[13px]",
                i === sel ? "border-brand bg-brand/15 text-foreground" : "border-line text-muted-foreground"
              )}
            >
              <button type="button" aria-pressed={i === sel} onClick={() => setSel(i)} className="h-full px-3 hover:text-foreground">
                Kamera {i + 1}
                {i === 0 && <span className="text-muted-foreground"> (förvald)</span>}
              </button>
              {rects.webcams.length > 1 && (
                <button
                  type="button"
                  aria-label={`Ta bort kamera ${i + 1}`}
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
            Lägg till kameraposition
          </Button>
          <Button
            variant="outline"
            className="h-8"
            disabled={rects.webcams.length >= MAX_CAMS || taken(newCam(true))}
            onClick={() => addCam(true)}
            title={`Lägger till kamera ${sel + 1} speglad i sidled`}
          >
            <FlipHorizontal2 aria-hidden />
            Spegla
          </Button>
          {rects.webcams.length >= MAX_CAMS ? (
            <span className="text-xs text-muted-foreground">Högst {MAX_CAMS} positioner.</span>
          ) : (
            copies.length > 0 && (
              <span className="text-xs text-muted-foreground">
                {copies.join(" och ")} ger en ruta som redan finns. Flytta kamera {sel + 1} först.
              </span>
            )
          )}
        </div>
      )}

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
                    aria-label={`${label}, ruta. Flytta med piltangenterna, Skift för en pixel i taget.`}
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
            webcam={rects?.webcams[sel] ?? null}
            gameplay={rects?.gameplay ?? null}
            split={split}
          />
          <figcaption className="mt-2 text-xs text-muted-foreground">
            Så blir klippet med kamera {sel + 1}: kameran överst och spelet under.
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
          Rutorna är sparade. Läget sätts till kameran överst och spelet under, och nya klipp använder rutorna.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button className="h-9" disabled={!(dirty || unsaved) || busy} onClick={save}>
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
