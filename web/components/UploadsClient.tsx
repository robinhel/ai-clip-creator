"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Lock, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow } from "@/components/ClipResultRow";
import { POSITIONS } from "@/components/VersionDialog";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, StickerPosition, UploadOptions } from "@/lib/types";

// Samma val som servern (campaign/versions.py). Servern kontrollerar dem.
const SPEEDS = [1, 1.05, 1.1, 1.15, 1.2];
const FILTERS: [string, string][] = [
  ["none", "Inget"],
  ["fresh", "Fräsch"],
  ["warm", "Varm"],
  ["cool", "Kall"],
  ["punch", "Kraftig"],
];
const FRAMINGS: [string, string, string][] = [
  ["fill", "Fyll bilden", "Zoomar in och skär bort kanterna."],
  ["fit", "Hela bilden", "Hela bilden på en suddig bakgrund."],
];
const ACCEPT = ".mp4,.mov,.m4v,.webm,.mkv";
const num = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sec = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });
const field = "mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand disabled:opacity-60 [&>option]:bg-panel";

type Item = { key: string; file: File; title: string; state: "waiting" | "busy" | "done" | "error"; message?: string };

export function UploadsClient() {
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [profile, setProfile] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const [mirror, setMirror] = useState(true);
  const [speed, setSpeed] = useState(1.1);
  const [filter, setFilter] = useState("fresh");
  const [brightness, setBrightness] = useState(0);
  const [saturation, setSaturation] = useState(0);
  const [framing, setFraming] = useState("fit");
  const [opts, setOpts] = useState<UploadOptions | null>(null);
  const [font, setFont] = useState("");
  const [cut, setCut] = useState(0);
  const [overlay, setOverlay] = useState("");
  const [sticker, setSticker] = useState("");
  const [position, setPosition] = useState<StickerPosition>("top_right");
  const [stickerStart, setStickerStart] = useState("0");
  const [stickerLength, setStickerLength] = useState("3");
  const [busy, setBusy] = useState(false);
  const [clips, setClips] = useState<CampaignClip[]>([]);

  const loadClips = useCallback(() => {
    api.campaign.clips.list({ vod_id: "upload" }).then(setClips).catch(() => {});
  }, []);

  useEffect(() => {
    api.campaign
      .profiles()
      .then((list) => {
        const active = list.filter((p) => p.active);
        setProfiles(active);
        setProfile((current) => current || active[0]?.name || "");
      })
      .catch(() => {});
    loadClips();
  }, [loadClips]);

  // Valen gäller kampanjen: en kampanj kan kräva sin egen overlay.
  useEffect(() => {
    if (!profile) return;
    let live = true;
    setOpts(null);
    setOverlay("");
    api.campaign
      .uploadOptions(profile)
      .then((o) => live && setOpts(o))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [profile]);

  function add(files: FileList | null) {
    if (!files) return;
    const added = Array.from(files).map((file) => ({ key: `${file.name}-${file.size}-${Math.random()}`, file, title: "", state: "waiting" as const }));
    setItems((list) => [...list, ...added]);
  }

  const update = (key: string, change: Partial<Item>) =>
    setItems((list) => list.map((item) => (item.key === key ? { ...item, ...change } : item)));

  async function makeAll() {
    setBusy(true);
    // En i taget: servern kodar om varje video, och det tar datorns kraft.
    for (const item of items.filter((i) => i.state === "waiting" || i.state === "error")) {
      update(item.key, { state: "busy", message: undefined });
      const form = new FormData();
      form.append("file", item.file);
      form.append("profile", profile);
      form.append("title", item.title);
      form.append("mirror", String(mirror));
      form.append("speed", String(speed));
      form.append("filter", filter);
      form.append("brightness", String(brightness));
      form.append("saturation", String(saturation));
      form.append("framing", framing);
      form.append("font", font);
      form.append("start_cut_s", String(cut));
      form.append("overlay", overlay);
      if (sticker) {
        form.append("sticker", sticker);
        form.append("sticker_position", position);
        form.append("sticker_start_s", String(startS));
        form.append("sticker_duration_s", String(lengthS));
      }
      try {
        const clip = await api.campaign.upload(form);
        update(item.key, { state: "done", message: `Klipp #${clip.id}` });
        loadClips();
      } catch (err) {
        update(item.key, { state: "error", message: err instanceof Error ? err.message : "Gick inte att göra om" });
      }
    }
    setBusy(false);
  }

  const toReview = clips.filter((c) => c.status === "renderad" || c.status === "underkand");
  const approved = clips.filter((c) => c.status === "godkand").length;
  const formats = profiles.find((p) => p.name === profile)?.formats ?? [];
  const waiting = items.filter((i) => i.state === "waiting" || i.state === "error").length;
  const startS = Number(stickerStart.replace(",", "."));
  const lengthS = Number(stickerLength.replace(",", "."));
  const stickerProblem = !sticker
    ? null
    : !Number.isFinite(startS) || startS < 0
      ? "Klistermärkets start får inte vara negativ."
      : !Number.isFinite(lengthS) || lengthS < 0.5 || lengthS > 10
        ? "Klistermärket ska synas 0,5–10 s."
        : null;
  const label = (text: string, id: string) => (
    <label htmlFor={id} className="text-[13px] text-muted-foreground">
      {text}
    </label>
  );

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Egna videor</h1>
      <p className="mt-1.5 text-muted-foreground">
        Dra in videor. Appen gör om dem till 9:16 med valen nedan. Säg ja till ett klipp, så hamnar det i{" "}
        <Link href="/queue" className="text-brand underline-offset-4 hover:underline">
          Att posta
        </Link>
        {approved > 0 ? `, där ${approved} egna klipp redan väntar` : ""}.
      </p>

      <section className="mt-6 grid gap-6 rounded-2xl border border-line bg-panel p-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-3">
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              add(e.dataTransfer.files);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-10 text-center text-sm",
              dragging ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
            )}
          >
            <Upload className="size-6 text-muted-foreground" aria-hidden />
            <span className="font-medium">Dra in videor här, eller klicka för att välja</span>
            <span className="text-xs text-muted-foreground">mp4, mov, m4v, webm eller mkv</span>
            <input
              type="file"
              accept={ACCEPT}
              multiple
              className="sr-only"
              onChange={(e) => {
                add(e.target.files);
                e.target.value = "";
              }}
            />
          </label>

          {items.length > 0 && (
            <ul className="space-y-2">
              {items.map((item) => (
                <li key={item.key} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
                  <span className="min-w-24 flex-1 truncate font-medium" title={item.file.name}>
                    {item.file.name}
                  </span>
                  <input
                    aria-label={`Rubrik för ${item.file.name}`}
                    placeholder="Rubrik (bränns in i början, valfri)"
                    value={item.title}
                    onChange={(e) => update(item.key, { title: e.target.value })}
                    disabled={item.state === "busy" || item.state === "done"}
                    className={cn(field, "mt-0 w-64")}
                  />
                  <span
                    role={item.state === "error" ? "alert" : undefined}
                    className={cn("text-xs", item.state === "error" ? "text-[#fca5a5]" : "text-muted-foreground")}
                  >
                    {item.state === "busy" ? (
                      <Loader2 className="size-4 animate-spin" aria-label="Görs om" />
                    ) : item.state === "done" ? (
                      `Klar: ${item.message}`
                    ) : (
                      item.message ?? "Väntar"
                    )}
                  </span>
                  {item.state !== "busy" && (
                    <button
                      type="button"
                      aria-label={`Ta bort ${item.file.name} ur listan`}
                      onClick={() => setItems((list) => list.filter((i) => i.key !== item.key))}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="size-4" aria-hidden />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-4">
          <div>
            {label("Kampanj", "upload-profile")}
            <select id="upload-profile" value={profile} onChange={(e) => setProfile(e.target.value)} disabled={busy} className={field}>
              {profiles.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.streamer || p.name}
                </option>
              ))}
            </select>
          </div>
          <div role="radiogroup" aria-label="Bild" className="space-y-1">
            <p className="text-[13px] text-muted-foreground">Bild</p>
            <div className="grid grid-cols-2 gap-2">
              {FRAMINGS.map(([value, text, help]) => (
                <label
                  key={value}
                  className={cn(
                    "flex cursor-pointer flex-col gap-0.5 rounded-xl border px-3 py-2 text-sm",
                    framing === value ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
                  )}
                >
                  <span className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="upload-framing"
                      value={value}
                      checked={framing === value}
                      onChange={() => setFraming(value)}
                      disabled={busy}
                      className="size-4 accent-[var(--brand,#7c5cff)]"
                    />
                    <span className="font-medium">{text}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">{help}</span>
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2.5 text-sm">
            <input
              type="checkbox"
              checked={mirror}
              onChange={(e) => setMirror(e.target.checked)}
              disabled={busy}
              className="size-4 accent-[var(--brand,#7c5cff)]"
            />
            Spegla bilden
          </label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              {label("Hastighet", "upload-speed")}
              <select id="upload-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} disabled={busy} className={field}>
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>
                    {num.format(s)}x
                  </option>
                ))}
              </select>
            </div>
            <div>
              {label("Filter", "upload-filter")}
              <select id="upload-filter" value={filter} onChange={(e) => setFilter(e.target.value)} disabled={busy} className={field}>
                {FILTERS.map(([value, text]) => (
                  <option key={value} value={value}>
                    {text}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {(
            [
              ["Ljusstyrka", "upload-brightness", brightness, setBrightness, 0.3],
              ["Mättnad", "upload-saturation", saturation, setSaturation, 0.5],
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
          {opts && (
            <>
              <div>
                {label("Font", "upload-font")}
                <select id="upload-font" value={font} onChange={(e) => setFont(e.target.value)} disabled={busy} className={field}>
                  <option value="">Kampanjens font</option>
                  {opts.fonts.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-muted-foreground">
                  {opts.hook_text
                    ? "Fonten gäller rubriken som bränns in."
                    : "Kampanjen bränner inte in rubriken, så fonten syns bara i kampanjens egen text."}
                </p>
              </div>
              <div>
                {label("Overlay", "upload-overlay")}
                <select
                  id="upload-overlay"
                  value={overlay}
                  onChange={(e) => setOverlay(e.target.value)}
                  disabled={busy || opts.overlay_locked}
                  className={field}
                >
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
              <div>
                <p className="flex justify-between gap-3">
                  {label("Klipp i början", "upload-cut")}
                  <span className="text-sm font-medium tabular-nums">{sec.format(cut)} s</span>
                </p>
                <input
                  id="upload-cut"
                  type="range"
                  min={0}
                  max={opts.max_start_cut_s}
                  step={0.5}
                  value={cut}
                  disabled={busy}
                  onChange={(e) => setCut(Number(e.target.value))}
                  className="w-full accent-[var(--brand,#7c5cff)]"
                />
                <p className="text-xs text-muted-foreground">
                  Videon börjar så här många sekunder in. Blir den kortare än kampanjens {sec.format(opts.min_seconds)} s säger appen till.
                </p>
              </div>
              <fieldset className="space-y-3 rounded-xl border border-line p-3">
                <legend className="px-1 text-[13px] text-muted-foreground">Klistermärke</legend>
                <div>
                  {label("Klistermärke", "upload-sticker")}
                  <select id="upload-sticker" value={sticker} onChange={(e) => setSticker(e.target.value)} disabled={busy} className={field}>
                    <option value="">Inget</option>
                    {opts.stickers.map((s) => (
                      <option key={s.file} value={s.file}>
                        {s.file}
                      </option>
                    ))}
                  </select>
                </div>
                {sticker && (
                  <>
                    <div>
                      {label("Position", "upload-position")}
                      <select
                        id="upload-position"
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
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        {label("Start (s)", "upload-sticker-start")}
                        <input
                          id="upload-sticker-start"
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
                        {label("Längd (s)", "upload-sticker-length")}
                        <input
                          id="upload-sticker-length"
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
                    <p className="text-xs text-muted-foreground">
                      Klistermärket hamnar utanför plattformens säkra zon, där knappar och text ligger.
                    </p>
                  </>
                )}
              </fieldset>
            </>
          )}
          <Button className="h-10 w-full" disabled={busy || waiting === 0 || !profile || !!stickerProblem} onClick={makeAll}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
            {busy ? "Gör om…" : waiting > 1 ? `Gör om ${waiting} videor` : "Gör om videon"}
          </Button>
          {!profile && <p className="text-xs text-muted-foreground">Ingen aktiv kampanj. Slå på en under Kampanjer.</p>}
          {stickerProblem && <p className="text-xs text-muted-foreground">{stickerProblem}</p>}
        </div>
      </section>

      <section className="mt-8">
        <h2 className="text-xl font-semibold">Att granska</h2>
        <p className="mt-1 text-sm text-muted-foreground">Säg ja till klippet för att skicka det till Att posta.</p>
        {toReview.length === 0 ? (
          <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
            Inga egna videor väntar på granskning.
          </p>
        ) : (
          toReview.map((clip, index) => (
            <ClipResultRow
              key={`${clip.id}-${clip.updated}`}
              clip={clip}
              number={index + 1}
              formats={profiles.find((p) => p.name === clip.profile)?.formats ?? formats}
              onUpdate={(changed) => setClips((list) => list.map((c) => (c.id === changed.id ? changed : c)))}
              onReload={loadClips}
            />
          ))
        )}
      </section>
    </main>
  );
}
