"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Pause, Play, Repeat, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { CaptionWords } from "@/components/CaptionWords";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { inRange, lineAt } from "@/lib/words";
import type { CampaignClip, CaptionWord, ClipEditInfo } from "@/lib/types";

type Handle = "in" | "out";

const STEP = 0.1;
const tenth = (s: number) => Math.round(s * 10) / 10;
const sec = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmt = (s: number) => `${sec.format(s)} s`;

// Två decimaler i felet, så att 14,97 s inte visas som 15,0 s när gränsen är 15 s.
const exactSec = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

/** Felet för längden, eller null om den ligger inom kampanjens gränser. Exakt min–max, som servern. */
export function lengthProblem(length: number, min: number | null, max: number | null): string | null {
  if (min != null && length < min) return `Klippet är ${exactSec.format(length)} s. Kampanjen kräver minst ${min} s.`;
  if (max != null && length > max) return `Klippet är ${exactSec.format(length)} s. Kampanjen tillåter högst ${max} s.`;
  return null;
}

interface Props {
  clip: CampaignClip;
  info: ClipEditInfo;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Klippet som servern har gjort klart igen, om tiden ändrades och om orden i undertexten ändrades. */
  onSaved: (clip: CampaignClip, trimmed: boolean, wordsChanged: boolean) => void;
}

type Ready = ClipEditInfo & { original: NonNullable<ClipEditInfo["original"]>; trim: NonNullable<ClipEditInfo["trim"]> };

/** Trimma start och slut och byt rubrik, som i OpusClip. Klippet kan bara bli kortare än råklippet. */
export function ClipEditor(props: Props) {
  const { info, open, onOpenChange } = props;
  if (info.editable && info.original && info.trim) return <Editor {...props} info={info as Ready} />;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 p-6 sm:max-w-[460px]">
        <DialogTitle className="text-lg font-semibold">Klippet går inte att redigera</DialogTitle>
        <DialogDescription>{info.reason ?? "Servern säger inte varför."}</DialogDescription>
        <div className="flex justify-end">
          <Button variant="outline" className="h-9" onClick={() => onOpenChange(false)}>
            Stäng
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Editor({ clip, info, open, onOpenChange, onSaved }: Omit<Props, "info"> & { info: Ready }) {
  const exact = info.original.duration_s;
  const total = tenth(exact);
  const [inS, setIn] = useState(tenth(info.trim.in_s));
  const [outS, setOut] = useState(tenth(info.trim.out_s));
  const [title, setTitle] = useState(info.title);
  const subs = info.subtitles;
  const original = subs?.editable ? subs.words : [];
  const [words, setWords] = useState<CaptionWord[]>(original);
  const [selected, setSelected] = useState<Handle>("in");
  const [now, setNow] = useState(tenth(info.trim.in_s));
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bara första värdet: en ny src skulle ladda om videon vid varje dragning.
  const [startAt] = useState(tenth(info.trim.in_s));
  const video = useRef<HTMLVideoElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const drag = useRef<Handle | "seek" | null>(null);
  // Senaste intervallet för uppspelningen, utan att starta om rutan för varje ändring.
  const range = useRef({ inS, outS, loop });
  range.current = { inS, outS, loop };

  const inChanged = inS !== tenth(info.trim.in_s);
  const outChanged = outS !== tenth(info.trim.out_s);
  const titleChanged = title.trim() !== info.title;
  const wordsChanged = JSON.stringify(words) !== JSON.stringify(original);
  const wordProblem = !wordsChanged
    ? null
    : words.some((w) => !w.word.trim())
      ? "Ett ord i undertexten är tomt. Skriv det eller ta bort det."
      : words.some((w) => w.word.trim().length > 40)
        ? "Ett ord i undertexten får vara högst 40 tecken."
        : null;
  // Slutet längst ut är originalets exakta längd; tidslinjen visar den avrundad till 0,1 s.
  const outExact = outS >= total ? exact : outS;
  const length = Math.round((outExact - inS) * 1000) / 1000;
  const problem = lengthProblem(length, info.min_s, info.max_s);
  // Bara en ny rubrik: servern kontrollerar inte längden, så den stoppar inte heller här.
  const block =
    (inChanged || outChanged ? problem : null) ??
    wordProblem ??
    (!title.trim() ? "Skriv en rubrik." : !inChanged && !outChanged && !titleChanged && !wordsChanged ? "Inget är ändrat." : null);
  // Råklippet saknar undertext, så raden som sägs ritas ovanpå videon.
  const style = subs?.style ?? "box";
  const line = subs?.editable ? lineAt(words.filter((w) => inRange(w, inS, outExact)), now, style) : null;

  // Uppspelningen håller sig inom intervallet. timeupdate kommer för sällan, så rutan följs varje bildruta.
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      const v = video.current;
      if (!v) return;
      const { inS: a, outS: b, loop: again } = range.current;
      if (v.currentTime >= b) {
        v.currentTime = a;
        if (!again) v.pause();
      }
      setNow(tenth(v.currentTime));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  function seek(t: number) {
    const v = video.current;
    if (v) v.currentTime = t;
    setNow(tenth(t));
  }

  function setHandle(handle: Handle, t: number) {
    if (handle === "in") {
      const next = tenth(Math.min(Math.max(t, 0), outS - STEP));
      setIn(next);
      seek(next);
    } else {
      const next = tenth(Math.max(Math.min(t, total), inS + STEP));
      setOut(next);
      seek(next);
    }
    setSelected(handle);
  }

  function togglePlay() {
    const v = video.current;
    if (!v) return;
    if (!v.paused) return v.pause();
    if (v.currentTime < inS || v.currentTime >= outS - 0.05) v.currentTime = inS;
    v.play().catch(() => {});
  }

  function timeAt(clientX: number): number {
    const box = track.current!.getBoundingClientRect();
    return Math.min(Math.max(((clientX - box.left) / box.width) * total, 0), total);
  }

  function onPointerDown(e: React.PointerEvent, what: Handle | "seek") {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    video.current?.pause();
    drag.current = what;
    if (what === "seek") seek(timeAt(e.clientX));
    else {
      // preventDefault stoppar fokusbytet; utan fokus går piltangenterna till rubrikfältet.
      (e.currentTarget as HTMLElement).focus();
      setSelected(what);
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current) return;
    const t = timeAt(e.clientX);
    if (drag.current === "seek") seek(Math.min(Math.max(t, inS), outS));
    else setHandle(drag.current, t);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    // Piltangenterna flyttar valt handtag, utom när man skriver i ett fält, styr videon eller står i orden.
    const target = e.target as HTMLElement;
    if (["INPUT", "TEXTAREA", "VIDEO"].includes(target.tagName) || target.closest("[data-caption-words]")) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const delta = (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : STEP);
    setHandle(selected, (selected === "in" ? inS : outS) + delta);
  }

  async function save() {
    if (block) return;
    setBusy(true);
    setError(null);
    video.current?.pause();
    try {
      // Bara det som har ändrats, så att servern inte trimmar om klippet för en ny rubrik.
      const body: { in_s?: number; out_s?: number; title?: string; words?: CaptionWord[] } = {};
      if (inChanged) body.in_s = inS;
      if (outChanged) body.out_s = outExact;
      if (titleChanged) body.title = title.trim();
      // Hela listan, bara när den är ändrad. Ett ord som saknas räknas som borttaget.
      if (wordsChanged) body.words = words.map((w) => ({ ...w, word: w.word.trim() }));
      onSaved(await api.campaign.clips.saveEdit(clip.id, body), inChanged || outChanged, wordsChanged);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara klippet");
      setBusy(false);
    }
  }

  const pct = (t: number) => `${(t / total) * 100}%`;
  const ticks = Array.from({ length: Math.floor(total / 5) + 1 }, (_, i) => i * 5);

  return (
    <Dialog
      open={open}
      // Stäng inte medan servern gör klippet klart, då kommer svaret aldrig fram.
      onOpenChange={(next) => !busy && onOpenChange(next)}
    >
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[860px]" onKeyDown={onKeyDown}>
        <div>
          <DialogTitle className="text-lg font-semibold">Redigera klippet</DialogTitle>
          <DialogDescription className="mt-1">
            Dra handtagen för att trimma start och slut. Klippet kan bara bli kortare än råklippet.
          </DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[250px_minmax(0,1fr)]">
          <div className="relative aspect-[9/16] w-full overflow-hidden rounded-xl bg-black">
            <video
              ref={video}
              src={`${api.campaign.proxied(info.original.url)}#t=${startAt}`}
              playsInline
              preload="auto"
              aria-label="Råklippet"
              onClick={togglePlay}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onSeeked={(e) => setNow(tenth(e.currentTarget.currentTime))}
              className="h-full w-full cursor-pointer object-contain"
            />
            {line && (
              <p
                data-caption-preview
                data-style={style}
                aria-hidden
                // Ungefär där undertexten bränns in: ovanför den nedre säkra zonen.
                className="pointer-events-none absolute inset-x-3 bottom-[22%] text-center text-[17px] leading-snug font-extrabold uppercase [paint-order:stroke] [-webkit-text-stroke:3px_#000]"
              >
                {line.map((w, i) => {
                  // Box: rutan (kampanjens guld som standard) bakom ordet som sägs. Outline: röd innan ordet sägs, vit efter.
                  const said = w.start <= now && now < (line[i + 1]?.start ?? Infinity);
                  return (
                    <span key={`${w.start}-${i}`}>
                      <span
                        data-said={style === "box" && said ? "true" : undefined}
                        className={
                          style === "box"
                            ? cn("rounded-[3px] px-0.5 text-white", said && "bg-[#ffd700]")
                            : w.start <= now
                              ? "text-white"
                              : "text-[#ff3b3b]"
                        }
                      >
                        {w.word}
                      </span>{" "}
                    </span>
                  );
                })}
              </p>
            )}
          </div>

          <div className="flex min-w-0 flex-col gap-5">
            <label className="block space-y-1">
              <span className="block text-[13px] text-muted-foreground">Rubrik</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={120}
                className="h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand"
              />
              <span className="block text-xs text-muted-foreground">
                Ny rubrik ger ny caption från kampanjens mall. En caption du har skrivit själv behålls. Kräver
                kampanjen ingen egen text, står rubriken också överst i klippet de första sekunderna.
              </span>
            </label>

            <dl className="grid grid-cols-3 gap-3 text-sm">
              {(
                [
                  ["In", fmt(inS), "in"],
                  ["Ut", fmt(outS), "out"],
                  ["Längd", fmt(length), "length"],
                ] as const
              ).map(([label, value, id]) => (
                <div
                  key={id}
                  className={cn(
                    "rounded-xl border border-line bg-background px-3 py-2",
                    id === selected && "border-brand",
                    id === "length" && problem && "border-[#4d1d1d]"
                  )}
                >
                  <dt className="text-[13px] text-muted-foreground">{label}</dt>
                  <dd data-value={id} className={cn("text-lg font-semibold tabular-nums", id === "length" && problem && "text-bad")}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="-mt-2 text-xs text-muted-foreground">
              Kampanjen tillåter {info.min_s ?? 0}–{info.max_s ?? "∞"} s. Råklippet är {fmt(total)}.
            </p>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" className="h-9" onClick={togglePlay} aria-label={playing ? "Pausa" : "Spela"}>
                {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
                {playing ? "Pausa" : "Spela"}
              </Button>
              <Button variant="outline" className="h-9" onClick={() => setHandle("in", video.current?.currentTime ?? now)}>
                Sätt in här
              </Button>
              <Button variant="outline" className="h-9" onClick={() => setHandle("out", video.current?.currentTime ?? now)}>
                Sätt ut här
              </Button>
              <Button
                variant="ghost"
                className={cn("h-9", loop ? "text-foreground" : "text-muted-foreground")}
                aria-pressed={loop}
                onClick={() => setLoop(!loop)}
              >
                <Repeat aria-hidden />
                Slinga
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Piltangenterna flyttar det valda handtaget 0,1 s, med Skift 1 s. Klicka på ett handtag för att välja det.
            </p>
          </div>
        </div>

        <div className="select-none">
          <div
            ref={track}
            data-testid="timeline"
            data-in={inS}
            data-out={outS}
            onPointerDown={(e) => onPointerDown(e, "seek")}
            onPointerMove={onPointerMove}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
            className="relative mx-2 h-14 cursor-pointer touch-none rounded-lg bg-[#1d1d23]"
          >
            {/* Grått utanför intervallet, lila ram runt det som blir klippet. */}
            <div className="absolute inset-y-0 left-0 rounded-l-lg bg-black/55" style={{ width: pct(inS) }} />
            <div className="absolute inset-y-0 right-0 rounded-r-lg bg-black/55" style={{ width: `calc(100% - ${pct(outS)})` }} />
            <div
              className="absolute inset-y-0 border-y-2 border-brand bg-brand/15"
              style={{ left: pct(inS), width: `calc(${pct(outS)} - ${pct(inS)})` }}
            />
            {ticks.map((t) => (
              <span key={t} className="absolute bottom-0 h-2 w-px bg-white/25" style={{ left: pct(t) }} />
            ))}
            <span className="pointer-events-none absolute -inset-y-1 w-0.5 rounded bg-white" style={{ left: pct(now) }} aria-hidden />
            {(["in", "out"] as const).map((handle) => {
              const t = handle === "in" ? inS : outS;
              return (
                <div
                  key={handle}
                  role="slider"
                  tabIndex={0}
                  aria-label={handle === "in" ? "Start" : "Slut"}
                  aria-valuemin={handle === "in" ? 0 : inS + STEP}
                  aria-valuemax={handle === "in" ? outS - STEP : total}
                  aria-valuenow={t}
                  aria-valuetext={fmt(t)}
                  data-handle={handle}
                  onFocus={() => setSelected(handle)}
                  onPointerDown={(e) => onPointerDown(e, handle)}
                  className={cn(
                    "absolute -inset-y-1 grid w-3.5 cursor-ew-resize place-items-center rounded-md bg-brand outline-none focus-visible:ring-2 focus-visible:ring-white",
                    handle === "in" ? "-translate-x-full" : "",
                    selected === handle && "ring-2 ring-white/80"
                  )}
                  style={{ left: pct(t) }}
                >
                  <span className="h-5 w-0.5 rounded bg-white/80" aria-hidden />
                </div>
              );
            })}
          </div>
          <div className="relative mx-2 mt-1 h-4 text-[11px] text-muted-foreground tabular-nums">
            {ticks.map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: pct(t) }}>
                {t}
              </span>
            ))}
          </div>
        </div>

        {subs?.editable ? (
          <CaptionWords
            words={words}
            original={original}
            onChange={setWords}
            now={now}
            inS={inS}
            outS={outExact}
            duration={exact}
            onSeek={(t) => {
              video.current?.pause();
              seek(t);
            }}
          />
        ) : (
          subs?.reason && <p className="rounded-xl border border-line bg-background px-3 py-2.5 text-sm text-muted-foreground">{subs.reason}</p>
        )}

        {clip.status === "godkand" && (
          <p className="text-sm text-warn">Klippet är godkänt. Efter ändringen behöver det ett nytt ja.</p>
        )}
        {(error || block) && (
          <p
            role={error ? "alert" : undefined}
            className={cn("text-sm", error || (problem && block === problem) ? "text-[#fca5a5]" : "text-muted-foreground")}
          >
            {error ?? block}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
          <Button
            variant="ghost"
            className="h-9"
            disabled={busy}
            onClick={() => {
              setIn(0);
              setOut(total);
              seek(0);
            }}
          >
            <RotateCcw aria-hidden />
            Återställ
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" className="h-9" disabled={busy} onClick={() => onOpenChange(false)}>
              Avbryt
            </Button>
            <Button className="h-9 px-4" disabled={busy || !!block} title={block ?? undefined} onClick={save}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {busy ? "Gör klippet klart igen..." : "Spara"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
