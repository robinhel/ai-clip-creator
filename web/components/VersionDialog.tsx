"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, Loader2, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { AccountList, pickAccount, useClipAccounts } from "@/components/ClipAccounts";
import { api } from "@/lib/api";
import { allows, cn } from "@/lib/utils";
import type { CampaignClip, StickerPosition, VersionCreate, VersionOptions } from "@/lib/types";

type Platform = "tiktok" | "youtube";
const PLATFORMS: [Platform, "tiktok" | "youtube_shorts", string][] = [
  ["tiktok", "tiktok", "TikTok"],
  ["youtube", "youtube_shorts", "YouTube"],
];

const POSITIONS: [StickerPosition, string][] = [
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

const sec = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });
const field = "mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand disabled:opacity-60 [&>option]:bg-panel";

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
  const [cut, setCut] = useState(0);
  const [overlay, setOverlay] = useState("");
  const [sticker, setSticker] = useState("");
  const [position, setPosition] = useState<StickerPosition>("top_right");
  const [stickerStart, setStickerStart] = useState("0");
  const [stickerLength, setStickerLength] = useState("3");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<CampaignClip | null>(null);
  const video = useRef<HTMLVideoElement>(null);

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
        setCut(d.start_cut_s);
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

  useEffect(() => {
    if (video.current && video.current.readyState >= 1) video.current.currentTime = cut;
  }, [cut]);

  const startS = Number(stickerStart.replace(",", "."));
  const lengthS = Number(stickerLength.replace(",", "."));
  const reason = !account
    ? "Inget av kampanjens konton är ledigt för klippet."
    : optsError
      ? null
      : !opts
        ? "Läser valen…"
        : !opts.allowed
          ? (opts.reason ?? "Servern tillåter ingen ny version här.")
          : sticker && (!Number.isFinite(startS) || startS < 0)
            ? "Klistermärkets start får inte vara negativ."
            : sticker && (!Number.isFinite(lengthS) || lengthS < 0.5 || lengthS > 10)
              ? "Klistermärket ska synas 0,5–10 s."
              : null;

  async function create() {
    if (reason || !opts) return;
    const body: VersionCreate = {
      platform,
      account,
      font: font || null,
      start_cut_s: cut,
      overlay: overlay || null,
      sticker: sticker ? { file: sticker, position, start_s: startS, duration_s: lengthS } : null,
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
  const label = (text: string, id: string) => (
    <label htmlFor={id} className="text-[13px] text-muted-foreground">
      {text}
    </label>
  );

  return (
    // Stäng inte medan servern renderar, då kommer svaret aldrig fram.
    <Dialog open onOpenChange={(open) => !open && !busy && onClose(made)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[820px]">
        <div>
          <DialogTitle className="text-lg font-semibold">Ny version för ett annat konto</DialogTitle>
          <DialogDescription className="mt-1">
            Samma klipp med egen font, eget klipp i början, overlay och klistermärke. Versionen blir ett eget klipp som du granskar.
          </DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[240px_minmax(0,1fr)]">
          <div className="space-y-2">
            <div data-version-preview className="relative aspect-[9/16] w-full overflow-hidden rounded-xl bg-black">
              <video
                ref={video}
                src={api.campaign.videoUrl(clip)}
                poster={api.campaign.posterUrl(clip)}
                controls
                muted
                playsInline
                preload="auto"
                aria-label="Förhandsvisning av versionen"
                onLoadedMetadata={(e) => (e.currentTarget.currentTime = cut)}
                className="h-full w-full object-contain"
              />
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
              {/* Utan vald font syns originalets undertext redan i videon. */}
              {font && (
                <p
                  aria-hidden
                  style={{ fontFamily: `"${font}", sans-serif` }}
                  className="pointer-events-none absolute inset-x-3 bottom-[30%] text-center text-lg leading-tight text-white uppercase [paint-order:stroke] [-webkit-text-stroke:3px_#000]"
                >
                  Undertext i {font}
                </p>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Förhandsvisningen är ungefärlig: den ritar valen ovanpå det färdiga klippet. Servern gör den riktiga filen.
            </p>
          </div>

          <div className="min-w-0 space-y-4">
            {made ? (
              <div role="status" className="space-y-3 rounded-xl border border-line bg-background p-4">
                <p className="flex items-center gap-2 text-sm text-good">
                  <CheckCircle2 className="size-4 shrink-0" aria-hidden />
                  Versionen är gjord som klipp #{made.id}.
                </p>
                <p className="text-sm text-muted-foreground">
                  {made.status === "underkand"
                    ? "Regelkontrollen hittade problem. Rätta dem på klippet innan du säger ja."
                    : "Granska den och säg ja innan du postar. Byt text med Föreslå ny text när du postar."}
                </p>
                <Button className="h-10 w-full" variant="outline" onClick={() => onClose(made)}>
                  Stäng
                </Button>
              </div>
            ) : (
              <>
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
                            onChange={() => {
                              setPlatform(p);
                              setPicked("");
                            }}
                            className="size-4 accent-[var(--brand,#7c5cff)]"
                          />
                          <span className="font-medium">{text}</span>
                        </span>
                        {off && <span className="text-xs text-muted-foreground">{start[p]?.reason ?? "Går inte för klippet."}</span>}
                      </label>
                    );
                  })}
                </div>

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
                  <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
                    {optsError}
                  </p>
                )}

                {opts?.allowed && (
                  <>
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
                          {/* null hos servern: den overlay klippet redan har, så förhandsvisningen ritar ingen extra. */}
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

                    <div>
                      <p className="flex justify-between gap-3">
                        {label("Klipp i början", "version-cut")}
                        <span className="text-sm font-medium tabular-nums">{sec.format(cut)} s</span>
                      </p>
                      <input
                        id="version-cut"
                        type="range"
                        min={0}
                        max={opts.max_start_cut_s}
                        step={0.5}
                        value={cut}
                        disabled={busy || opts.max_start_cut_s <= 0}
                        onChange={(e) => setCut(Number(e.target.value))}
                        className="w-full accent-[var(--brand,#7c5cff)]"
                      />
                      <p className="text-xs text-muted-foreground">
                        Versionen börjar så här många sekunder in. Högst {sec.format(opts.max_start_cut_s)} s, så att klippet inte blir för kort.
                      </p>
                    </div>

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
                  </>
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
              <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
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

  if (!start || Object.keys(start).length === 0) return null;
  const allowed = Object.values(start).some((o) => o?.allowed);
  const reasons = [...new Set(Object.values(start).map((o) => o?.reason).filter(Boolean))];

  return (
    <div className="space-y-1.5 border-t border-line pt-3">
      <Button className="h-9 w-full" variant="outline" disabled={!allowed} onClick={() => setOpen(true)}>
        <Copy aria-hidden />
        Ny version
      </Button>
      {!allowed && reasons.map((r) => <p key={r} className="text-xs text-muted-foreground">{r}</p>)}
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
