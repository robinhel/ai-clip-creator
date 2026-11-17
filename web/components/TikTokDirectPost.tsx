"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, TikTokCreatorInfo, TikTokPrivacy } from "@/lib/types";

/** TikTok har granskat appen. Innan dess blir varje direkt inlägg privat (Bara jag). Slå på med
 * NEXT_PUBLIC_TIKTOK_AUDITED=1 när TikTok har godkänt appen. */
export const TIKTOK_AUDITED = process.env.NEXT_PUBLIC_TIKTOK_AUDITED === "1";
export const PRIVATE_UNTIL_AUDITED =
  "TikTok har inte granskat appen än. Ett direkt inlägg blir därför privat (Bara jag). Det får ingen länk och går inte att skicka in i Content Rewards förrän du gör det offentligt i TikTok-appen.";

// TikTok granskar texterna på engelska i den här dialogen. De står ordagrant som i TikToks riktlinjer.
const MUSIC_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en";
const BC_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en";
export const NEED_DISCLOSURE = "You need to indicate if your content promotes yourself, a third party, or both.";
const BC_PRIVATE = "Branded content visibility cannot be set to private.";
const TITLE_MAX = 2200;

const PRIVACY_LABEL: Record<TikTokPrivacy, string> = {
  PUBLIC_TO_EVERYONE: "Alla",
  MUTUAL_FOLLOW_FRIENDS: "Vänner",
  FOLLOWER_OF_CREATOR: "Följare",
  SELF_ONLY: "Bara jag",
};

/** TikTok räknar beskrivningen i UTF-16-enheter, som String.length. */
const utf16 = (text: string) => text.length;

function Check({ label, checked, disabled, onChange, title }: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
  title?: string;
}) {
  return (
    <label title={title} className={cn("flex items-center gap-2 text-sm", disabled ? "cursor-not-allowed text-muted-foreground opacity-60" : "cursor-pointer")}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-[var(--brand,#7c5cff)]"
      />
      {label}
    </label>
  );
}

/** Läser kontot färskt hos TikTok, som riktlinjerna kräver före varje post. */
export function useCreatorInfo(openId: string | undefined) {
  const [creator, setCreator] = useState<TikTokCreatorInfo | null>(null);
  const [creatorError, setCreatorError] = useState<string | null>(null);
  useEffect(() => {
    setCreator(null);
    setCreatorError(null);
    if (!openId) return;
    api.campaign.tiktok
      .creatorInfo(openId)
      .then(setCreator)
      .catch((err) => setCreatorError(err instanceof Error ? err.message : "Kunde inte läsa kontot hos TikTok"));
  }, [openId]);
  return { creator, creatorError };
}

/** Fälten och reglerna för Direct Post. Schemaläggningen använder samma. */
export function useTikTokForm(clip: CampaignClip, creator: TikTokCreatorInfo | null) {
  const [title, setTitle] = useState(clip.caption ?? "");
  const [privacy, setPrivacy] = useState<TikTokPrivacy | "">("");
  const [allowComment, setAllowComment] = useState(false);
  const [allowDuet, setAllowDuet] = useState(false);
  const [allowStitch, setAllowStitch] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [yourBrand, setYourBrand] = useState(false);
  const [branded, setBranded] = useState(false);
  // Omslaget i sekunder in i klippet. null: inget valt, TikTok tar första bilden.
  const [cover, setCover] = useState<number | null>(null);

  // Uppåt, som servern: 60,4 s är över en gräns på 60 s.
  const length = clip.start_s != null && clip.end_s != null ? Math.ceil(clip.end_s - clip.start_s) : null;
  const tooLong = !!creator && length != null && length > creator.max_video_post_duration_sec;
  const brandedOn = disclose && branded;
  const reason = !creator
    ? null
    : tooLong
      ? `Klippet är ${length} s. Kontot får posta högst ${creator.max_video_post_duration_sec} s på TikTok.`
      : utf16(title) > TITLE_MAX
        ? `Beskrivningen får vara högst ${TITLE_MAX} tecken.`
        : !privacy
          ? "Välj vem som får se klippet."
          : null;
  const needDisclosure = disclose && !yourBrand && !branded;
  // Fälten som skickas, utan konto och läge. null tills allt är ifyllt.
  const fields =
    !creator || reason || needDisclosure || !privacy
      ? null
      : {
          title,
          privacy_level: privacy,
          allow_comment: allowComment,
          allow_duet: allowDuet,
          allow_stitch: allowStitch,
          brand_organic: disclose && yourBrand,
          brand_content: brandedOn,
          cover_s: cover,
        };

  return {
    title, setTitle, privacy, setPrivacy, allowComment, setAllowComment, allowDuet, setAllowDuet, allowStitch, setAllowStitch,
    disclose, setDisclose, yourBrand, setYourBrand, branded, setBranded, brandedOn, cover, setCover, reason, needDisclosure, fields,
  };
}

const coverSec = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Omslaget: ett reglage för tiden och bilden ur videon vid den tiden. */
function CoverPicker({ clip, value, onChange }: { clip: CampaignClip; value: number | null; onChange: (s: number | null) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [duration, setDuration] = useState(clip.start_s != null && clip.end_s != null ? clip.end_s - clip.start_s : 0);
  // Nedåt till 0,1 s, så att värdet alltid ligger inom klippet som servern kräver.
  const max = Math.max(0, Math.floor(duration * 10) / 10);

  useEffect(() => {
    if (video.current) video.current.currentTime = value ?? 0;
  }, [value]);

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line p-2.5">
      <video
        ref={video}
        data-cover
        src={api.campaign.videoUrl(clip)}
        poster={value == null ? api.campaign.posterUrl(clip) : undefined}
        muted
        playsInline
        preload="auto"
        aria-hidden
        onLoadedMetadata={(e) => {
          if (!duration) setDuration(e.currentTarget.duration);
          e.currentTarget.currentTime = value ?? 0;
        }}
        className={cn("aspect-[9/16] w-14 shrink-0 rounded-md bg-black object-cover", value == null && "opacity-60")}
      />
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="flex items-baseline justify-between gap-2 text-[13px] text-muted-foreground">
          <label htmlFor={`omslag-${clip.id}`}>Omslag</label>
          {value == null ? (
            <span className="text-xs">Inget valt. TikTok tar första bilden.</span>
          ) : (
            <span className="flex items-baseline gap-2">
              <span className="text-sm font-medium text-foreground tabular-nums">{coverSec.format(value)} s</span>
              <button type="button" onClick={() => onChange(null)} className="text-xs underline-offset-4 hover:text-foreground hover:underline">
                Ta bort valet
              </button>
            </span>
          )}
        </p>
        <input
          id={`omslag-${clip.id}`}
          type="range"
          min={0}
          max={max}
          step={0.1}
          value={value ?? 0}
          disabled={max === 0}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full accent-[var(--brand,#7c5cff)]"
        />
      </div>
    </div>
  );
}

/** Kampanjens regler om kommentarer och gillningar, till exempel "Kommentarer och gilla-markeringar ska vara påslagna". */
export const interactionRules = (rules: string[]) => rules.filter((r) => /kommentar|gilla|likes|comment/i.test(r));

/** Beskrivning, vem som får se, kommentarer, duett, stitch, märkning och avtalstexten, ordagrant som TikTok kräver. */
export function TikTokFields({ clip, creator, form, rules = [] }: {
  clip: CampaignClip;
  creator: TikTokCreatorInfo | null;
  form: ReturnType<typeof useTikTokForm>;
  /** Kampanjens regler. De som gäller kommentarer och gillningar visas vid rutorna. */
  rules?: string[];
}) {
  const {
    title, setTitle, privacy, setPrivacy, allowComment, setAllowComment, allowDuet, setAllowDuet, allowStitch, setAllowStitch,
    disclose, setDisclose, yourBrand, setYourBrand, branded, setBranded, brandedOn,
  } = form;
  return (
    <>
      <label className="block">
        <span className="flex justify-between text-[13px] text-muted-foreground">
          Beskrivning
          <span className={cn("tabular-nums", utf16(title) > TITLE_MAX && "text-bad")}>
            {utf16(title)} / {TITLE_MAX}
          </span>
        </span>
        <textarea
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          rows={4}
          className="mt-1 w-full resize-y rounded-xl border border-line bg-background px-3 py-2 text-sm outline-none focus:border-brand"
        />
      </label>

      <CoverPicker clip={clip} value={form.cover} onChange={form.setCover} />

      <label className="block">
        <span className="text-[13px] text-muted-foreground">Vem får se</span>
        <select
          value={privacy}
          onChange={(e) => {
            setPrivacy(e.target.value as TikTokPrivacy);
            // Branded content går inte ihop med Bara jag, också när reglaget är av och rutan dold.
            if (e.target.value === "SELF_ONLY") setBranded(false);
          }}
          disabled={!creator}
          className="mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand [&>option]:bg-panel"
        >
          <option value="" disabled>
            Välj…
          </option>
          {creator?.privacy_level_options.map((p) => {
            const off = p === "SELF_ONLY" && brandedOn;
            return (
              <option key={p} value={p} disabled={off} title={off ? BC_PRIVATE : undefined}>
                {PRIVACY_LABEL[p] ?? p}
              </option>
            );
          })}
        </select>
        {brandedOn && creator?.privacy_level_options.includes("SELF_ONLY") && (
          <span className="mt-1 block text-xs text-muted-foreground">{BC_PRIVATE}</span>
        )}
        {/* Listan följer TikToks val utan förval, men en ogranskad app får bara posta privat. */}
        {!TIKTOK_AUDITED && <span className="mt-1 block text-xs text-warn/90">Inlägget blir privat (Bara jag) tills TikTok har granskat appen.</span>}
      </label>

      <div className="space-y-1.5">
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <Check label="Allow comment" checked={allowComment} disabled={!creator || creator.comment_disabled} onChange={setAllowComment} />
          <Check label="Allow duet" checked={allowDuet} disabled={!creator || creator.duet_disabled} onChange={setAllowDuet} />
          <Check label="Allow stitch" checked={allowStitch} disabled={!creator || creator.stitch_disabled} onChange={setAllowStitch} />
        </div>
        {/* TikTok kräver att rutorna är av från början. Kampanjens regel står här, så att Robin slår på dem själv. */}
        {interactionRules(rules).map((rule) => (
          <p key={rule} data-campaign-rule className="text-xs text-warn/90">
            Kampanjens regel: {rule}
          </p>
        ))}
      </div>

      <div className="rounded-xl border border-line p-3">
        <button
          type="button"
          role="switch"
          aria-checked={disclose}
          onClick={() => setDisclose(!disclose)}
          className="flex w-full items-center justify-between gap-3 text-sm font-medium"
        >
          Disclose video content
          <span className={cn("flex h-5 w-9 items-center rounded-full border px-0.5 transition-colors", disclose ? "border-brand bg-brand" : "border-line bg-[#1c1c22]")}>
            <span className={cn("size-3.5 rounded-full bg-white transition-transform", disclose && "translate-x-4")} />
          </span>
        </button>
        {disclose && (
          <div className="mt-3 space-y-2">
            <Check label="Your brand" checked={yourBrand} onChange={setYourBrand} />
            <Check
              label="Branded content"
              checked={branded}
              disabled={privacy === "SELF_ONLY"}
              title={privacy === "SELF_ONLY" ? BC_PRIVATE : undefined}
              onChange={setBranded}
            />
            {privacy === "SELF_ONLY" && <p className="text-xs text-muted-foreground">{BC_PRIVATE}</p>}
            {(branded || yourBrand) && (
              <p className="text-xs text-warn">
                {branded ? "Your video will be labeled as 'Paid partnership'" : "Your video will be labeled as 'Promotional content'"}
              </p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        {brandedOn ? (
          <>
            By posting, you agree to TikTok&apos;s{" "}
            <a href={BC_URL} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-4">
              Branded Content Policy
            </a>{" "}
            and{" "}
            <a href={MUSIC_URL} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-4">
              Music Usage Confirmation
            </a>
          </>
        ) : (
          <>
            By posting, you agree to TikTok&apos;s{" "}
            <a href={MUSIC_URL} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-4">
              Music Usage Confirmation
            </a>
          </>
        )}
      </p>
    </>
  );
}
