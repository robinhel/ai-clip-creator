"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ExternalLink, Loader2, XCircle, SquarePlay } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { channelName } from "@/components/YouTubeCard";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, YouTubeChannel, YouTubePrivacy, YouTubeStatus, YouTubeUpload } from "@/lib/types";

const POLL_MS = 10_000;
const TITLE_MAX = 100;
const DESCRIPTION_MAX = 5000;
// Vertikal video upp till 3 minuter blir en Short.
const SHORT_MAX_S = 180;

const PRIVACY: [YouTubePrivacy, string][] = [
  ["public", "Offentlig"],
  ["unlisted", "Olistad"],
  ["private", "Privat"],
];

// YouTube räknar titeln i tecken och beskrivningen i byte.
const chars = (text: string) => Array.from(text).length;
const bytes = (text: string) => new TextEncoder().encode(text).length;

function uploadText(upload: YouTubeUpload): string {
  if (upload.status === "processed") return "Klar på YouTube.";
  if (upload.status === "failed") return `Misslyckades: ${upload.error ?? "okänt fel"}`;
  if (upload.status === "uploading") return "Laddas upp…";
  return "YouTube behandlar videon.";
}

const field = "mt-1 w-full rounded-lg border border-line bg-background px-3 text-sm outline-none focus:border-brand";

interface DialogProps {
  clip: CampaignClip;
  channels: YouTubeChannel[];
  upload: YouTubeUpload | null;
  onSent: (upload: YouTubeUpload) => void;
  onClose: (updated?: CampaignClip) => void;
}

function YouTubeDialog({ clip, channels, upload, onSent, onClose }: DialogProps) {
  const [channelId, setChannelId] = useState(channels[0].channel_id);
  const [title, setTitle] = useState(clip.title ?? "");
  const [description, setDescription] = useState(clip.caption ?? "");
  const [privacy, setPrivacy] = useState<YouTubePrivacy | "">("");
  const [forKids, setForKids] = useState(false); // spelklippen är inte gjorda för barn
  const [notify, setNotify] = useState(true);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<CampaignClip | null>(null);

  // Uppåt, som TikTok: 180,4 s är över gränsen.
  const length = clip.start_s != null && clip.end_s != null ? Math.ceil(clip.end_s - clip.start_s) : null;
  const reason =
    length != null && length > SHORT_MAX_S
      ? `Klippet är ${length} s. En Short får vara högst ${SHORT_MAX_S} s.`
      : !title.trim()
        ? "Skriv en titel."
        : /[<>]/.test(title)
          ? "Titeln får inte innehålla < eller >."
          : chars(title) > TITLE_MAX
            ? `Titeln får vara högst ${TITLE_MAX} tecken.`
            : /[<>]/.test(description)
              ? "Beskrivningen får inte innehålla < eller >."
              : bytes(description) > DESCRIPTION_MAX
                ? `Beskrivningen får vara högst ${DESCRIPTION_MAX} byte.`
                : !privacy
                  ? "Välj vem som får se klippet."
                  : null;

  async function publish() {
    if (reason || !privacy) return;
    setSending(true);
    setError(null);
    try {
      const result = await api.campaign.clips.sendToYouTube(clip.id, {
        channel_id: channelId,
        title: title.trim(),
        description,
        privacy,
        made_for_kids: forKids,
        notify_subscribers: notify,
      });
      setMessage(result.message ?? "YouTube behandlar videon.");
      onSent(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte ladda upp klippet till YouTube");
    } finally {
      setSending(false);
    }
  }

  async function savePostUrl(url: string) {
    setSaving(true);
    setError(null);
    try {
      setSaved(await api.campaign.posts.add(clip.id, { platform: "youtube_shorts", url }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte spara postlänken");
    } finally {
      setSaving(false);
    }
  }

  const result = message ? upload : null;

  return (
    <Dialog open onOpenChange={(open) => !open && !sending && onClose(saved ?? undefined)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[780px]">
        <div>
          <DialogTitle className="text-lg font-semibold">Publicera på YouTube</DialogTitle>
          <DialogDescription className="mt-1">Klippet laddas upp som en Short på kanalen.</DialogDescription>
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
            {result ? (
              <div className="space-y-3">
                {(result.status === "uploading" || result.status === "uploaded") && (
                  <p role="status" className="rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
                    {message}
                  </p>
                )}
                {(result.status === "uploading" || result.status === "uploaded") && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                    {result.status === "uploading" ? "Laddas upp." : "Behandlas."} Appen frågar YouTube var 10:e sekund.
                  </p>
                )}
                {result.status === "processed" && (
                  <p className="flex items-center gap-2 text-sm text-good">
                    <CheckCircle2 className="size-4 shrink-0" aria-hidden />
                    Klar på YouTube.
                  </p>
                )}
                {result.status === "failed" && (
                  <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
                    <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                    Misslyckades: {result.error ?? "YouTube sa inte varför."}
                  </p>
                )}
                {result.status !== "failed" && result.url && (
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={result.url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", className: "h-9" })}>
                      <ExternalLink aria-hidden />
                      Öppna på YouTube
                    </a>
                    {saved ? (
                      <span className="flex items-center gap-1.5 text-sm text-good">
                        <CheckCircle2 className="size-4" aria-hidden />
                        Sparad som postlänk.
                      </span>
                    ) : (
                      <Button className="h-9" disabled={saving} onClick={() => savePostUrl(result.url!)}>
                        {saving && <Loader2 className="animate-spin" aria-hidden />}
                        Spara som postlänk
                      </Button>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <>
                {channels.length > 1 && (
                  <label className="block">
                    <span className="text-[13px] text-muted-foreground">Kanal</span>
                    <select value={channelId} onChange={(e) => setChannelId(e.target.value)} className={cn(field, "h-9 [&>option]:bg-panel")}>
                      {channels.map((c) => (
                        <option key={c.channel_id} value={c.channel_id}>
                          {channelName(c)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

                <label className="block">
                  <span className="flex justify-between text-[13px] text-muted-foreground">
                    Titel
                    <span className={cn("tabular-nums", chars(title) > TITLE_MAX && "text-bad")}>
                      {chars(title)} / {TITLE_MAX}
                    </span>
                  </span>
                  <input value={title} onChange={(e) => setTitle(e.target.value)} className={cn(field, "h-9")} />
                </label>

                <label className="block">
                  <span className="flex justify-between text-[13px] text-muted-foreground">
                    Beskrivning
                    <span className={cn("tabular-nums", bytes(description) > DESCRIPTION_MAX && "text-bad")}>
                      {bytes(description)} / {DESCRIPTION_MAX}
                    </span>
                  </span>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={4}
                    className={cn(field, "resize-y rounded-xl py-2")}
                  />
                </label>

                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block">
                    <span className="text-[13px] text-muted-foreground">Vem får se</span>
                    <select
                      value={privacy}
                      onChange={(e) => setPrivacy(e.target.value as YouTubePrivacy)}
                      className={cn(field, "h-9 [&>option]:bg-panel")}
                    >
                      <option value="" disabled>
                        Välj…
                      </option>
                      {PRIVACY.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <fieldset>
                    <legend className="text-[13px] text-muted-foreground">Gjort för barn?</legend>
                    <div className="mt-2.5 flex gap-5 text-sm">
                      {(
                        [
                          [true, "Ja"],
                          [false, "Nej"],
                        ] as const
                      ).map(([value, label]) => (
                        <label key={label} className="flex cursor-pointer items-center gap-2">
                          <input
                            type="radio"
                            name={`barn-${clip.id}`}
                            checked={forKids === value}
                            onChange={() => setForKids(value)}
                            className="size-4 accent-[var(--brand,#7c5cff)]"
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                </div>

                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={notify}
                    onChange={(e) => setNotify(e.target.checked)}
                    className="size-4 accent-[var(--brand,#7c5cff)]"
                  />
                  Meddela prenumeranter
                </label>

                <div className="space-y-1.5">
                  <Button className="h-10 w-full" disabled={!!reason || sending} onClick={publish}>
                    {sending ? <Loader2 className="animate-spin" aria-hidden /> : <SquarePlay aria-hidden />}
                    {sending ? "Laddar upp…" : "Publicera på YouTube"}
                  </Button>
                  {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
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

interface Props {
  clip: CampaignClip;
  captionDirty: boolean;
  hasProblems: boolean;
  /** Klippet efter att länken har sparats som postlänk. */
  onUpdate?: (clip: CampaignClip) => void;
}

/** Ladda upp ett godkänt klipp som en Short. Klippets status ändras inte. */
export function YouTubeSend({ clip, captionDirty, hasProblems, onUpdate }: Props) {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [upload, setUpload] = useState<YouTubeUpload | null>(null);
  const [open, setOpen] = useState(false);
  const [pollTick, setPollTick] = useState(0);

  useEffect(() => {
    api.campaign.youtube.status().then(setStatus).catch(() => setFailed(true));
    api.campaign.clips.youtube(clip.id).then(setUpload).catch(() => {});
  }, [clip.id]);

  // Medan YouTube behandlar videon: fråga om läget var 10:e sekund, också efter ett misslyckat anrop.
  // Också uploading: servern gör en gammal uploading-rad till failed efter 10 min.
  const processing = upload?.status === "uploading" || upload?.status === "uploaded";
  useEffect(() => {
    if (!processing) return;
    const timer = setTimeout(() => {
      api.campaign.clips
        .youtube(clip.id)
        .then((u) => u && setUpload(u))
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [processing, pollTick, clip.id]);

  const channels = status?.accounts.filter((c) => c.connected && !c.needs_login) ?? [];
  if (!status && !failed) return null;

  return (
    <div className="space-y-1.5 border-t border-line pt-3">
      {channels.length === 0 ? (
        <>
          <Link href="/settings" className={buttonVariants({ variant: "outline", className: "h-9 w-full" })}>
            <SquarePlay aria-hidden />
            Publicera på YouTube
          </Link>
          <p className="text-xs text-muted-foreground">Ingen YouTube-kanal är kopplad. Koppla en under Inställningar.</p>
        </>
      ) : (
        <Button className="h-9 w-full" variant="outline" disabled={hasProblems || captionDirty} onClick={() => setOpen(true)}>
          <SquarePlay aria-hidden />
          Publicera på YouTube
        </Button>
      )}
      {upload && (
        <p className={cn("text-xs", upload.status === "failed" ? "text-bad" : "text-muted-foreground")}>
          YouTube: {uploadText(upload)}
          {upload.status !== "failed" && upload.url && (
            <>
              {" "}
              <a href={upload.url} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                Öppna på YouTube
              </a>
            </>
          )}
        </p>
      )}
      {open && (
        <YouTubeDialog
          clip={clip}
          channels={channels}
          upload={upload}
          onSent={setUpload}
          onClose={(updated) => {
            setOpen(false);
            if (updated) onUpdate?.(updated);
          }}
        />
      )}
    </div>
  );
}
