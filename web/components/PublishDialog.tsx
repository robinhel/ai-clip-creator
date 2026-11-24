"use client";

import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BellRing, CalendarClock, CheckCircle2, ExternalLink, Loader2, Monitor, Send, SquarePlay, Upload, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import { AccountList, pickAccount, SuggestText, useClipAccounts } from "@/components/ClipAccounts";
import { CopyButton } from "@/components/CopyButton";
import { PLATFORM_NAME, ScheduleLines, stockholmIso, WhenField } from "@/components/ScheduleDialog";
import { accountName, tiktokAccounts } from "@/components/TikTokCard";
import { NEED_DISCLOSURE, PRIVATE_UNTIL_AUDITED, TIKTOK_AUDITED, TikTokFields, useCreatorInfo, useTikTokForm } from "@/components/TikTokDirectPost";
import { draftWaiting, PICKED_TIKTOK, TIKTOK_STUDIO, tiktokUploadText, useTikTokUpload } from "@/components/TikTokSend";
import { useYouTubeForm, useYouTubeUpload, YouTubeFields, youtubeUploadText } from "@/components/YouTubeSend";
import { api } from "@/lib/api";
import { allows, cn } from "@/lib/utils";
import type {
  CampaignClip,
  CampaignProfileDetail,
  ClipAccounts,
  Platform,
  ScheduleCreate,
  ScheduledPost,
  SchedulePlatform,
  TikTokAccount,
  TikTokCreatorInfo,
  TikTokStatus,
  YouTubeChannel,
  YouTubeStatus,
} from "@/lib/types";

type Way = "utkast" | "direkt" | "sjalv";
type When = "nu" | "senare";

/** Öppna Publicera för ett klipp från ett annat ställe, till exempel Schemalägg igen i listan Schemalagt. */
export interface PublishRequest {
  clipId: number;
  platform?: Platform;
  later?: boolean;
}

/** Det som Klippen skickar med till klippraden. Bara Klippen postar; andra sidor länkar dit. */
export interface Posting {
  /** Klippets schemalagda poster som väntar eller laddas upp nu. */
  pending: ScheduledPost[];
  youtube: YouTubeStatus | null;
  tiktok: TikTokStatus | null;
  campaign: CampaignProfileDetail | null;
  request: PublishRequest | null;
  onRequestDone: () => void;
  /** Läs om schemat och klippen, till exempel efter en uppladdning som servern sparar som inlägg. */
  onChanged: () => void;
}

const PLATFORMS = Object.keys(PLATFORM_LABEL) as Platform[];
const STUDIO: Record<Platform, [string, string]> = {
  tiktok: [TIKTOK_STUDIO, "TikTok Studio"],
  youtube_shorts: ["https://studio.youtube.com", "YouTube Studio"],
  instagram_reels: ["https://www.instagram.com", "Instagram"],
};
const SCHEDULE: Partial<Record<Platform, SchedulePlatform>> = { tiktok: "tiktok", youtube_shorts: "youtube" };

/** Kampanjens plattformar där klippet inte har något inlägg. */
export const freePlatforms = (clip: CampaignClip) =>
  PLATFORMS.filter((p) => allows(clip, p) && !(clip.posts ?? []).some((post) => post.platform === p));

const link = "inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4";

/** Kampanjens regler och länkarna till Content Rewards och analytics-formuläret, hopfällda. */
export function CampaignRules({ detail, open = false }: { detail: CampaignProfileDetail; open?: boolean }) {
  const { rules, approval, links } = detail;
  const missing = [!links.content_rewards && "Content Rewards", !links.analytics_form && "the analytics form"].filter(Boolean);
  return (
    <details open={open} className="rounded-lg border border-line px-3 py-2 text-[13px] text-muted-foreground">
      <summary className="cursor-pointer select-none hover:text-foreground">The campaign's rules ({rules.length})</summary>
      {rules.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
          {rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      )}
      {approval && <p className="mt-1.5">{approval}</p>}
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {links.content_rewards && (
          <a href={links.content_rewards} target="_blank" rel="noreferrer" className={link}>
            Open Content Rewards
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
        {links.analytics_form && (
          <a href={links.analytics_form} target="_blank" rel="noreferrer" className={link}>
            Analytics form
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
      </p>
      {missing.length > 0 && (
        <p className="mt-1">
          The link to {missing.join(" and ")} is missing.{" "}
          <Link href={`/campaigns/${detail.name}?tab=installningar&del=oversikt#lankar`} className="underline underline-offset-4 hover:text-foreground">
            Add {missing.length > 1 ? "them" : "it"} under the campaign's Set up
          </Link>
          .
        </p>
      )}
    </details>
  );
}

/** Länken till inlägget, när Robin har postat själv eller gjort ett privat inlägg offentligt. */
function LinkStep({ clip, platform, onSaved }: { clip: CampaignClip; platform: Platform; onSaved: (clip: CampaignClip) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.campaign.posts.add(clip.id, { platform, url: url.trim() }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the link");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-line bg-background p-3">
      <label className="block text-[13px] text-muted-foreground" htmlFor={`postlank-${clip.id}`}>
        Link to the post on {PLATFORM_LABEL[platform]}
      </label>
      <Input id={`postlank-${clip.id}`} type="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
      <Button className="h-9 w-full" disabled={busy || !url.trim()} onClick={save}>
        {busy ? <Loader2 className="animate-spin" aria-hidden /> : <CheckCircle2 aria-hidden />}
        Save the link, the clip is posted
      </Button>
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

function Good({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="flex items-start gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
      <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <div className="space-y-1 text-sm">
      <p className="font-medium">Next steps</p>
      <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ol>
    </div>
  );
}

type Done =
  | { kind: "draft"; copied: boolean; message: string | null }
  | { kind: "self"; copied: boolean; blocked: boolean }
  | { kind: "direct" }
  | { kind: "scheduled"; post: ScheduledPost };

type Option = { way: Way; label: string; text: string; off: ReactNode | null };
type TikTokForm = ReturnType<typeof useTikTokForm>;
type YouTubeForm = ReturnType<typeof useYouTubeForm>;
type TikTokUploadState = ReturnType<typeof useTikTokUpload>;
type YouTubeUploadState = ReturnType<typeof useYouTubeUpload>;

interface DialogProps {
  clip: CampaignClip;
  preset: { platform?: Platform; later?: boolean };
  posting: Posting;
  tt: TikTokUploadState;
  yt: YouTubeUploadState;
  onUpdate: (clip: CampaignClip) => void;
  onClose: (changed: boolean) => void;
}

const radio = "size-4 accent-[var(--brand,#7c5cff)]";
const errorBox = "rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]";

/** Inget konto att posta på. allPosted: TikTok har konton, men klippet ligger redan på alla. */
function ConnectLink({ name, allPosted = false }: { name: string; allPosted?: boolean }) {
  return (
    <>
      {allPosted ? "The clip is already on all the campaign's TikTok accounts." : `No ${name} account is connected.`}{" "}
      <Link href="/settings" className="text-brand underline-offset-4 hover:underline">
        Connect {name}
      </Link>
    </>
  );
}

const selfOption = (p: Platform): Option => ({
  way: "sjalv",
  label: "Post yourself",
  text: `The app copies the caption, downloads the file and opens ${STUDIO[p][1]}. Then you paste the link here.`,
  off: null,
});

// Tills TikTok har granskat appen blir ett direkt inlägg privat, och valet säger det före klicket.
const TIKTOK_DIRECT = TIKTOK_AUDITED
  ? { label: "Publish directly", text: "The app publishes the clip on the account, now or at a time you choose." }
  : { label: "Publish directly, becomes private", text: PRIVATE_UNTIL_AUDITED };

function tiktokOptions(account: TikTokAccount | undefined, limit: number, allPosted: boolean): Option[] {
  const missing = <ConnectLink name="TikTok" allPosted={allPosted} />;
  const full =
    account && account.pending_24h >= limit
      ? `${limit} drafts are already waiting in the inbox for ${accountName(account)}. Post or delete one in the TikTok app first.`
      : null;
  const canDirect = !!account?.can_publish && !!account.open_id;
  const draft: Option = {
    way: "utkast",
    label: "As a draft",
    text: "The clip goes to the inbox in the TikTok app. There you post it with sound and caption.",
    off: account ? full : missing,
  };
  const direct: Option = {
    way: "direkt",
    ...TIKTOK_DIRECT,
    off: account ? (canDirect ? null : "The account may not publish directly. Connect it again under Settings.") : missing,
  };
  // Tills TikTok har granskat appen kommer utkast och posta själv först.
  return TIKTOK_AUDITED ? [direct, draft, selfOption("tiktok")] : [draft, selfOption("tiktok"), direct];
}

function youtubeOptions(channels: number): Option[] {
  return [
    {
      way: "direkt",
      label: "Upload to YouTube",
      text: "The app uploads the clip as a Short, now or at a time you choose.",
      off: channels === 0 ? <ConnectLink name="YouTube" /> : null,
    },
    selfOption("youtube_shorts"),
  ];
}

function optionsFor(platform: Platform | undefined, tiktok: { account?: TikTokAccount; limit: number; allPosted: boolean }, channels: number): Option[] {
  if (platform === "tiktok") return tiktokOptions(tiktok.account, tiktok.limit, tiktok.allPosted);
  if (platform === "youtube_shorts") return youtubeOptions(channels);
  return platform ? [selfOption(platform)] : [];
}

function whenReason(when: When, at: string | null): string | null {
  if (when !== "senare") return null;
  if (!at) return "Choose a date and time.";
  return Date.parse(at) <= Date.now() ? "That time has already passed. Choose a later time." : null;
}

interface FieldsCheck {
  youtube: string | null;
  /** null när posten inte är ett direkt inlägg på TikTok. */
  tiktok: { creatorError: string | null; creator: TikTokCreatorInfo | null; form: TikTokForm } | null;
}

function fieldsReason({ youtube, tiktok }: FieldsCheck): string | null {
  if (!tiktok) return youtube;
  if (tiktok.creatorError != null) return tiktok.creatorError;
  if (!tiktok.creator) return "Loading the account from TikTok…";
  return tiktok.form.reason ?? (tiktok.form.needDisclosure ? NEED_DISCLOSURE : null);
}

/** Varför knappen inte går att trycka på än, eller null. */
function blockReason(r: {
  platform?: Platform;
  option?: Option;
  when: When;
  at: string | null;
  scheduledOn: string | null;
  fields: FieldsCheck;
}): string | null {
  if (!r.platform) return "The clip is already on all the campaign's platforms.";
  if (!r.option) return "Choose how the clip is posted.";
  if (r.scheduledOn) return `The clip is already scheduled on ${r.scheduledOn}. Cancel the schedule above first.`;
  return whenReason(r.when, r.at) ?? fieldsReason(r.fields);
}

/** Ikonen och texten på knappen. */
function actionFor(when: When, way: Way | undefined, platform: Platform | undefined, draftAgain: boolean): { icon: ReactNode; text: string } {
  if (when === "senare") {
    return way === "sjalv"
      ? { icon: <BellRing aria-hidden />, text: "Schedule reminder" }
      : { icon: <CalendarClock aria-hidden />, text: "Schedule" };
  }
  if (way === "utkast") return { icon: <Upload aria-hidden />, text: draftAgain ? "Send the draft again" : "Send as draft" };
  if (way === "sjalv") return { icon: <Monitor aria-hidden />, text: `Copy caption, download and open ${platform ? STUDIO[platform][1] : ""}` };
  if (platform === "youtube_shorts") return { icon: <SquarePlay aria-hidden />, text: "Upload to YouTube" };
  return { icon: <Send aria-hidden />, text: TIKTOK_AUDITED ? "Publish on TikTok" : "Publish privately on TikTok" };
}

/** Posten i schemat. Utan fält för TikTok blir den en påminnelse om att posta själv. */
function scheduleBody(at: string, platform: Platform, youtube: YouTubeForm["body"], tiktok: TikTokForm["fields"], openId: string): ScheduleCreate {
  if (platform === "youtube_shorts" && youtube) return { platform: "youtube", at, account: youtube.channel_id, fields: youtube };
  if (tiktok) return { platform: "tiktok", at, account: openId, fields: tiktok };
  return { platform: "tiktok", at, ...(openId ? { account: openId } : {}), fields: {} };
}

/** TikTok: det senast valda kontot om det är kopplat, annars det första. Med flera konton: aldrig ett där klippet redan ligger. */
function useTikTokAccount(tiktok: TikTokStatus | null, info: ClipAccounts | null) {
  const accounts = tiktok ? tiktokAccounts(tiktok).filter((a) => a.connected) : [];
  const [picked, setPicked] = useState(() => localStorage.getItem(PICKED_TIKTOK) ?? "");
  const openId = info?.multi
    ? pickAccount(info, accounts.map((a) => a.open_id), picked)
    : ((accounts.find((a) => a.open_id === picked) ?? accounts[0])?.open_id ?? "");
  function pick(id: string) {
    setPicked(id);
    localStorage.setItem(PICKED_TIKTOK, id);
  }
  return { accounts, openId, account: accounts.find((a) => a.open_id === openId), pick };
}

/** Hjälpen för inskick: med den på sparar servern inlägget själv efter en uppladdning. */
function useSubmitHelper() {
  const [helper, setHelper] = useState(false);
  useEffect(() => {
    api.campaign.preferences.get().then((p) => setHelper(p.submit_helper)).catch(() => setHelper(false));
  }, []);
  return helper;
}

const pasteCaption = (clip: CampaignClip, copied: boolean, copiedText: string): ReactNode =>
  copied ? copiedText : <>Paste the caption. <CopyButton text={clip.caption ?? ""} label="Copy caption" /></>;

function ScheduledDone({ post, onClose }: { post: ScheduledPost; onClose: () => void }) {
  return (
    <div className="space-y-3">
      <Good>{post.reminder ? "The reminder is scheduled. You get a notification on the Mac at that time." : "The clip is scheduled. The app uploads it at that time, if the Mac is on."}</Good>
      {post.warning && (
        <p data-schedule-warning className="flex items-start gap-1.5 rounded-lg border border-[#3a3018] px-2.5 py-1.5 text-[13px] text-warn/80">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {post.warning}
        </p>
      )}
      <Button className="h-10 w-full" variant="outline" onClick={onClose}>
        Close
      </Button>
    </div>
  );
}

function DraftDone({ clip, done, upload, onSaved }: {
  clip: CampaignClip;
  done: Extract<Done, { kind: "draft" }>;
  upload: TikTokUploadState["upload"];
  onSaved: (clip: CampaignClip) => void;
}) {
  return (
    <div className="space-y-3">
      <Good>{upload ? tiktokUploadText(upload) : "The draft is sent."} {done.message}</Good>
      <Steps
        items={[
          "Open the TikTok app and the draft in the inbox.",
          pasteCaption(clip, done.copied, "Paste the caption. It is copied."),
          "Post, copy the link to the post and paste it here.",
        ]}
      />
      <LinkStep clip={clip} platform="tiktok" onSaved={onSaved} />
    </div>
  );
}

function SelfDone({ clip, platform, done, onSaved }: {
  clip: CampaignClip;
  platform: Platform;
  done: Extract<Done, { kind: "self" }>;
  onSaved: (clip: CampaignClip) => void;
}) {
  const studio = STUDIO[platform][1];
  return (
    <div className="space-y-3">
      <Good>
        {done.copied ? "The caption is copied and the file is downloading." : "The file is downloading. The browser did not let the app copy the caption."}
        {done.blocked ? ` The browser blocked the new tab. Open ${studio} yourself.` : ""}
      </Good>
      <Steps items={[`Drag the file to ${studio}.`, pasteCaption(clip, done.copied, "Paste the caption."), "Post, copy the link to the post and paste it here."]} />
      <LinkStep clip={clip} platform={platform} onSaved={onSaved} />
    </div>
  );
}

function Failed({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      {children}
    </p>
  );
}

function Waiting({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {children}
    </p>
  );
}

/** Direkt till YouTube. Läget läses om av raden var 10:e sekund. */
function YouTubeDirectDone({ clip, upload, helper, onSaved }: {
  clip: CampaignClip;
  upload: YouTubeUploadState["upload"];
  helper: boolean;
  onSaved: (clip: CampaignClip) => void;
}) {
  if (!upload) return null;
  if (upload.status === "failed") return <Failed>{youtubeUploadText(upload)}</Failed>;
  return (
    <div className="space-y-3">
      {upload.status === "processed" ? <Good>Done on YouTube.</Good> : <Waiting>{youtubeUploadText(upload)} The app asks YouTube every 10 seconds.</Waiting>}
      {upload.url && (
        <PostedLink url={upload.url} label="Open on YouTube" saveable={!helper || upload.privacy !== "public"} clip={clip} platform="youtube_shorts" onSaved={onSaved} />
      )}
    </div>
  );
}

/** Direkt till TikTok. Utan granskning blir inlägget privat och får ingen länk förrän Robin gör det offentligt. */
function TikTokDirectDone({ clip, upload, helper, onSaved }: {
  clip: CampaignClip;
  upload: TikTokUploadState["upload"];
  helper: boolean;
  onSaved: (clip: CampaignClip) => void;
}) {
  if (upload?.mode !== "direct") return null;
  if (upload.status === "processing") return <Waiting>Publishing. The app asks TikTok every 10 seconds.</Waiting>;
  if (upload.status === "failed") return <Failed>{tiktokUploadText(upload)}</Failed>;
  if (upload.status !== "published") return null;
  if (upload.post_url) {
    return (
      <div className="space-y-3">
        <Good>Published on TikTok.</Good>
        <PostedLink url={upload.post_url} label="Open on TikTok" saveable={!helper} clip={clip} platform="tiktok" onSaved={onSaved} />
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <Good>Published as private (Only me). The post has no link yet.</Good>
      <Steps
        items={[
          "Open the post in the TikTok app and make it public.",
          "Copy the link to the post and paste it here. Then you can submit it in Content Rewards.",
        ]}
      />
      <LinkStep clip={clip} platform="tiktok" onSaved={onSaved} />
    </div>
  );
}

/** Det som hände efter klicket, med nästa steg. */
function DoneView({ done, platform, clip, tt, yt, helper, onSaved, onClose }: {
  done: Done;
  platform: Platform;
  clip: CampaignClip;
  tt: TikTokUploadState;
  yt: YouTubeUploadState;
  helper: boolean;
  onSaved: (clip: CampaignClip) => void;
  onClose: () => void;
}) {
  if (done.kind === "scheduled") return <ScheduledDone post={done.post} onClose={onClose} />;
  if (done.kind === "draft") return <DraftDone clip={clip} done={done} upload={tt.upload} onSaved={onSaved} />;
  if (done.kind === "self") return <SelfDone clip={clip} platform={platform} done={done} onSaved={onSaved} />;
  if (platform === "youtube_shorts") return <YouTubeDirectDone clip={clip} upload={yt.upload} helper={helper} onSaved={onSaved} />;
  return <TikTokDirectDone clip={clip} upload={tt.upload} helper={helper} onSaved={onSaved} />;
}

function PlatformPicker({ clipId, platforms, open, value, onChoose }: {
  clipId: number;
  platforms: Platform[];
  open: Platform[];
  value?: Platform;
  onChoose: (p: Platform) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Platform" className="grid gap-2 sm:grid-cols-3">
      {platforms.map((p) => {
        const off = !open.includes(p);
        return (
          <label
            key={p}
            className={cn(
              "flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm transition-colors",
              value === p ? "border-brand bg-brand/10" : "border-line",
              off ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-muted-foreground"
            )}
          >
            <input type="radio" name={`publicera-plattform-${clipId}`} checked={value === p} disabled={off} onChange={() => onChoose(p)} className={radio} />
            <span className="font-medium">{PLATFORM_LABEL[p]}</span>
            {off && <span className="ml-auto text-xs text-muted-foreground">Posted</span>}
          </label>
        );
      })}
    </div>
  );
}

function WayChoice({ clipId, option, checked, warn, onChoose }: {
  clipId: number;
  option: Option;
  checked: boolean;
  /** Texten är en varning, som att ett direkt inlägg blir privat. */
  warn: boolean;
  onChoose: (way: Way) => void;
}) {
  const dim = option.off ? "opacity-50" : undefined;
  return (
    <label
      data-way={option.way}
      className={cn(
        "flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors",
        checked ? "border-brand bg-brand/10" : "border-line",
        option.off ? "cursor-not-allowed" : "cursor-pointer hover:border-muted-foreground"
      )}
    >
      <input
        type="radio"
        name={`publicera-hur-${clipId}`}
        checked={checked}
        disabled={!!option.off}
        onChange={() => onChoose(option.way)}
        className={cn("mt-0.5 shrink-0", radio)}
      />
      <span className="min-w-0">
        <span className={cn("block font-medium", dim)}>{option.label}</span>
        <span className={cn("block text-xs", warn ? "text-warn/90" : "text-muted-foreground", dim)}>{option.text}</span>
        {option.off && <span className="mt-0.5 block text-xs text-muted-foreground">{option.off}</span>}
      </span>
    </label>
  );
}

function WhenPicker({ clipId, value, reminder, onChoose }: { clipId: number; value: When; reminder: boolean; onChoose: (w: When) => void }) {
  const labels: Record<When, string> = { nu: "Now", senare: reminder ? "Later, with a reminder" : "Later" };
  return (
    <div role="radiogroup" aria-label="When" className="flex gap-2">
      {(["nu", "senare"] as When[]).map((w) => (
        <label
          key={w}
          className={cn(
            "flex flex-1 cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm",
            value === w ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
          )}
        >
          <input type="radio" name={`publicera-nar-${clipId}`} checked={value === w} onChange={() => onChoose(w)} className={radio} />
          {labels[w]}
        </label>
      ))}
    </div>
  );
}

/** Kontot på TikTok, bara när det finns fler än ett. Med flera konton per kampanj: listan som visar var klippet redan ligger. */
function TikTokAccountPicker({ accounts, info, value, onChoose }: {
  accounts: TikTokAccount[];
  info: ClipAccounts | null;
  value: string;
  onChoose: (id: string) => void;
}) {
  if (accounts.length <= 1) return null;
  if (info?.multi) return <AccountList label="TikTok account" info={info} connected={accounts.map((a) => a.open_id)} value={value} onChange={onChoose} />;
  return (
    <label className="block">
      <span className="text-[13px] text-muted-foreground">TikTok account</span>
      <select
        aria-label="TikTok account"
        value={value}
        onChange={(e) => onChoose(e.target.value)}
        className="mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand [&>option]:bg-panel"
      >
        {accounts.map((a) => (
          <option key={a.open_id} value={a.open_id}>
            {accountName(a)}
          </option>
        ))}
      </select>
    </label>
  );
}

function CreatorLine({ creator, creatorError, account }: { creator: TikTokCreatorInfo | null; creatorError: string | null; account?: TikTokAccount }) {
  if (creatorError) return <p role="alert" className={errorBox}>{creatorError}</p>;
  if (!creator) return <p className="text-sm text-muted-foreground">Loading the account from TikTok…</p>;
  return (
    <p className="text-sm">
      Posting to: <span className="font-semibold">{creator.nickname ?? (account ? accountName(account) : "")}</span>
      {creator.username && <span className="text-muted-foreground"> (@{creator.username})</span>}
    </p>
  );
}

/** Ett direkt inlägg på TikTok: kontot hos TikTok, förslag på text och fälten med kampanjens regler. */
function TikTokDirect({ clip, creator, creatorError, account, openId, info, form, rules }: {
  clip: CampaignClip;
  creator: TikTokCreatorInfo | null;
  creatorError: string | null;
  account?: TikTokAccount;
  openId: string;
  info: ClipAccounts | null;
  form: TikTokForm;
  rules: string[];
}) {
  return (
    <>
      <CreatorLine creator={creator} creatorError={creatorError} account={account} />
      <SuggestText clipId={clip.id} platform="tiktok" account={openId} info={info} onText={(v) => form.setTitle(v.caption)} />
      <TikTokFields clip={clip} creator={creator} form={form} rules={rules} />
    </>
  );
}

/** En uppladdning till YouTube: kanal, förslag på text och fälten. */
function YouTubeDirect({ clip, channels, form, info }: { clip: CampaignClip; channels: YouTubeChannel[]; form: YouTubeForm; info: ClipAccounts | null }) {
  return (
    <YouTubeFields clip={clip} channels={channels} form={form} accounts={info}>
      <SuggestText
        clipId={clip.id}
        platform="youtube"
        account={form.channelId}
        info={info}
        onText={(v) => {
          form.setTitle(v.title);
          form.setDescription(v.description ?? v.caption);
        }}
      />
    </YouTubeFields>
  );
}

/** Knappen, varför den inte går än, och länken för den som redan har postat från mobilen. */
function SendArea({ clip, action, sending, reason, linkPlatform, onGo, onSaved }: {
  clip: CampaignClip;
  action: { icon: ReactNode; text: string };
  sending: boolean;
  reason: string | null;
  /** Plattformen för Redan postat, bara när Robin postar själv nu. */
  linkPlatform: Platform | null;
  onGo: () => void;
  onSaved: (clip: CampaignClip) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Button className="h-auto min-h-10 w-full py-2 whitespace-normal" disabled={!!reason || sending} onClick={onGo}>
        {sending ? <Loader2 className="animate-spin" aria-hidden /> : action.icon}
        {sending ? "Sending…" : action.text}
      </Button>
      {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
      {linkPlatform && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Already posted? Paste the link</summary>
          <div className="mt-2">
            <LinkStep clip={clip} platform={linkPlatform} onSaved={onSaved} />
          </div>
        </details>
      )}
    </div>
  );
}

/** Kopierar den sparade captionen. Startas i klicket, medan sidan har fokus; Chrome nekar annars. */
function copyCaption(clip: CampaignClip): Promise<boolean> {
  return navigator.clipboard.writeText(clip.caption ?? "").then(
    () => true,
    () => false
  );
}

/** Posta själv: kopiera captionen, ladda ner filen och öppna studion. Svarar om captionen kopierades och om fliken stoppades. */
async function postYourself(clip: CampaignClip, p: Platform): Promise<Done> {
  const copying = copyCaption(clip);
  // Inte "noopener" i window.open: då svarar den alltid null, och felet går inte att visa.
  const tab = window.open(STUDIO[p][0], "_blank");
  if (tab) tab.opener = null;
  const a = document.createElement("a");
  a.href = api.campaign.downloadUrl(clip);
  a.download = "";
  a.click();
  return { kind: "self", copied: await copying, blocked: !tab };
}

/** Ett fönster för all postning: plattform, konto, hur och när. */
function PublishDialog({ clip, preset, posting, tt, yt, onUpdate, onClose }: DialogProps) {
  const { pending, youtube, tiktok, campaign } = posting;
  const helper = useSubmitHelper();

  const { info: ttInfo } = useClipAccounts(clip.id, "tiktok", allows(clip, "tiktok"));
  const { info: ytInfo } = useClipAccounts(clip.id, "youtube", allows(clip, "youtube_shorts"));
  const multiInfo: Partial<Record<Platform, ClipAccounts | null>> = { tiktok: ttInfo, youtube_shorts: ytInfo };
  const multi = (p: Platform) => !!multiInfo[p]?.multi;
  const posted = (p: Platform) => (clip.posts ?? []).some((post) => post.platform === p);
  const platforms = PLATFORMS.filter((p) => allows(clip, p));
  const open = platforms.filter((p) => !posted(p) || multi(p));
  const [chosen, setChosen] = useState<Platform | null>(preset.platform ?? null);
  const platform = chosen && open.includes(chosen) ? chosen : open[0];

  const ttAcc = useTikTokAccount(tiktok, ttInfo);
  const channels = youtube?.accounts.filter((c) => c.connected && !c.needs_login) ?? [];
  const ytForm = useYouTubeForm(clip, channels, ytInfo);

  const ttState = { account: ttAcc.account, limit: tiktok?.pending_limit ?? 5, allPosted: ttAcc.accounts.length > 0 && !!ttInfo?.multi };
  const options = optionsFor(platform, ttState, channels.length);
  const [wayChosen, setWay] = useState<Way | null>(preset.later && platform !== "instagram_reels" ? "direkt" : null);
  const option = options.find((o) => o.way === wayChosen && !o.off) ?? options.find((o) => !o.off);
  const way = option?.way;
  // Senare: en uppladdning vid tiden, eller en påminnelse om att posta själv på TikTok.
  const schedulePlatform = platform ? SCHEDULE[platform] : undefined;
  const canLater = (way === "direkt" && !!schedulePlatform) || (way === "sjalv" && platform === "tiktok");
  const [whenChosen, setWhen] = useState<When>(preset.later ? "senare" : "nu");
  const when: When = canLater ? whenChosen : "nu";
  const [local, setLocal] = useState("");

  const directTt = platform === "tiktok" && way === "direkt";
  const { creator, creatorError } = useCreatorInfo(directTt ? ttAcc.account?.open_id : undefined);
  const ttForm = useTikTokForm(clip, creator);

  function chooseTikTok(id: string) {
    ttAcc.pick(id);
    // Valen beror på kontots creator_info. Robin väljer om, annars kan posten misslyckas.
    ttForm.setPrivacy("");
    ttForm.setAllowComment(false);
    ttForm.setAllowDuet(false);
    ttForm.setAllowStitch(false);
  }

  const pendingHere = pending.filter((p) => p.platform === schedulePlatform);
  const busyHere = pendingHere.length > 0 && !!platform && !multi(platform) && (way === "direkt" || when === "senare");
  const at = stockholmIso(local);
  const ytDirect = way === "direkt" && platform === "youtube_shorts";
  const reason = blockReason({
    platform,
    option,
    when,
    at,
    scheduledOn: busyHere ? PLATFORM_NAME[schedulePlatform!] : null,
    fields: { youtube: ytDirect ? ytForm.reason : null, tiktok: directTt ? { creatorError, creator, form: ttForm } : null },
  });

  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  async function send(call: () => Promise<Done>) {
    setSending(true);
    setError(null);
    try {
      setDone(await call());
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work. Try again.");
    } finally {
      setSending(false);
    }
  }

  async function sendDraft(): Promise<Done> {
    const copying = copyCaption(clip);
    // Ett konto: ingen kropp, servern väljer det. Flera: det valda.
    const result = await api.campaign.clips.sendToTikTok(clip.id, ttAcc.accounts.length > 1 ? { open_id: ttAcc.openId } : undefined);
    tt.setUpload({ ...result, display_name: result.display_name ?? ttAcc.account?.display_name ?? null });
    return { kind: "draft", copied: await copying, message: result.message ?? null };
  }

  async function sendDirect(): Promise<Done> {
    if (ytDirect && ytForm.body) yt.setUpload(await api.campaign.clips.sendToYouTube(clip.id, ytForm.body));
    else if (directTt && ttForm.fields) tt.setUpload(await api.campaign.clips.sendToTikTok(clip.id, { open_id: ttAcc.openId, mode: "direct", ...ttForm.fields }));
    return { kind: "direct" };
  }

  function go() {
    if (reason) return;
    if (!platform || !way) return;
    if (when === "senare") {
      const body = scheduleBody(at!, platform, ytForm.body, directTt ? ttForm.fields : null, ttAcc.openId);
      return send(async () => ({ kind: "scheduled", post: await api.campaign.clips.schedule(clip.id, body) }));
    }
    if (way === "sjalv") return void postYourself(clip, platform).then(setDone);
    return send(way === "utkast" ? sendDraft : sendDirect);
  }

  function saved(updated: CampaignClip) {
    onUpdate(updated);
    onClose(true);
  }

  const action = actionFor(when, way, platform, draftWaiting(tt.upload));

  return (
    <Dialog open onOpenChange={(o) => !o && !sending && onClose(!!done)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-5 sm:max-w-[820px] sm:p-6">
        <div>
          <DialogTitle className="text-lg font-semibold">Publish {clip.title || `clip ${clip.id}`}</DialogTitle>
          <DialogDescription className="mt-1">
            Choose platform, account and how the clip goes out. The clip becomes Posted when the link to the post is saved.
          </DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[200px_minmax(0,1fr)]">
          <video
            src={api.campaign.videoUrl(clip)}
            poster={api.campaign.posterUrl(clip)}
            controls
            playsInline
            preload="metadata"
            aria-label="Clip preview"
            className="hidden aspect-[9/16] w-full rounded-xl bg-black object-contain sm:block"
          />

          <div className="min-w-0 space-y-4">
            <fieldset disabled={!!done} className="space-y-4">
              <PlatformPicker clipId={clip.id} platforms={platforms} open={open} value={platform} onChoose={setChosen} />
              {platform && <ScheduleLines pending={pendingHere} onChange={posting.onChanged} />}

              <div role="radiogroup" aria-label="How" className="space-y-2">
                {options.map((o) => (
                  <WayChoice
                    key={o.way}
                    clipId={clip.id}
                    option={o}
                    checked={way === o.way}
                    warn={o.way === "direkt" && !TIKTOK_AUDITED && platform === "tiktok"}
                    onChoose={setWay}
                  />
                ))}
              </div>

              {canLater && <WhenPicker clipId={clip.id} value={when} reminder={way === "sjalv"} onChoose={setWhen} />}
              {when === "senare" && <WhenField value={local} onChange={setLocal} />}

              {platform === "tiktok" && way !== "sjalv" && (
                <TikTokAccountPicker accounts={ttAcc.accounts} info={ttInfo} value={ttAcc.openId} onChoose={chooseTikTok} />
              )}
              {directTt && (
                <TikTokDirect
                  clip={clip}
                  creator={creator}
                  creatorError={creatorError}
                  account={ttAcc.account}
                  openId={ttAcc.openId}
                  info={ttInfo}
                  form={ttForm}
                  rules={campaign?.rules ?? []}
                />
              )}
              {ytDirect && <YouTubeDirect clip={clip} channels={channels} form={ytForm} info={ytInfo} />}

              {campaign && <CampaignRules detail={campaign} />}
            </fieldset>

            {done && platform ? (
              <DoneView done={done} platform={platform} clip={clip} tt={tt} yt={yt} helper={helper} onSaved={saved} onClose={() => onClose(true)} />
            ) : (
              <SendArea
                clip={clip}
                action={action}
                sending={sending}
                reason={reason}
                linkPlatform={way === "sjalv" && when === "nu" && platform ? platform : null}
                onGo={go}
                onSaved={saved}
              />
            )}

            {error && (
              <p role="alert" className={errorBox}>
                {error}
              </p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Länken till inlägget efter en uppladdning. Med hjälpen för inskick sparar servern inlägget själv. */
function PostedLink({ url, label, saveable, clip, platform, onSaved }: {
  url: string;
  label: string;
  saveable: boolean;
  clip: CampaignClip;
  platform: Platform;
  onSaved: (clip: CampaignClip) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.campaign.posts.add(clip.id, { platform, url }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the link");
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <a href={url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", className: "h-9" })}>
          <ExternalLink aria-hidden />
          {label}
        </a>
        {saveable && (
          <Button className="h-9" disabled={busy} onClick={save}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Save the link, the clip is posted
          </Button>
        )}
      </div>
      {!saveable && <p className="text-sm text-muted-foreground">The app saves the post itself. Submit the link in Content Rewards within 30 minutes, on the Submit tab.</p>}
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

function OpenLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <>
      {" "}
      <a href={href} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
        {children}
      </a>
    </>
  );
}

const uploadTone = (failed: boolean) => cn("text-xs", failed ? "text-bad" : "text-muted-foreground");

/** Läget för klippets senaste uppladdning till TikTok, under knappen Publicera. */
function TikTokLine({ upload }: { upload: TikTokUploadState["upload"] }) {
  if (!upload) return null;
  const link = upload.mode === "direct" ? upload.post_url : null;
  return (
    <p className={uploadTone(upload.status === "failed")}>
      {tiktokUploadText(upload)}
      {link && <OpenLink href={link}>Open on TikTok</OpenLink>}
    </p>
  );
}

/** Läget för klippets senaste uppladdning till YouTube, under knappen Publicera. */
function YouTubeLine({ upload }: { upload: YouTubeUploadState["upload"] }) {
  if (!upload) return null;
  const failed = upload.status === "failed";
  return (
    <p className={uploadTone(failed)}>
      YouTube: {youtubeUploadText(upload)}
      {!failed && upload.url && <OpenLink href={upload.url}>Open on YouTube</OpenLink>}
    </p>
  );
}

/** Fönstret för klippet är öppet, med förval. Schemalägg igen i listan Schemalagt öppnar det med en begäran. */
function useRequestedOpen(clipId: number, { request, onRequestDone }: Posting) {
  const [open, setOpen] = useState<DialogProps["preset"] | null>(null);
  useEffect(() => {
    if (request?.clipId !== clipId) return;
    setOpen({ platform: request.platform, later: request.later });
    onRequestDone();
  }, [request, clipId, onRequestDone]);
  return [open, setOpen] as const;
}

/** Knappen Publicera i klippraden, med klippets schema och senaste uppladdningar under. */
export function PublishButton({ clip, posting, block, onUpdate }: {
  clip: CampaignClip;
  posting: Posting;
  /** Varför klippet inte kan postas nu, till exempel osparad caption. */
  block: string | null;
  onUpdate: (clip: CampaignClip) => void;
}) {
  const [open, setOpen] = useRequestedOpen(clip.id, posting);
  const tt = useTikTokUpload(clip.id, allows(clip, "tiktok"));
  const yt = useYouTubeUpload(clip.id, allows(clip, "youtube_shorts"));
  const free = freePlatforms(clip);
  const first = clip.status === "godkand";

  // Ett postat klipp: bara när någon av kampanjens plattformar saknar inlägg.
  if (!first && free.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <Button className="h-auto min-h-9 w-full py-2 whitespace-normal" disabled={!!block} onClick={() => setOpen({})}>
        <Send aria-hidden />
        {first ? "Publish" : "Publish on more platforms"}
      </Button>
      {[block, first ? null : `Missing posts on ${free.map((p) => PLATFORM_LABEL[p]).join(" and ")}.`].map(
        (line) => line && <p key={line} className="text-xs text-muted-foreground">{line}</p>
      )}
      <ScheduleLines pending={posting.pending} onChange={posting.onChanged} />
      <TikTokLine upload={tt.upload} />
      <YouTubeLine upload={yt.upload} />
      {open && (
        <PublishDialog
          clip={clip}
          preset={open}
          posting={posting}
          tt={tt}
          yt={yt}
          onUpdate={onUpdate}
          onClose={(changed) => {
            setOpen(null);
            if (changed) posting.onChanged();
          }}
        />
      )}
    </div>
  );
}
