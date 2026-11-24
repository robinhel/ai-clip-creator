"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, Loader2, Lock, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { AccountList, pickAccount, useClipAccounts } from "@/components/ClipAccounts";
import { api } from "@/lib/api";
import { allows, cn } from "@/lib/utils";
import { hasOwn, isNewStart, NO_OWN, sec, startLabel, startReach } from "@/lib/versionOwn";
import type { CampaignClip, StickerPosition, VersionCreate, VersionOptions } from "@/lib/types";

type Platform = "tiktok" | "youtube";
const PLATFORMS: [Platform, "tiktok" | "youtube_shorts", string][] = [
  ["tiktok", "tiktok", "TikTok"],
  ["youtube", "youtube_shorts", "YouTube"],
];

export const POSITIONS: [StickerPosition, string][] = [
  ["top_left", "Uppe till vänster"],
  ["top_right", "Uppe till höger"],
  ["center", "Mitten"],
  ["bottom_left", "Nere till vänster"],
  ["bottom_right", "Nere till höger"],
];
// Ungefär där servern lägger klistermärket: utanför de säkra zonerna uppe och nere.
const POSITION_CLASS: Record<StickerPosition, string> = {
  top_left: "left-[6%] top-[11%]",
  top_right: "right-[6%] top-[11%]",
  center: "left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2",
  bottom_left: "left-[6%] bottom-[24%]",
  bottom_right: "right-[6%] bottom-[24%]",
};

const FILTER_LABEL: Record<string, string> = { none: "Inget", fresh: "Fräsch", warm: "Varm", cool: "Kall", punch: "Kraftig" };

const num = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const field = "mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand disabled:opacity-60 [&>option]:bg-panel";
const alert = "rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]";

function label(text: string, id: string) {
  return (
    <label htmlFor={id} className="text-[13px] text-muted-foreground">
      {text}
    </label>
  );
}

/** Reglagets gränser i tiondelar, avrundade inåt: reglaget stegar från min, så 0 (samma start) måste gå att nå. */
function startRange(opts: VersionOptions): [number, number] {
  return [Math.ceil(opts.min_start_cut_s * 10 - 1e-6) / 10, Math.floor(opts.max_start_cut_s * 10 + 1e-6) / 10];
}

/** Var förhandsvisningen spelar: originalfilen från startpunkten (in_s + startpunkt), annars det färdiga klippet. */
function previewSource(clip: CampaignClip, opts: VersionOptions | null, cut: number) {
  if (opts?.original_url) return { src: api.campaign.proxied(opts.original_url), poster: undefined, at: Math.max(0, opts.in_s + cut), original: true };
  return { src: api.campaign.videoUrl(clip), poster: api.campaign.posterUrl(clip), at: Math.max(0, cut), original: false };
}

/** Valen ritade ovanpå videon: overlay, klistermärke, hook och undertextens font. */
function PreviewLayers({ hook, font, overlayUrl, stickerUrl, position }: {
  hook: string;
  font: string;
  overlayUrl?: string;
  stickerUrl?: string;
  position: StickerPosition;
}) {
  return (
    <>
      {overlayUrl && (
        // eslint-disable-next-line @next/next/no-img-element -- overlayen från servern, i full storlek.
        <img data-overlay src={api.campaign.proxied(overlayUrl)} alt="" className="pointer-events-none absolute inset-0 h-full w-full object-fill" />
      )}
      {stickerUrl && (
        // eslint-disable-next-line @next/next/no-img-element -- klistermärket från servern.
        <img
          data-sticker={position}
          src={api.campaign.proxied(stickerUrl)}
          alt=""
          className={cn("pointer-events-none absolute w-[24%] object-contain", POSITION_CLASS[position])}
        />
      )}
      {/* Hooken som i det färdiga klippet: vit text med svart kant uppe, högst 22 tecken per rad. */}
      {hook && (
        <p
          data-version-hook
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-[9%] mx-auto max-w-[22ch] px-2 text-center text-[15px] leading-tight font-bold text-white [paint-order:stroke] [-webkit-text-stroke:3px_#000]"
        >
          {hook}
        </p>
      )}
      {/* Bara ett prov på den valda fonten. Originalfilen har ingen undertext, servern lägger på den när versionen görs. */}
      {font && (
        <p
          aria-hidden
          style={{ fontFamily: `"${font}", sans-serif` }}
          className="pointer-events-none absolute inset-x-3 bottom-[30%] text-center text-lg leading-tight text-white uppercase [paint-order:stroke] [-webkit-text-stroke:3px_#000]"
        >
          Undertext i {font}
        </p>
      )}
    </>
  );
}

/** Förhandsvisningen: originalfilen från versionens startpunkt, med valen ritade ovanpå. hook är den hook som syns, tom när kampanjen inte visar någon. */
function VersionPreview({ clip, opts, cut, ...layers }: {
  clip: CampaignClip;
  opts: VersionOptions | null;
  cut: number;
  hook: string;
  font: string;
  overlayUrl?: string;
  stickerUrl?: string;
  position: StickerPosition;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const { src, poster, at, original } = previewSource(clip, opts, cut);

  useEffect(() => {
    if (video.current && video.current.readyState >= 1) video.current.currentTime = at;
  }, [at]);

  return (
    <div className="space-y-2">
      <div data-version-preview className="relative aspect-[9/16] w-full overflow-hidden rounded-xl bg-black">
        <video
          ref={video}
          src={src}
          poster={poster}
          controls
          muted
          playsInline
          preload="auto"
          aria-label="Förhandsvisning av versionen"
          onLoadedMetadata={(e) => (e.currentTarget.currentTime = at)}
          className="h-full w-full object-contain"
        />
        <PreviewLayers {...layers} />
      </div>
      <p className="text-xs text-muted-foreground">
        Förhandsvisningen är ungefärlig: den ritar valen ovanpå {original ? "originalfilen från startpunkten" : "det färdiga klippet"}. Servern gör den riktiga filen.
      </p>
      {original && <p className="text-xs text-muted-foreground">Undertexten läggs på när versionen görs.</p>}
    </div>
  );
}

/** Föreslå (Claude, kostar lite budget). Ett svar som kommer efter byte av klipp, plattform eller konto (scope) skrivs inte i fältet. */
function useSuggestHook(clipId: number, scope: string, onHook: (hook: string) => void) {
  const round = useRef(0);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    round.current += 1;
    setAsking(false);
    setError(null);
  }, [clipId, scope]);

  async function suggest() {
    const mine = ++round.current;
    setAsking(true);
    setError(null);
    try {
      const { hook } = await api.campaign.clips.suggestVersionHook(clipId);
      if (mine === round.current) onHook(hook);
    } catch (err) {
      if (mine === round.current) setError(err instanceof Error ? err.message : "Kunde inte föreslå en hook");
    } finally {
      if (mine === round.current) setAsking(false);
    }
  }

  return { asking, error, suggest };
}

/** Fältet Hook med Föreslå. */
function HookField({ value, onChange, busy, asking, error, onSuggest }: {
  value: string;
  onChange: (hook: string) => void;
  busy: boolean;
  asking: boolean;
  error: string | null;
  onSuggest: () => void;
}) {
  return (
    <div className="space-y-1.5">
      {label("Hook", "version-hook")}
      <div className="flex gap-2">
        <input id="version-hook" value={value} onChange={(e) => onChange(e.target.value)} disabled={busy || asking} className={field} />
        <Button variant="outline" className="mt-1 h-9 shrink-0" disabled={busy || asking} onClick={onSuggest}>
          {asking ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
          {asking ? "Skriver…" : "Föreslå"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">Högst 3 rader som ryms i bilden, färre i zoom. Servern säger till om den är för lång. En ny hook gör versionen till ett eget klipp. Tom: originalets hook.</p>
      {error && (
        <p role="alert" className={alert}>
          {error}
        </p>
      )}
    </div>
  );
}

/** Reglaget Startpunkt: tidigare (negativt) eller senare än originalet. Slutet ligger kvar. */
function StartField({ opts, cut, onChange, busy }: { opts: VersionOptions; cut: number; onChange: (cut: number) => void; busy: boolean }) {
  const [lo, hi] = startRange(opts);
  const fresh = isNewStart(cut, opts.new_start_s);
  const reach = startReach(lo, hi);
  return (
    <div>
      <p className="flex justify-between gap-3">
        {label("Startpunkt", "version-cut")}
        <span className="text-sm font-medium tabular-nums">
          {startLabel(cut)}
          {fresh && <span className="ml-2 rounded-full bg-brand/15 px-2 py-0.5 text-xs text-brand">Ny startpunkt</span>}
        </span>
      </p>
      <input
        id="version-cut"
        type="range"
        min={lo}
        max={hi}
        step={0.1}
        value={cut}
        disabled={busy || lo >= hi}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--brand,#7c5cff)]"
      />
      <p className="text-xs text-muted-foreground">
        Versionen börjar tidigare eller senare än originalet. Slutet ligger kvar.{" "}
        {reach ? `${reach}, så att klippet håller kampanjens längd.` : "Här går startpunkten inte att flytta, för då håller klippet inte kampanjens längd."} Minst {sec.format(opts.new_start_s)} s åt något håll är en ny startpunkt. Då visas inte höjdpunkten först.
      </p>
    </div>
  );
}

/** Plattformarna klippet får postas på. En plattform utan ny version visar serverns förklaring. */
function PlatformPicker({ clip, start, platform, busy, onPick }: {
  clip: CampaignClip;
  start: Partial<Record<Platform, VersionOptions>>;
  platform: Platform;
  busy: boolean;
  onPick: (platform: Platform) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Plattform" className="grid grid-cols-2 gap-2">
      {PLATFORMS.filter(([, name]) => allows(clip, name)).map(([p, , text]) => {
        const off = !start[p]?.allowed;
        return (
          <label
            key={p}
            className={cn(
              "flex flex-col gap-0.5 rounded-xl border px-3 py-2.5 text-sm",
              platform === p ? "border-brand bg-brand/10" : "border-line",
              off ? "cursor-not-allowed opacity-55" : "cursor-pointer hover:border-muted-foreground"
            )}
          >
            <span className="flex items-center gap-2.5">
              <input
                type="radio"
                name={`version-plattform-${clip.id}`}
                checked={platform === p}
                disabled={off || busy}
                onChange={() => onPick(p)}
                className="size-4 accent-[var(--brand,#7c5cff)]"
              />
              <span className="font-medium">{text}</span>
            </span>
            {off && <span className="text-xs text-muted-foreground">{start[p]?.reason ?? "Går inte för klippet."}</span>}
          </label>
        );
      })}
    </div>
  );
}

/** Bild och hastighet: spegling, hastighet, filter, ljusstyrka och mättnad. */
function LookFields({ opts, busy, mirror, setMirror, speed, setSpeed, filter, setFilter, brightness, setBrightness, saturation, setSaturation }: {
  opts: VersionOptions;
  busy: boolean;
  mirror: boolean;
  setMirror: (mirror: boolean) => void;
  speed: number;
  setSpeed: (speed: number) => void;
  filter: string;
  setFilter: (filter: string) => void;
  brightness: number;
  setBrightness: (value: number) => void;
  saturation: number;
  setSaturation: (value: number) => void;
}) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-line p-3">
      <legend className="px-1 text-[13px] text-muted-foreground">Bild och hastighet</legend>
      <label className="flex items-center gap-2.5 text-sm">
        <input
          type="checkbox"
          checked={mirror}
          onChange={(e) => setMirror(e.target.checked)}
          disabled={busy}
          className="size-4 accent-[var(--brand,#7c5cff)]"
        />
        Spegla bilden (undertexten speglas inte)
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          {label("Hastighet", "version-speed")}
          <select id="version-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} disabled={busy} className={field}>
            {opts.speeds.map((s) => (
              <option key={s} value={s}>
                {num.format(s)}x
              </option>
            ))}
          </select>
        </div>
        <div>
          {label("Filter", "version-filter")}
          <select id="version-filter" value={filter} onChange={(e) => setFilter(e.target.value)} disabled={busy} className={field}>
            {opts.filters.map((f) => (
              <option key={f} value={f}>
                {FILTER_LABEL[f] ?? f}
              </option>
            ))}
          </select>
        </div>
      </div>
      {(
        [
          ["Ljusstyrka", "version-brightness", brightness, setBrightness, 0.3],
          ["Mättnad", "version-saturation", saturation, setSaturation, 0.5],
        ] as const
      ).map(([text, id, value, set, max]) => (
        <div key={id}>
          <p className="flex justify-between gap-3">
            {label(text, id)}
            <span className="text-sm font-medium tabular-nums">{num.format(value)}</span>
          </p>
          <input
            id={id}
            type="range"
            min={-max}
            max={max}
            step={0.01}
            value={value}
            disabled={busy}
            onChange={(e) => set(Number(e.target.value))}
            className="w-full accent-[var(--brand,#7c5cff)]"
          />
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        Förhandsvisningen visar inte spegling, filter eller hastighet. Granska den färdiga versionen.
      </p>
    </fieldset>
  );
}

/** Klistermärket: fil, position, start och längd. */
function StickerFields({ opts, busy, sticker, setSticker, position, setPosition, stickerStart, setStickerStart, stickerLength, setStickerLength }: {
  opts: VersionOptions;
  busy: boolean;
  sticker: string;
  setSticker: (file: string) => void;
  position: StickerPosition;
  setPosition: (position: StickerPosition) => void;
  stickerStart: string;
  setStickerStart: (value: string) => void;
  stickerLength: string;
  setStickerLength: (value: string) => void;
}) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-line p-3">
      <legend className="px-1 text-[13px] text-muted-foreground">Klistermärke</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          {label("Klistermärke", "version-sticker")}
          <select id="version-sticker" value={sticker} onChange={(e) => setSticker(e.target.value)} disabled={busy} className={field}>
            <option value="">Inget</option>
            {opts.stickers.map((s) => (
              <option key={s.file} value={s.file}>
                {s.file}
              </option>
            ))}
          </select>
        </div>
        {sticker && (
          <div>
            {label("Position", "version-position")}
            <select
              id="version-position"
              value={position}
              onChange={(e) => setPosition(e.target.value as StickerPosition)}
              disabled={busy}
              className={field}
            >
              {POSITIONS.map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {sticker && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            {label("Start (s)", "version-sticker-start")}
            <input
              id="version-sticker-start"
              type="number"
              min={0}
              step={0.5}
              value={stickerStart}
              onChange={(e) => setStickerStart(e.target.value)}
              disabled={busy}
              className={field}
            />
          </div>
          <div>
            {label("Längd (s)", "version-sticker-length")}
            <input
              id="version-sticker-length"
              type="number"
              min={0.5}
              max={10}
              step={0.5}
              value={stickerLength}
              onChange={(e) => setStickerLength(e.target.value)}
              disabled={busy}
              className={field}
            />
          </div>
        </div>
      )}
    </fieldset>
  );
}

/** Svaret när versionen är gjord: klippets nummer som på klippkorten, och vad Robin gör sedan. */
function MadeNotice({ made, onClose }: { made: CampaignClip; onClose: () => void }) {
  return (
    <div role="status" className="space-y-3 rounded-xl border border-line bg-background p-4">
      <p className="flex items-center gap-2 text-sm text-good">
        <CheckCircle2 className="size-4 shrink-0" aria-hidden />
        Versionen är gjord som klipp {made.id}.
      </p>
      <p className="text-sm text-muted-foreground">
        {made.status === "underkand"
          ? "Regelkontrollen hittade problem. Rätta dem på klippet innan du säger ja."
          : "Granska den och säg ja innan du postar. Byt text med Föreslå ny text när du postar."}
      </p>
      <Button className="h-10 w-full" variant="outline" onClick={onClose}>
        Stäng
      </Button>
    </div>
  );
}

/** Varningarna ovanför Gör versionen. Knappen fungerar ändå. */
function VersionWarnings({ own, accountWarning }: { own: boolean; accountWarning: string | null }) {
  if (own && !accountWarning) return null;
  return (
    <div className="space-y-1.5 text-sm text-warn">
      {!own && <p data-version-no-own>⚠️ {NO_OWN}</p>}
      {accountWarning && <p data-version-account-warning>⚠️ {accountWarning}</p>}
    </div>
  );
}

/** Fel i klistermärkets start eller längd, eller null. */
function stickerProblem(startS: number, lengthS: number): string | null {
  const within = (value: number, lo: number, hi: number) => Number.isFinite(value) && value >= lo && value <= hi;
  if (!within(startS, 0, Infinity)) return "Klistermärkets start får inte vara negativ.";
  if (!within(lengthS, 0.5, 10)) return "Klistermärket ska synas 0,5–10 s.";
  return null;
}

/** Varför Gör versionen inte går just nu, eller null. Medan Föreslå skriver väntar knappen, annars skickas den gamla hooken. */
function blockReason({ account, optsError, opts, sticker, startS, lengthS, asking }: {
  account: string;
  optsError: string | null;
  opts: VersionOptions | null;
  sticker: string;
  startS: number;
  lengthS: number;
  asking: boolean;
}): string | null {
  if (!account) return "Inget av kampanjens konton är ledigt för klippet.";
  if (optsError) return null;
  if (!opts) return "Läser valen…";
  if (!opts.allowed) return opts.reason ?? "Servern tillåter ingen ny version här.";
  if (asking) return "Väntar på hooken från Föreslå…";
  return sticker ? stickerProblem(startS, lengthS) : null;
}

function VersionDialog({ clip, start, onClose }: {
  clip: CampaignClip;
  /** Valen per plattform utan konto, från knappen. */
  start: Partial<Record<Platform, VersionOptions>>;
  onClose: (made: CampaignClip | null) => void;
}) {
  const [platform, setPlatform] = useState<Platform>(start.tiktok?.allowed ? "tiktok" : "youtube");
  const { info } = useClipAccounts(clip.id, platform);
  // Okopplade konton har inget namn och går inte att välja.
  const connected = info?.accounts.filter((a) => a.name).map((a) => a.account) ?? [];
  const [picked, setPicked] = useState("");
  const account = pickAccount(info, connected, picked);
  const [opts, setOpts] = useState<VersionOptions | null>(null);
  const [optsError, setOptsError] = useState<string | null>(null);

  const [font, setFont] = useState("");
  const [hook, setHook] = useState("");
  const [cut, setCut] = useState(0);
  const [overlay, setOverlay] = useState("");
  const [sticker, setSticker] = useState("");
  const [position, setPosition] = useState<StickerPosition>("top_right");
  const [stickerStart, setStickerStart] = useState("0");
  const [stickerLength, setStickerLength] = useState("3");
  const [mirror, setMirror] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState("none");
  const [brightness, setBrightness] = useState(0);
  const [saturation, setSaturation] = useState(0);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<CampaignClip | null>(null);

  // Valen och förvalen gäller kontot: ett konto kan ha en egen fast stil i kampanjen.
  useEffect(() => {
    if (!account) return;
    let live = true;
    setOpts(null);
    setOptsError(null);
    api.campaign.clips
      .versionOptions(clip.id, platform, account)
      .then((o) => {
        if (!live) return;
        setOpts(o);
        const d = o.defaults;
        setFont(d.font ?? "");
        setHook(o.hook);
        // Kontots förval i tiondelar som reglaget, inom reglagets gränser så att servern godtar det.
        const [lo, hi] = startRange(o);
        setCut(Math.min(hi, Math.max(lo, Math.round(d.start_cut_s * 10) / 10)));
        setOverlay(d.overlay ?? "");
        setSticker(d.sticker?.file ?? "");
        setPosition(d.sticker?.position ?? "top_right");
        setStickerStart(String(d.sticker?.start_s ?? 0));
        setStickerLength(String(d.sticker?.duration_s ?? 3));
      })
      .catch((err) => live && setOptsError(err instanceof Error ? err.message : "Kunde inte läsa valen"));
    return () => {
      live = false;
    };
  }, [clip.id, platform, account]);

  const suggester = useSuggestHook(clip.id, `${platform}/${account}`, setHook);

  // Hooken som syns och som skickas: servern gör en tom hook till originalets.
  const shownHook = hook.trim() || (opts?.hook ?? "").trim();
  const startS = Number(stickerStart.replace(",", "."));
  const lengthS = Number(stickerLength.replace(",", "."));
  const reason = blockReason({ account, optsError, opts, sticker, startS, lengthS, asking: suggester.asking });

  async function create() {
    if (reason || !opts) return;
    const body: VersionCreate = {
      platform,
      account,
      font: font || null,
      start_cut_s: cut,
      // null: originalets hook, rubrik och caption.
      hook: shownHook !== opts.hook.trim() ? shownHook : null,
      overlay: overlay || null,
      sticker: sticker ? { file: sticker, position, start_s: startS, duration_s: lengthS } : null,
      mirror,
      speed,
      filter,
      brightness,
      saturation,
    };
    setBusy(true);
    setError(null);
    try {
      setMade(await api.campaign.clips.createVersion(clip.id, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte göra versionen");
    } finally {
      setBusy(false);
    }
  }

  const overlayUrl = opts?.overlays.find((o) => o.file === overlay)?.url;
  const stickerUrl = opts?.stickers.find((s) => s.file === sticker)?.url;

  return (
    // Stäng inte medan servern renderar, då kommer svaret aldrig fram.
    <Dialog open onOpenChange={(open) => !open && !busy && onClose(made)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[820px]">
        <div>
          <DialogTitle className="text-lg font-semibold">Ny version för ett annat konto</DialogTitle>
          <DialogDescription className="mt-1">
            Ge versionen {(opts ?? start[platform])?.hook_text ? "en ny hook eller en ny startpunkt" : "en ny startpunkt"}, så räknas den som ett eget klipp. Du kan också byta font, overlay, klistermärke, spegling, filter och hastighet. Versionen blir ett eget klipp som du granskar.
          </DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[240px_minmax(0,1fr)]">
          <VersionPreview
            clip={clip}
            opts={opts}
            cut={cut}
            hook={opts?.hook_text ? shownHook : ""}
            font={font}
            overlayUrl={overlayUrl}
            stickerUrl={stickerUrl}
            position={position}
          />

          <div className="min-w-0 space-y-4">
            {made ? (
              <MadeNotice made={made} onClose={() => onClose(made)} />
            ) : (
              <>
                <PlatformPicker
                  clip={clip}
                  start={start}
                  platform={platform}
                  busy={busy}
                  onPick={(p) => {
                    setPlatform(p);
                    setPicked("");
                  }}
                />

                {info?.multi && (
                  <AccountList
                    label={platform === "tiktok" ? "TikTok-konto" : "Kanal"}
                    info={info}
                    connected={connected}
                    value={account}
                    onChange={setPicked}
                    disabled={busy}
                  />
                )}

                {optsError && (
                  <p role="alert" className={alert}>
                    {optsError}
                  </p>
                )}

                {opts?.allowed && (
                  <>
                    {opts.hook_text && (
                      <HookField
                        value={hook}
                        onChange={setHook}
                        busy={busy}
                        asking={suggester.asking}
                        error={suggester.error}
                        onSuggest={suggester.suggest}
                      />
                    )}

                    <div className="grid gap-4 sm:grid-cols-2">
                      <div>
                        {label("Font", "version-font")}
                        <select id="version-font" value={font} onChange={(e) => setFont(e.target.value)} disabled={busy} className={field}>
                          <option value="">Som originalet</option>
                          {opts.fonts.map((f) => (
                            <option key={f.name} value={f.name}>
                              {f.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        {label("Overlay", "version-overlay")}
                        <select
                          id="version-overlay"
                          value={overlay}
                          onChange={(e) => setOverlay(e.target.value)}
                          disabled={busy || opts.overlay_locked}
                          className={field}
                        >
                          {/* null hos servern: kampanjens overlay. Förhandsvisningen ritar den inte: originalfilen har ingen, det färdiga klippet har den redan. */}
                          <option value="">Kampanjens overlay</option>
                          {opts.overlays.map((o) => (
                            <option key={o.file} value={o.file}>
                              {o.file.split("/").pop()}
                            </option>
                          ))}
                        </select>
                        {opts.overlay_locked && (
                          <p className="mt-1 flex items-start gap-1.5 text-xs text-muted-foreground">
                            <Lock className="mt-px size-3 shrink-0" aria-hidden />
                            Kampanjen kräver sin egen overlay, så den går inte att byta.
                          </p>
                        )}
                      </div>
                    </div>

                    <StartField opts={opts} cut={cut} onChange={setCut} busy={busy} />

                    <LookFields
                      opts={opts}
                      busy={busy}
                      mirror={mirror}
                      setMirror={setMirror}
                      speed={speed}
                      setSpeed={setSpeed}
                      filter={filter}
                      setFilter={setFilter}
                      brightness={brightness}
                      setBrightness={setBrightness}
                      saturation={saturation}
                      setSaturation={setSaturation}
                    />

                    <StickerFields
                      opts={opts}
                      busy={busy}
                      sticker={sticker}
                      setSticker={setSticker}
                      position={position}
                      setPosition={setPosition}
                      stickerStart={stickerStart}
                      setStickerStart={setStickerStart}
                      stickerLength={stickerLength}
                      setStickerLength={setStickerLength}
                    />
                  </>
                )}

                {opts?.allowed && (
                  <VersionWarnings
                    own={hasOwn({ ...opts, hook: shownHook, start_cut_s: cut })}
                    accountWarning={opts.account_warning}
                  />
                )}

                <div className="space-y-1.5">
                  <Button className="h-10 w-full" disabled={!!reason || busy} onClick={create}>
                    {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Copy aria-hidden />}
                    {busy ? "Gör versionen…" : "Gör versionen"}
                  </Button>
                  {busy ? (
                    <p className="text-xs text-muted-foreground">Servern gör en ny fil. Det kan ta en minut.</p>
                  ) : (
                    reason && <p className="text-xs text-muted-foreground">{reason}</p>
                  )}
                </div>
              </>
            )}

            {error && (
              <p role="alert" className={alert}>
                {error}
              </p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Knappen Ny version. Visas när servern svarar på valen: aktiv om någon plattform tillåter, annars avstängd med serverns förklaring. */
export function VersionButton({ clip, onCreated }: { clip: CampaignClip; onCreated?: (made: CampaignClip) => void }) {
  const [start, setStart] = useState<Partial<Record<Platform, VersionOptions>> | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    const wanted = PLATFORMS.filter(([, name]) => allows(clip, name)).map(([p]) => p);
    // Servern kräver ett konto: fråga för ett ledigt, så att svaret gäller klippet. Äldre server: anropen misslyckas och knappen visas inte.
    const ask = async (p: Platform) => {
      const { accounts } = await api.campaign.clips.accounts(clip.id, p);
      const free = accounts.filter((a) => a.state === "free");
      const account = free.find((a) => a.name) ?? free[0] ?? accounts[0];
      return [p, await api.campaign.clips.versionOptions(clip.id, p, account?.account ?? "")] as const;
    };
    Promise.all(wanted.map((p) => ask(p).catch(() => null))).then(
      (list) => live && setStart(Object.fromEntries(list.filter((x) => x !== null)))
    );
    return () => {
      live = false;
    };
  }, [clip]);

  // Går ingen ny version att göra, visas knappen inte alls.
  if (!start || !Object.values(start).some((o) => o?.allowed)) return null;

  return (
    <div className="space-y-1.5">
      <Button className="h-9 w-full" variant="outline" onClick={() => setOpen(true)}>
        <Copy aria-hidden />
        Ny version
      </Button>
      {open && (
        <VersionDialog
          clip={clip}
          start={start}
          onClose={(made) => {
            setOpen(false);
            if (made) onCreated?.(made);
          }}
        />
      )}
    </div>
  );
}
