"use client";

import { useState } from "react";
import { Info, Loader2, Plus } from "lucide-react";
import { AUDIOS, MUSIC_ACCEPT, formatSeconds, musicFileProblem, volumeWarning, type Choices } from "@/lib/uploads";
import { uploadMusic, type DraftOptions } from "@/lib/uploadsApi";
import { cn } from "@/lib/utils";
import type { SetChoice } from "./ChoiceFields";
import { errorText, field, label } from "./ui";
import type { UploadedMusic } from "./useUploadData";

interface Props {
  opts: DraftOptions;
  profile: string;
  choices: Choices;
  set: SetChoice;
  busy: boolean;
  /** En uppladdad låt: den läggs i kampanjens lista, om kampanjen är densamma. Returnerar om den lades till. */
  onMusicAdded: (uploaded: UploadedMusic) => boolean;
}

function AudioChoice({ choices, set, busy }: Pick<Props, "choices" | "set" | "busy">) {
  return (
    <div role="radiogroup" aria-label="Ljud" className="grid grid-cols-3 gap-2">
      {AUDIOS.map(([value, text]) => (
        <label
          key={value}
          className={cn(
            "flex cursor-pointer items-center gap-2 rounded-xl border px-2.5 py-2 text-sm",
            choices.audio === value ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
          )}
        >
          <input
            type="radio"
            name="upload-audio"
            value={value}
            checked={choices.audio === value}
            onChange={() => set("audio", value)}
            disabled={busy}
            className="size-4 shrink-0 accent-[var(--brand,#7c5cff)]"
          />
          <span className="font-medium">{text}</span>
        </label>
      ))}
    </div>
  );
}

type Added = (uploaded: UploadedMusic) => void;

/** Uppladdningen av en låt. Fel filtyp nekas innan uppladdningen. percent null: ingen uppladdning pågår. */
function useMusicUpload(profile: string, onAdded: Added) {
  const [percent, setPercent] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  function pick(file: File | undefined) {
    if (!file) return;
    const problem = musicFileProblem(file.name);
    setError(problem);
    if (problem) return;
    setPercent(0);
    uploadMusic({ file, profile }, setPercent)
      .done.then((music) => onAdded({ to: profile, music }))
      .catch((err) => setError(errorText(err, "Musiken gick inte att ladda upp.")))
      .finally(() => setPercent(null));
  }

  return { percent, error, pick };
}

/** Knappen Lägg till musik, med förloppet i procent. */
function AddMusic({ profile, busy, onAdded }: { profile: string; busy: boolean; onAdded: Added }) {
  const { percent, error, pick } = useMusicUpload(profile, onAdded);
  const uploading = percent != null;
  return (
    <div>
      <label
        className={cn(
          "inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-line px-3 text-sm font-medium hover:bg-white/5",
          (busy || uploading) && "pointer-events-none opacity-60"
        )}
      >
        {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Plus className="size-4" aria-hidden />}
        {uploading ? `Laddar upp ${percent} %` : "Lägg till musik"}
        <input
          type="file"
          accept={MUSIC_ACCEPT}
          className="sr-only"
          disabled={busy || uploading}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </label>
      {error && (
        <p role="alert" className="mt-1 text-xs break-words text-[#fca5a5]">
          {error}
        </p>
      )}
    </div>
  );
}

function MusicList({ opts, choices, set, busy }: Pick<Props, "opts" | "choices" | "set" | "busy">) {
  const music = opts.music ?? [];
  return (
    <div>
      {label("Låt", "upload-music")}
      <select id="upload-music" value={choices.music} onChange={(e) => set("music", e.target.value)} disabled={busy} className={field}>
        <option value="">{music.length ? "Välj en låt" : "Kampanjen har ingen musik än"}</option>
        {music.map((m) => (
          <option key={m.file} value={m.file}>
            {m.file} ({formatSeconds(m.duration_s)} s)
          </option>
        ))}
      </select>
    </div>
  );
}

function MusicStart({ choices, set, busy }: Pick<Props, "choices" | "set" | "busy">) {
  return (
    <div>
      {label("Musiken börjar vid (s)", "upload-music-start")}
      <input
        id="upload-music-start"
        type="text"
        inputMode="decimal"
        value={choices.musicStart}
        onChange={(e) => set("musicStart", e.target.value)}
        disabled={busy}
        className={field}
      />
    </div>
  );
}

function OriginalVolume({ opts, choices, set, busy }: Pick<Props, "opts" | "choices" | "set" | "busy">) {
  const warnings = volumeWarning(choices.originalVolume, opts.audio_rules);
  return (
    <div>
      <p className="flex justify-between gap-3">
        {label("Originalljud", "upload-original-volume")}
        <span className="text-sm font-medium tabular-nums">{choices.originalVolume} %</span>
      </p>
      <input
        id="upload-original-volume"
        type="range"
        min={0}
        max={100}
        step={5}
        value={choices.originalVolume}
        disabled={busy}
        onChange={(e) => set("originalVolume", Number(e.target.value))}
        className="w-full accent-[var(--brand,#7c5cff)]"
      />
      {warnings.map((w) => (
        <p key={w} className="text-xs text-warn">
          {w}
        </p>
      ))}
    </div>
  );
}

/** Kampanjens regler om ljud, i en liten ruta vid valet. */
function AudioRules({ rules }: { rules: string[] | undefined }) {
  if (!rules?.length) return null;
  return (
    <div className="flex items-start gap-1.5 rounded-lg border border-[#5a4416] bg-[#1b160b] px-2.5 py-2 text-xs text-warn">
      <Info className="mt-px size-3.5 shrink-0" aria-hidden />
      <div>
        <p>Kampanjens regler om ljud:</p>
        <ul className="mt-0.5 list-disc pl-4">
          {rules.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Ljud: behåll originalljudet, tyst, eller musik ur kampanjens mapp med originalljudet under. */
export function AudioFields({ opts, profile, choices, set, busy, onMusicAdded }: Props) {
  const fieldProps = { choices, set, busy };
  return (
    <fieldset className="min-w-0 space-y-3 rounded-xl border border-line p-3">
      <legend className="px-1 text-[13px] text-muted-foreground">Ljud</legend>
      <AudioChoice {...fieldProps} />
      {choices.audio === "mute" && <p className="text-xs text-muted-foreground">Klippet får ett tyst ljudspår.</p>}
      {choices.audio !== "keep" && <AudioRules rules={opts.audio_rules} />}
      {choices.audio === "music" && (
        <>
          <MusicList opts={opts} {...fieldProps} />
          <AddMusic
            profile={profile}
            busy={busy}
            onAdded={(uploaded) => {
              if (onMusicAdded(uploaded)) set("music", uploaded.music.file);
            }}
          />
          <MusicStart {...fieldProps} />
          <OriginalVolume opts={opts} {...fieldProps} />
        </>
      )}
    </fieldset>
  );
}
