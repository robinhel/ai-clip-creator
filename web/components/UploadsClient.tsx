"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Info, Loader2, Lock, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ClipResultRow } from "@/components/ClipResultRow";
import { POSITIONS } from "@/components/VersionDialog";
import { api, uploadWithProgress } from "@/lib/api";
import { takeHandedOverFiles } from "@/lib/handoff";
import { cn } from "@/lib/utils";
import type { CampaignClip, CampaignFormat, CampaignProfile, CampaignStatus, StickerPosition, UploadOptions } from "@/lib/types";

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

type Item = {
  key: string;
  file: File;
  title: string;
  state: "waiting" | "busy" | "done" | "error";
  message?: string;
  /** Uppladdningen i procent medan state är busy. 100 betyder att servern gör om videon. */
  percent?: number;
};

// Flikar som på projektsidan, så att ett nekat klipp går att hitta och ångra.
const TABS: { id: string; label: string; statuses: CampaignStatus[] }[] = [
  { id: "granska", label: "Att granska", statuses: ["renderad", "underkand"] },
  { id: "godkanda", label: "Godkända", statuses: ["godkand"] },
  { id: "postade", label: "Postade", statuses: ["postad", "inskickad", "analytics_inskickad", "betald"] },
  { id: "nekade", label: "Nekade", statuses: ["nekad"] },
];

const LEAVE_WARNING = "Videorna i listan är inte klara. Lämnar du sidan kan de gå förlorade. Vill du lämna sidan ändå?";

/** Ett klick på en länk som byter sida i samma flik. */
function leavesPage(e: MouseEvent): boolean {
  const link = (e.target as Element | null)?.closest?.("a[href]");
  if (e.defaultPrevented || !link) return false;
  return link.getAttribute("target") !== "_blank";
}

/** Varnar innan sidan lämnas medan videor väntar eller laddas upp: vid omladdning och vid klick på en länk i appen. */
function useLeaveWarning(on: boolean) {
  useEffect(() => {
    if (!on) return;
    function beforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = ""; // äldre webbläsare kräver ett värde
    }
    // Före Next.js länkar (fångas på vägen ner), så att ett nej stoppar bytet av sida.
    function click(e: MouseEvent) {
      if (!leavesPage(e)) return;
      if (!window.confirm(LEAVE_WARNING)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [on]);
}

/** Kontrollerar klistermärkets tider. null när de går att använda. */
function stickerProblemText(sticker: string, startS: number, lengthS: number): string | null {
  if (!sticker) return null;
  if (!Number.isFinite(startS) || startS < 0) return "Klistermärkets start får inte vara negativ.";
  const tooShortOrLong = lengthS < 0.5 || lengthS > 10;
  return Number.isFinite(lengthS) && !tooShortOrLong ? null : "Klistermärket ska synas 0,5–10 s.";
}

/** ", där 2 egna klipp redan väntar" i texten överst. */
function alreadyWaiting(approved: number): string {
  if (approved === 0) return "";
  return approved === 1 ? ", där 1 eget klipp redan väntar" : `, där ${approved} egna klipp redan väntar`;
}

function ItemStatus({ item }: { item: Item }) {
  if (item.state === "busy") {
    const percent = item.percent ?? 0;
    return (
      <span className="inline-flex items-center gap-1.5 tabular-nums" role="status">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {percent < 100 ? `Laddar upp ${percent} %` : "Gör om videon…"}
      </span>
    );
  }
  return <>{item.state === "done" ? `Klar: ${item.message}` : (item.message ?? "Väntar")}</>;
}

function ItemRow({
  item,
  burnsTitle,
  onTitle,
  onRemove,
}: {
  item: Item;
  burnsTitle: boolean;
  onTitle: (title: string) => void;
  onRemove: () => void;
}) {
  const failed = item.state === "error";
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
      <span className="min-w-24 flex-1 truncate font-medium" title={item.file.name}>
        {item.file.name}
      </span>
      <input
        aria-label={`Rubrik för ${item.file.name}`}
        placeholder={burnsTitle ? "Rubrik (bränns in i början, valfri)" : "Rubrik till captionen (valfri)"}
        value={item.title}
        onChange={(e) => onTitle(e.target.value)}
        disabled={item.state === "busy" || item.state === "done"}
        className={cn(field, "mt-0 w-64")}
      />
      <span role={failed ? "alert" : undefined} className={cn("text-xs", failed ? "text-[#fca5a5]" : "text-muted-foreground")}>
        <ItemStatus item={item} />
      </span>
      {item.state !== "busy" && (
        <button type="button" aria-label={`Ta bort ${item.file.name} ur listan`} onClick={onRemove} className="text-muted-foreground hover:text-foreground">
          <X className="size-4" aria-hidden />
        </button>
      )}
    </li>
  );
}

function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
      <span className="flex-1">{error}</span>
      <button type="button" onClick={onRetry} className="inline-flex h-8 items-center rounded-lg border border-[#4d1d1d] px-3 font-medium hover:bg-white/5">
        Försök igen
      </button>
    </div>
  );
}

function emptyText(loadError: string | null, tab: string): string {
  if (loadError) return "Klippen går inte att visa just nu.";
  return tab === "granska" ? "Inga egna videor väntar på granskning." : "Inga egna klipp med den statusen.";
}

/** Dina egna klipp: flikarna och klippen i den valda fliken. */
function OwnClips({
  clips,
  profiles,
  formats,
  loadError,
  onUpdate,
  onReload,
}: {
  clips: CampaignClip[];
  profiles: CampaignProfile[];
  formats: CampaignFormat[];
  loadError: string | null;
  onUpdate: (clip: CampaignClip) => void;
  onReload: () => void;
}) {
  const [tab, setTab] = useState("granska");
  const activeTab = TABS.find((t) => t.id === tab) ?? TABS[0];
  const shown = clips.filter((c) => activeTab.statuses.includes(c.status));
  return (
    <section className="mt-8">
      <h2 className="text-xl font-semibold">Dina egna klipp</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Säg ja till klippet för att skicka det till Att posta. Ett nekat klipp ligger under Nekade, där det går att ångra.
      </p>
      <div role="tablist" aria-label="Visa egna klipp" className="mt-3 flex w-fit max-w-full flex-wrap overflow-hidden rounded-[9px] border border-line text-[13px]">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className="px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
          >
            {t.label} <span className="tabular-nums">{clips.filter((c) => t.statuses.includes(c.status)).length}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="mt-4 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          {emptyText(loadError, tab)}
        </p>
      ) : (
        shown.map((clip, index) => (
          <ClipResultRow
            key={`${clip.id}-${clip.updated}`}
            clip={clip}
            number={index + 1}
            formats={profiles.find((p) => p.name === clip.profile)?.formats ?? formats}
            onUpdate={onUpdate}
            onReload={onReload}
          />
        ))
      )}
    </section>
  );
}

/** Valen för videorna. Samma för alla videor i listan. */
interface Choices {
  mirror: boolean;
  speed: number;
  filter: string;
  brightness: number;
  saturation: number;
  framing: string;
  font: string;
  cut: number;
  overlay: string;
  sticker: string;
  position: StickerPosition;
  stickerStart: string;
  stickerLength: string;
}

const START_CHOICES: Choices = {
  mirror: false,
  speed: 1.1,
  filter: "fresh",
  brightness: 0,
  saturation: 0,
  framing: "fit",
  font: "",
  cut: 0,
  overlay: "",
  sticker: "",
  position: "top_right",
  stickerStart: "0",
  stickerLength: "3",
};

type SetChoice = <K extends keyof Choices>(key: K, value: Choices[K]) => void;

/** Sekunder ur fältet. Robin kan skriva komma. */
const seconds = (text: string) => Number(text.replace(",", "."));

/** Det som skickas till POST /campaign/uploads för en video. */
function uploadForm(item: Item, profile: string, c: Choices): FormData {
  const fields: [string, string | Blob][] = [
    ["file", item.file],
    ["profile", profile],
    ["title", item.title],
    ["mirror", String(c.mirror)],
    ["speed", String(c.speed)],
    ["filter", c.filter],
    ["brightness", String(c.brightness)],
    ["saturation", String(c.saturation)],
    ["framing", c.framing],
    ["font", c.font],
    ["start_cut_s", String(c.cut)],
    ["overlay", c.overlay],
  ];
  if (c.sticker) {
    fields.push(
      ["sticker", c.sticker],
      ["sticker_position", c.position],
      ["sticker_start_s", String(seconds(c.stickerStart))],
      ["sticker_duration_s", String(seconds(c.stickerLength))]
    );
  }
  const form = new FormData();
  for (const [key, value] of fields) form.append(key, value);
  return form;
}

const label = (text: string, id: string) => (
  <label htmlFor={id} className="text-[13px] text-muted-foreground">
    {text}
  </label>
);

function DropZone({ onFiles }: { onFiles: (files: FileList | null) => void }) {
  const [dragging, setDragging] = useState(false);
  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        onFiles(e.dataTransfer.files);
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
          onFiles(e.target.files);
          e.target.value = "";
        }}
      />
    </label>
  );
}

function ProfileField({
  profiles,
  profile,
  onProfile,
  source,
  busy,
}: {
  profiles: CampaignProfile[];
  profile: string;
  onProfile: (name: string) => void;
  source: string | null | undefined;
  busy: boolean;
}) {
  return (
    <div>
      {label("Kampanj", "upload-profile")}
      <select id="upload-profile" value={profile} onChange={(e) => onProfile(e.target.value)} disabled={busy} className={field}>
        {profiles.map((p) => (
          <option key={p.name} value={p.name}>
            {p.streamer || p.name}
          </option>
        ))}
      </select>
      {source && (
        <p className="mt-1.5 flex items-start gap-1.5 rounded-lg border border-[#5a4416] bg-[#1b160b] px-2.5 py-2 text-xs text-warn">
          <Info className="mt-px size-3.5 shrink-0" aria-hidden />
          <span>Kampanjens regel för material: {source}</span>
        </p>
      )}
    </div>
  );
}

interface FieldsProps {
  choices: Choices;
  set: SetChoice;
  busy: boolean;
}

function FramingField({ choices, set, busy }: FieldsProps) {
  return (
    <div role="radiogroup" aria-label="Bild" className="space-y-1">
      <p className="text-[13px] text-muted-foreground">Bild</p>
      <div className="grid grid-cols-2 gap-2">
        {FRAMINGS.map(([value, text, help]) => (
          <label
            key={value}
            className={cn(
              "flex cursor-pointer flex-col gap-0.5 rounded-xl border px-3 py-2 text-sm",
              choices.framing === value ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
            )}
          >
            <span className="flex items-center gap-2">
              <input
                type="radio"
                name="upload-framing"
                value={value}
                checked={choices.framing === value}
                onChange={() => set("framing", value)}
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
  );
}

/** Bild, spegling, hastighet, filter, ljusstyrka och mättnad. */
function PictureFields({ choices, set, busy }: FieldsProps) {
  return (
    <>
      <FramingField choices={choices} set={set} busy={busy} />
      <label className="flex items-center gap-2.5 text-sm">
        <input
          type="checkbox"
          checked={choices.mirror}
          onChange={(e) => set("mirror", e.target.checked)}
          disabled={busy}
          className="size-4 accent-[var(--brand,#7c5cff)]"
        />
        Spegla bilden
      </label>
      <div className="grid grid-cols-2 gap-3">
        <div>
          {label("Hastighet", "upload-speed")}
          <select id="upload-speed" value={choices.speed} onChange={(e) => set("speed", Number(e.target.value))} disabled={busy} className={field}>
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {num.format(s)}x
              </option>
            ))}
          </select>
        </div>
        <div>
          {label("Filter", "upload-filter")}
          <select id="upload-filter" value={choices.filter} onChange={(e) => set("filter", e.target.value)} disabled={busy} className={field}>
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
          ["Ljusstyrka", "upload-brightness", "brightness", 0.3],
          ["Mättnad", "upload-saturation", "saturation", 0.5],
        ] as const
      ).map(([text, id, key, max]) => (
        <div key={id}>
          <p className="flex justify-between gap-3">
            {label(text, id)}
            <span className="text-sm font-medium tabular-nums">{num.format(choices[key])}</span>
          </p>
          <input
            id={id}
            type="range"
            min={-max}
            max={max}
            step={0.01}
            value={choices[key]}
            disabled={busy}
            onChange={(e) => set(key, Number(e.target.value))}
            className="w-full accent-[var(--brand,#7c5cff)]"
          />
        </div>
      ))}
    </>
  );
}

function StickerFields({ opts, choices, set, busy }: FieldsProps & { opts: UploadOptions }) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-line p-3">
      <legend className="px-1 text-[13px] text-muted-foreground">Klistermärke</legend>
      <div>
        {label("Välj klistermärke", "upload-sticker")}
        <select id="upload-sticker" value={choices.sticker} onChange={(e) => set("sticker", e.target.value)} disabled={busy} className={field}>
          <option value="">Inget</option>
          {opts.stickers.map((s) => (
            <option key={s.file} value={s.file}>
              {s.file}
            </option>
          ))}
        </select>
      </div>
      {choices.sticker && (
        <>
          <div>
            {label("Position", "upload-position")}
            <select
              id="upload-position"
              value={choices.position}
              onChange={(e) => set("position", e.target.value as StickerPosition)}
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
                value={choices.stickerStart}
                onChange={(e) => set("stickerStart", e.target.value)}
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
                value={choices.stickerLength}
                onChange={(e) => set("stickerLength", e.target.value)}
                disabled={busy}
                className={field}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">Klistermärket hamnar utanför plattformens säkra zon, där knappar och text ligger.</p>
        </>
      )}
    </fieldset>
  );
}

/** Font, overlay, klipp i början och klistermärke: valen som kampanjen styr. */
function CampaignFields({ opts, choices, set, busy }: FieldsProps & { opts: UploadOptions }) {
  return (
    <>
      <div>
        {label("Font", "upload-font")}
        <select id="upload-font" value={choices.font} onChange={(e) => set("font", e.target.value)} disabled={busy} className={field}>
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
          value={choices.overlay}
          onChange={(e) => set("overlay", e.target.value)}
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
          <span className="text-sm font-medium tabular-nums">{sec.format(choices.cut)} s</span>
        </p>
        <input
          id="upload-cut"
          type="range"
          min={0}
          max={opts.max_start_cut_s}
          step={0.5}
          value={choices.cut}
          disabled={busy}
          onChange={(e) => set("cut", Number(e.target.value))}
          className="w-full accent-[var(--brand,#7c5cff)]"
        />
        <p className="text-xs text-muted-foreground">
          Videon börjar så här många sekunder in. Blir den kortare än kampanjens {sec.format(opts.min_seconds)} s säger appen till.
        </p>
      </div>
      <StickerFields opts={opts} choices={choices} set={set} busy={busy} />
    </>
  );
}

function makeLabel(busy: boolean, waiting: number): string {
  if (busy) return "Gör om…";
  return waiting > 1 ? `Gör om ${waiting} videor` : "Gör om videon";
}

/** Texten under knappen när videorna är klara, med länken till Klippen. */
function MadeNote({ made }: { made: number }) {
  return (
    <p role="status" className="text-xs text-good">
      {made === 1 ? "1 klipp är klart." : `${made} klipp är klara.`} Granska nedan, eller i{" "}
      <Link href="/queue?tab=granska" className="underline underline-offset-4">
        Klippen, Att granska
      </Link>
      .
    </p>
  );
}

function NoCampaign() {
  return (
    <p className="text-xs text-muted-foreground">
      Ingen aktiv kampanj.{" "}
      <Link href="/campaigns" className="underline underline-offset-4 hover:text-foreground">
        Slå på en under Kampanjer
      </Link>
      .
    </p>
  );
}

const errorText = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** Kampanjerna och de egna klippen. ?kampanj=<id> väljer kampanjen, till exempel från kampanjsidan. */
function useUploadData() {
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [profile, setProfile] = useState("");
  const [clips, setClips] = useState<CampaignClip[]>([]);
  // Servern svarar inte: visas i stället för "Ingen aktiv kampanj" och en tom lista.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profilesLoaded, setProfilesLoaded] = useState(false);

  const loadClips = useCallback(() => {
    api.campaign.clips
      .list({ vod_id: "upload" })
      .then(setClips)
      .catch((err) => setLoadError(errorText(err, "Kunde inte hämta de egna videorna")));
  }, []);

  const load = useCallback(() => {
    setLoadError(null);
    const wanted = new URLSearchParams(window.location.search).get("kampanj");
    api.campaign
      .profiles()
      .then((list) => {
        const active = list.filter((p) => p.active);
        setProfiles(active);
        setProfile((current) => current || (active.find((p) => p.name === wanted) ?? active[0])?.name || "");
        setProfilesLoaded(true);
      })
      .catch((err) => setLoadError(errorText(err, "Kunde inte hämta kampanjerna")));
    loadClips();
  }, [loadClips]);

  return { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips };
}

/** Valen som kampanjen erbjuder. En kampanj kan kräva sin egen overlay, så overlayn börjar om vid byte. */
function useUploadOptions(profile: string, set: SetChoice) {
  const [opts, setOpts] = useState<UploadOptions | null>(null);
  useEffect(() => {
    if (!profile) return;
    let live = true;
    setOpts(null);
    set("overlay", "");
    api.campaign
      .uploadOptions(profile)
      .then((o) => live && setOpts(o))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [profile]); // eslint-disable-line react-hooks/exhaustive-deps
  return opts;
}

const isWaiting = (i: Item) => i.state === "waiting" || i.state === "error";

export function UploadsClient() {
  const { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips } = useUploadData();
  const [items, setItems] = useState<Item[]>([]);
  const [choices, setChoices] = useState<Choices>(START_CHOICES);
  const set: SetChoice = useCallback((key, value) => setChoices((c) => ({ ...c, [key]: value })), []);
  const opts = useUploadOptions(profile, set);
  const [busy, setBusy] = useState(false);
  // Klipp som blev klara i den här omgången, för länken till Klippen.
  const [made, setMade] = useState(0);

  useEffect(() => {
    load();
    // Videor som Robin drog in på Hem.
    add(takeHandedOverFiles());
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  function add(files: FileList | File[] | null) {
    if (!files || files.length === 0) return;
    const added = Array.from(files).map((file) => ({ key: `${file.name}-${file.size}-${Math.random()}`, file, title: "", state: "waiting" as const }));
    setItems((list) => [...list, ...added]);
  }

  const update = (key: string, change: Partial<Item>) =>
    setItems((list) => list.map((item) => (item.key === key ? { ...item, ...change } : item)));

  async function makeAll() {
    setBusy(true);
    setMade(0);
    // En i taget: servern kodar om varje video, och det tar datorns kraft.
    for (const item of items.filter(isWaiting)) {
      update(item.key, { state: "busy", message: undefined, percent: 0 });
      try {
        const clip = await uploadWithProgress(uploadForm(item, profile, choices), (percent) => update(item.key, { percent }));
        update(item.key, { state: "done", message: `Klipp #${clip.id}` });
        setMade((n) => n + 1);
        loadClips();
      } catch (err) {
        update(item.key, { state: "error", message: errorText(err, "Gick inte att göra om") });
      }
    }
    setBusy(false);
  }

  const approved = clips.filter((c) => c.status === "godkand").length;
  useLeaveWarning(busy || items.some((i) => i.state === "waiting"));
  const formats = profiles.find((p) => p.name === profile)?.formats ?? [];
  const waiting = items.filter(isWaiting).length;
  // Utan valen ännu: som förut. Kräver kampanjen sin text bränns rubriken inte in.
  const burnsTitle = opts?.hook_text ?? true;
  const stickerProblem = stickerProblemText(choices.sticker, seconds(choices.stickerStart), seconds(choices.stickerLength));

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Egna videor</h1>
      <p className="mt-1.5 text-muted-foreground">
        Dra in videor. Appen gör om dem till 9:16 med valen nedan. Säg ja till ett klipp, så hamnar det i{" "}
        <Link href="/queue?tab=posta" className="text-brand underline-offset-4 hover:underline">
          Klippen, Att posta
        </Link>
        {alreadyWaiting(approved)}.
      </p>

      {loadError && <LoadError error={loadError} onRetry={load} />}

      <section className="mt-6 grid gap-6 rounded-2xl border border-line bg-panel p-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-3">
          <DropZone onFiles={add} />
          {items.length > 0 && !burnsTitle && (
            <p className="text-xs text-muted-foreground">
              Kampanjen bränner inte in rubriken i videon. Rubriken hamnar bara först i captionen.
            </p>
          )}
          {items.length > 0 && (
            <ul className="space-y-2">
              {items.map((item) => (
                <ItemRow
                  key={item.key}
                  item={item}
                  burnsTitle={burnsTitle}
                  onTitle={(title) => update(item.key, { title })}
                  onRemove={() => setItems((list) => list.filter((i) => i.key !== item.key))}
                />
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-4">
          <ProfileField profiles={profiles} profile={profile} onProfile={setProfile} source={opts?.source_allowed} busy={busy} />
          <PictureFields choices={choices} set={set} busy={busy} />
          {opts && <CampaignFields opts={opts} choices={choices} set={set} busy={busy} />}
          <Button className="h-10 w-full" disabled={busy || waiting === 0 || !profile || !!stickerProblem} onClick={makeAll}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
            {makeLabel(busy, waiting)}
          </Button>
          {profilesLoaded && !profile && <NoCampaign />}
          {!busy && made > 0 && <MadeNote made={made} />}
          {stickerProblem && <p className="text-xs text-muted-foreground">{stickerProblem}</p>}
        </div>
      </section>

      <OwnClips
        clips={clips}
        profiles={profiles}
        formats={formats}
        loadError={loadError}
        onUpdate={(changed) => setClips((list) => list.map((c) => (c.id === changed.id ? changed : c)))}
        onReload={loadClips}
      />
    </main>
  );
}
