"use client";

import { Info, Lock } from "lucide-react";
import { POSITIONS } from "@/components/VersionDialog";
import { FILTERS, FRAMINGS, SPEEDS, type Choices } from "@/lib/uploads";
import type { DraftOptions } from "@/lib/uploadsApi";
import type { CampaignProfile, StickerPosition } from "@/lib/types";
import { cn } from "@/lib/utils";
import { field, label } from "./ui";

const num = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export type SetChoice = <K extends keyof Choices>(key: K, value: Choices[K]) => void;

interface FieldsProps {
  choices: Choices;
  set: SetChoice;
  busy: boolean;
}

export function ProfileField({
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

function MirrorField({ choices, set, busy }: FieldsProps) {
  return (
    <div>
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
      {choices.mirror && (
        <p className="mt-1 text-xs text-muted-foreground">Appens texter är rättvända. Text i själva videon blir spegelvänd.</p>
      )}
    </div>
  );
}

/** Bild, spegling, hastighet, filter, ljusstyrka och mättnad. */
export function PictureFields({ choices, set, busy }: FieldsProps) {
  return (
    <>
      <FramingField choices={choices} set={set} busy={busy} />
      <MirrorField choices={choices} set={set} busy={busy} />
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

function StickerTimes({ choices, set, busy }: FieldsProps) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div>
        {label("Start (s)", "upload-sticker-start")}
        <input
          id="upload-sticker-start"
          aria-describedby="upload-sticker-times-help"
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
          aria-describedby="upload-sticker-times-help"
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
      <p id="upload-sticker-times-help" className="col-span-2 -mt-1 text-xs text-muted-foreground">
        Tiden i videon, före hastigheten.
      </p>
    </div>
  );
}

function StickerFields({ opts, choices, set, busy }: FieldsProps & { opts: DraftOptions }) {
  return (
    <fieldset className="min-w-0 space-y-3 rounded-xl border border-line p-3">
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
          <StickerTimes choices={choices} set={set} busy={busy} />
          <p className="text-xs text-muted-foreground">Klistermärket hamnar utanför plattformens säkra zon, där knappar och text ligger.</p>
        </>
      )}
    </fieldset>
  );
}

/** Font, overlay och klistermärke: valen som kampanjen styr. Klipp i början är tidslinjens start. */
export function CampaignFields({ opts, choices, set, busy }: FieldsProps & { opts: DraftOptions }) {
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
      <StickerFields opts={opts} choices={choices} set={set} busy={busy} />
    </>
  );
}
