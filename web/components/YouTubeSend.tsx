"use client";

import { useEffect, useState } from "react";
import { AccountList, pickAccount } from "@/components/ClipAccounts";
import { channelName } from "@/components/YouTubeCard";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, ClipAccounts, YouTubeChannel, YouTubePrivacy, YouTubeSendBody, YouTubeUpload } from "@/lib/types";

const POLL_MS = 10_000;
const TITLE_MAX = 100;
const DESCRIPTION_MAX = 5000;
// Vertikal video upp till 3 minuter blir en Short.
const SHORT_MAX_S = 180;

const PRIVACY: [YouTubePrivacy, string][] = [
  ["public", "Public"],
  ["unlisted", "Unlisted"],
  ["private", "Private"],
];

// YouTube räknar titeln i tecken och beskrivningen i byte.
const chars = (text: string) => Array.from(text).length;
const bytes = (text: string) => new TextEncoder().encode(text).length;

export function youtubeUploadText(upload: YouTubeUpload): string {
  if (upload.status === "processed") return "Done on YouTube.";
  if (upload.status === "failed") return `Failed: ${upload.error ?? "unknown error"}`;
  if (upload.status === "uploading") return "Uploading…";
  return "YouTube is processing the video.";
}

const field = "mt-1 w-full rounded-lg border border-line bg-background px-3 text-sm outline-none focus:border-brand";

/** Klippets senaste uppladdning till YouTube. Medan YouTube behandlar videon: läs om var 10:e sekund, också efter
 * ett misslyckat anrop. Också uploading: servern gör en gammal uploading-rad till failed efter 10 min. */
export function useYouTubeUpload(clipId: number, enabled = true) {
  const [upload, setUpload] = useState<YouTubeUpload | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    api.campaign.clips.youtube(clipId).then(setUpload).catch(() => {});
  }, [clipId, enabled]);

  const processing = upload?.status === "uploading" || upload?.status === "uploaded";
  useEffect(() => {
    if (!processing) return;
    const timer = setTimeout(() => {
      api.campaign.clips
        .youtube(clipId)
        .then((u) => u && setUpload(u))
        .catch(() => {})
        .finally(() => setTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [processing, tick, clipId]);

  return { upload, setUpload };
}

/** Fälten och reglerna för en uppladdning till YouTube. Schemaläggningen använder samma. */
export function useYouTubeForm(clip: CampaignClip, channels: YouTubeChannel[], accounts: ClipAccounts | null = null) {
  const [picked, setChannelId] = useState(channels[0]?.channel_id ?? "");
  // Med flera konton: aldrig en kanal där klippet redan är postat.
  const channelId = pickAccount(accounts, channels.map((c) => c.channel_id), picked);
  const [title, setTitle] = useState(clip.title ?? "");
  const [description, setDescription] = useState(clip.caption ?? "");
  const [privacy, setPrivacy] = useState<YouTubePrivacy | "">("");
  const [forKids, setForKids] = useState(false); // spelklippen är inte gjorda för barn
  const [notify, setNotify] = useState(true);

  // Uppåt, som TikTok: 180,4 s är över gränsen.
  const length = clip.start_s != null && clip.end_s != null ? Math.ceil(clip.end_s - clip.start_s) : null;
  const reason = !channelId
    ? "The clip is already on all the campaign's channels."
    : length != null && length > SHORT_MAX_S
      ? `The clip is ${length} s. A Short can be at most ${SHORT_MAX_S} s.`
      : !title.trim()
        ? "Enter a title."
        : /[<>]/.test(title)
          ? "The title cannot contain < or >."
          : chars(title) > TITLE_MAX
            ? `The title can be at most ${TITLE_MAX} characters.`
            : /[<>]/.test(description)
              ? "The description cannot contain < or >."
              : bytes(description) > DESCRIPTION_MAX
                ? `The description can be at most ${DESCRIPTION_MAX} bytes.`
                : !privacy
                  ? "Choose who can see the clip."
                  : null;
  const body: YouTubeSendBody | null =
    reason || !privacy
      ? null
      : { channel_id: channelId, title: title.trim(), description, privacy, made_for_kids: forKids, notify_subscribers: notify };

  return { channelId, setChannelId, title, setTitle, description, setDescription, privacy, setPrivacy, forKids, setForKids, notify, setNotify, reason, body };
}

/** Kanal, titel, beskrivning, vem som får se, gjort för barn och meddela prenumeranter. */
export function YouTubeFields({ clip, channels, form, accounts = null, children }: {
  clip: CampaignClip;
  channels: YouTubeChannel[];
  form: ReturnType<typeof useYouTubeForm>;
  /** Kampanjens kanaler med läge, när klippet kan ligga på flera kanaler. */
  accounts?: ClipAccounts | null;
  /** Visas mellan kanalen och titeln, till exempel Föreslå ny text. */
  children?: React.ReactNode;
}) {
  const { channelId, setChannelId, title, setTitle, description, setDescription, privacy, setPrivacy, forKids, setForKids, notify, setNotify } = form;
  return (
    <>
      {accounts?.multi ? (
        <AccountList label="Channel" info={accounts} connected={channels.map((c) => c.channel_id)} value={channelId} onChange={setChannelId} />
      ) : channels.length > 1 && (
        <label className="block">
          <span className="text-[13px] text-muted-foreground">Channel</span>
          <select value={channelId} onChange={(e) => setChannelId(e.target.value)} className={cn(field, "h-9 [&>option]:bg-panel")}>
            {channels.map((c) => (
              <option key={c.channel_id} value={c.channel_id}>
                {channelName(c)}
              </option>
            ))}
          </select>
        </label>
      )}
      {children}

      <label className="block">
        <span className="flex justify-between text-[13px] text-muted-foreground">
          Title
          <span className={cn("tabular-nums", chars(title) > TITLE_MAX && "text-bad")}>
            {chars(title)} / {TITLE_MAX}
          </span>
        </span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} className={cn(field, "h-9")} />
      </label>

      <label className="block">
        <span className="flex justify-between text-[13px] text-muted-foreground">
          Description
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
          <span className="text-[13px] text-muted-foreground">Who can see</span>
          <select
            value={privacy}
            onChange={(e) => setPrivacy(e.target.value as YouTubePrivacy)}
            className={cn(field, "h-9 [&>option]:bg-panel")}
          >
            <option value="" disabled>
              Choose…
            </option>
            {PRIVACY.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend className="text-[13px] text-muted-foreground">Made for kids?</legend>
          <div className="mt-2.5 flex gap-5 text-sm">
            {(
              [
                [true, "Yes"],
                [false, "No"],
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
        Notify subscribers
      </label>
    </>
  );
}
