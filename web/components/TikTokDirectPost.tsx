"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, Send, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { accountName } from "@/components/TikTokCard";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, TikTokAccount, TikTokCreatorInfo, TikTokPrivacy, TikTokUpload } from "@/lib/types";

// TikTok granskar texterna på engelska i den här dialogen. De står ordagrant som i TikToks riktlinjer.
const MUSIC_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en";
const BC_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en";
const NEED_DISCLOSURE = "You need to indicate if your content promotes yourself, a third party, or both.";
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

interface Props {
  clip: CampaignClip;
  account: TikTokAccount;
  /** Senaste uppladdningen, läst om av raden var 10:e sekund medan TikTok publicerar. */
  upload: TikTokUpload | null;
  onSent: (upload: TikTokUpload) => void;
  /** Stäng. Med klippet när det har fått en postlänk, så att raden uppdateras. */
  onClose: (updated?: CampaignClip) => void;
}

/** Publicera ett godkänt klipp direkt på TikTok. Monteras när den öppnas, så creator_info läses färskt varje gång. */
export function TikTokDirectPost({ clip, account, upload, onSent, onClose }: Props) {
  const [creator, setCreator] = useState<TikTokCreatorInfo | null>(null);
  const [creatorError, setCreatorError] = useState<string | null>(null);
  const [title, setTitle] = useState(clip.caption ?? "");
  const [privacy, setPrivacy] = useState<TikTokPrivacy | "">("");
  const [allowComment, setAllowComment] = useState(false);
  const [allowDuet, setAllowDuet] = useState(false);
  const [allowStitch, setAllowStitch] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [yourBrand, setYourBrand] = useState(false);
  const [branded, setBranded] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<CampaignClip | null>(null);

  useEffect(() => {
    api.campaign.tiktok
      .creatorInfo(account.open_id)
      .then(setCreator)
      .catch((err) => setCreatorError(err instanceof Error ? err.message : "Kunde inte läsa kontot hos TikTok"));
  }, [account.open_id]);

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
  const canPublish = !!creator && !reason && !needDisclosure && !sending;

  async function publish() {
    if (!canPublish || !privacy) return;
    setSending(true);
    setError(null);
    try {
      const result = await api.campaign.clips.sendToTikTok(clip.id, {
        open_id: account.open_id,
        mode: "direct",
        title,
        privacy_level: privacy,
        allow_comment: allowComment,
        allow_duet: allowDuet,
        allow_stitch: allowStitch,
        brand_organic: disclose && yourBrand,
        brand_content: brandedOn,
      });
      setMessage(result.message ?? "TikTok publicerar klippet.");
      onSent(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte publicera klippet på TikTok");
    } finally {
      setSending(false);
    }
  }

  async function savePostUrl(url: string) {
    setSaving(true);
    setError(null);
    try {
      setSaved(await api.campaign.posts.add(clip.id, { platform: "tiktok", url }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte spara postlänken");
    } finally {
      setSaving(false);
    }
  }

  // Efter sändningen: bara den här sändningens läge, inte ett äldre utkast.
  const result = message && upload?.mode === "direct" ? upload : null;
  const name = creator?.nickname ?? accountName(account);

  return (
    <Dialog open onOpenChange={(open) => !open && !sending && onClose(saved ?? undefined)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[780px]">
        <div>
          <DialogTitle className="text-lg font-semibold">Publicera på TikTok</DialogTitle>
          <DialogDescription className="mt-1">Klippet publiceras direkt på kontot, utan inkorgen i mobilen.</DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[220px_minmax(0,1fr)]">
          <video
            src={api.campaign.videoUrl(clip)}
            poster={api.campaign.posterUrl(clip)}
            controls
            playsInline
            preload="metadata"
            aria-label="Förhandsvisning av klippet"
            className="aspect-[9/16] w-full rounded-xl bg-black object-contain"
          />

          <div className="min-w-0 space-y-4">
            <div className="flex items-center gap-2.5">
              {creator?.avatar_url ? (
                // eslint-disable-next-line @next/next/no-img-element -- TikToks profilbild.
                <img src={creator.avatar_url} alt="" className="size-9 rounded-full object-cover" />
              ) : (
                <div className="size-9 rounded-full bg-line" />
              )}
              <p className="text-sm">
                {creator ? (
                  <>
                    Postar till: <span className="font-semibold">{name}</span>
                    {creator.username && <span className="text-muted-foreground"> (@{creator.username})</span>}
                  </>
                ) : creatorError ? null : (
                  <span className="text-muted-foreground">Läser kontot hos TikTok…</span>
                )}
              </p>
            </div>
            {creatorError && (
              <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
                {creatorError}
              </p>
            )}

            {result ? (
              <div className="space-y-3">
                <p role="status" className="rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
                  {message}
                </p>
                {result.status === "processing" && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                    Publiceras. Appen frågar TikTok var 10:e sekund.
                  </p>
                )}
                {result.status === "failed" && (
                  <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
                    <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                    Misslyckades: {result.error ?? "TikTok sa inte varför."}
                  </p>
                )}
                {result.status === "published" && !result.post_url && (
                  <p className="flex items-center gap-2 text-sm text-good">
                    <CheckCircle2 className="size-4 shrink-0" aria-hidden />
                    Publicerat. Privata inlägg har ingen offentlig länk.
                  </p>
                )}
                {result.status === "published" && result.post_url && (
                  <p className="flex items-center gap-2 text-sm text-good">
                    <CheckCircle2 className="size-4 shrink-0" aria-hidden />
                    Publicerat på TikTok.
                  </p>
                )}
                {result.status === "published" && result.post_url && (
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={result.post_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", className: "h-9" })}>
                      <ExternalLink aria-hidden />
                      Öppna på TikTok
                    </a>
                    {saved ? (
                      <span className="flex items-center gap-1.5 text-sm text-good">
                        <CheckCircle2 className="size-4" aria-hidden />
                        Sparad som postlänk.
                      </span>
                    ) : (
                      <Button className="h-9" disabled={saving} onClick={() => savePostUrl(result.post_url!)}>
                        {saving && <Loader2 className="animate-spin" aria-hidden />}
                        Spara som postlänk
                      </Button>
                    )}
                  </div>
                )}
              </div>
            ) : (
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
                  <span className="mt-1 block text-xs text-muted-foreground">
                    TikTok har inte granskat appen än. Tills dess fungerar bara Bara jag.
                  </span>
                </label>

                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  <Check label="Allow comment" checked={allowComment} disabled={!creator || creator.comment_disabled} onChange={setAllowComment} />
                  <Check label="Allow duet" checked={allowDuet} disabled={!creator || creator.duet_disabled} onChange={setAllowDuet} />
                  <Check label="Allow stitch" checked={allowStitch} disabled={!creator || creator.stitch_disabled} onChange={setAllowStitch} />
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

                <div className="space-y-1.5">
                  <Button className="h-10 w-full" disabled={!canPublish} title={needDisclosure ? NEED_DISCLOSURE : undefined} onClick={publish}>
                    {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
                    {sending ? "Publicerar…" : "Publicera på TikTok"}
                  </Button>
                  {(reason ?? (needDisclosure && NEED_DISCLOSURE)) && (
                    <p className="text-xs text-muted-foreground">{reason ?? NEED_DISCLOSURE}</p>
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
