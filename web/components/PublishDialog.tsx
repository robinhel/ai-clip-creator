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
  Platform,
  ScheduleCreate,
  ScheduledPost,
  SchedulePlatform,
  TikTokStatus,
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
  const missing = [!links.content_rewards && "Content Rewards", !links.analytics_form && "analytics-formuläret"].filter(Boolean);
  return (
    <details open={open} className="rounded-lg border border-line px-3 py-2 text-[13px] text-muted-foreground">
      <summary className="cursor-pointer select-none hover:text-foreground">Kampanjens regler ({rules.length})</summary>
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
            Öppna Content Rewards
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
        {links.analytics_form && (
          <a href={links.analytics_form} target="_blank" rel="noreferrer" className={link}>
            Analytics-formuläret
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
      </p>
      {missing.length > 0 && (
        <p className="mt-1">
          Länken till {missing.join(" och ")} saknas.{" "}
          <Link href={`/campaigns/${detail.name}?tab=installningar`} className="underline underline-offset-4 hover:text-foreground">
            Lägg in {missing.length > 1 ? "dem" : "den"} under kampanjens Inställningar
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
      setError(err instanceof Error ? err.message : "Det gick inte att spara länken");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-line bg-background p-3">
      <label className="block text-[13px] text-muted-foreground" htmlFor={`postlank-${clip.id}`}>
        Länk till inlägget på {PLATFORM_LABEL[platform]}
      </label>
      <Input id={`postlank-${clip.id}`} type="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
      <Button className="h-9 w-full" disabled={busy || !url.trim()} onClick={save}>
        {busy ? <Loader2 className="animate-spin" aria-hidden /> : <CheckCircle2 aria-hidden />}
        Spara länken, klippet är postat
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
      <p className="font-medium">Nästa steg</p>
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

interface DialogProps {
  clip: CampaignClip;
  preset: { platform?: Platform; later?: boolean };
  posting: Posting;
  tt: ReturnType<typeof useTikTokUpload>;
  yt: ReturnType<typeof useYouTubeUpload>;
  onUpdate: (clip: CampaignClip) => void;
  onClose: (changed: boolean) => void;
}

/** Ett fönster för all postning: plattform, konto, hur och när. */
function PublishDialog({ clip, preset, posting, tt, yt, onUpdate, onClose }: DialogProps) {
  const { pending, youtube, tiktok, campaign } = posting;
  const rules = campaign?.rules ?? [];
  const [helper, setHelper] = useState(false);
  useEffect(() => {
    api.campaign.preferences.get().then((p) => setHelper(p.submit_helper)).catch(() => setHelper(false));
  }, []);

  const { info: ttInfo } = useClipAccounts(clip.id, "tiktok", allows(clip, "tiktok"));
  const { info: ytInfo } = useClipAccounts(clip.id, "youtube", allows(clip, "youtube_shorts"));
  const multi = (p: Platform) => (p === "tiktok" ? !!ttInfo?.multi : p === "youtube_shorts" ? !!ytInfo?.multi : false);
  const posted = (p: Platform) => (clip.posts ?? []).some((post) => post.platform === p);
  const platforms = PLATFORMS.filter((p) => allows(clip, p));
  const open = platforms.filter((p) => !posted(p) || multi(p));
  const [chosen, setChosen] = useState<Platform | null>(preset.platform ?? null);
  const platform = chosen && open.includes(chosen) ? chosen : open[0];

  // TikTok: det senast valda kontot om det är kopplat, annars det första. Med flera konton: aldrig ett där klippet redan ligger.
  const ttAccounts = tiktok ? tiktokAccounts(tiktok).filter((a) => a.connected) : [];
  const [pickedTt, setPickedTt] = useState(() => localStorage.getItem(PICKED_TIKTOK) ?? "");
  const openId = ttInfo?.multi
    ? pickAccount(ttInfo, ttAccounts.map((a) => a.open_id), pickedTt)
    : ((ttAccounts.find((a) => a.open_id === pickedTt) ?? ttAccounts[0])?.open_id ?? "");
  const ttAccount = ttAccounts.find((a) => a.open_id === openId);
  const channels = youtube?.accounts.filter((c) => c.connected && !c.needs_login) ?? [];
  const ytForm = useYouTubeForm(clip, channels, ytInfo);

  const canDirect = !!ttAccount?.can_publish && !!ttAccount.open_id;
  const limit = tiktok?.pending_limit ?? 5;
  const connect = (name: string) => (
    <>
      {name === "TikTok" && ttAccounts.length > 0 && ttInfo?.multi ? "Klippet ligger redan på alla kampanjens TikTok-konton." : `Inget ${name}-konto är kopplat.`}{" "}
      <Link href="/settings" className="text-brand underline-offset-4 hover:underline">
        Koppla {name}
      </Link>
    </>
  );
  const self = (p: Platform) => ({
    way: "sjalv" as Way,
    label: "Posta själv",
    text: `Appen kopierar captionen, laddar ner filen och öppnar ${STUDIO[p][1]}. Sedan klistrar du in länken här.`,
    off: null,
  });
  const options: { way: Way; label: string; text: string; off: ReactNode | null }[] =
    platform === "tiktok"
      ? (() => {
          const draft = {
            way: "utkast" as Way,
            label: "Som utkast",
            text: "Klippet hamnar i inkorgen i TikTok-appen. Där postar du det med ljud och caption.",
            off: !ttAccount
              ? connect("TikTok")
              : ttAccount.pending_24h >= limit
                ? `${limit} utkast väntar redan i inkorgen för ${accountName(ttAccount)}. Posta eller ta bort något i TikTok-appen först.`
                : null,
          };
          const direct = {
            way: "direkt" as Way,
            label: TIKTOK_AUDITED ? "Publicera direkt" : "Publicera direkt, blir privat",
            text: TIKTOK_AUDITED ? "Appen publicerar klippet på kontot, nu eller vid en tid du väljer." : PRIVATE_UNTIL_AUDITED,
            off: !ttAccount ? connect("TikTok") : !canDirect ? "Kontot får inte publicera direkt. Koppla det igen under Inställningar." : null,
          };
          // Tills TikTok har granskat appen blir ett direkt inlägg privat, så utkast och posta själv kommer först.
          return TIKTOK_AUDITED ? [direct, draft, self("tiktok")] : [draft, self("tiktok"), direct];
        })()
      : platform === "youtube_shorts"
        ? [
            {
              way: "direkt" as Way,
              label: "Ladda upp till YouTube",
              text: "Appen laddar upp klippet som en Short, nu eller vid en tid du väljer.",
              off: channels.length === 0 ? connect("YouTube") : null,
            },
            self("youtube_shorts"),
          ]
        : platform
          ? [self(platform)]
          : [];
  const [wayChosen, setWay] = useState<Way | null>(preset.later && platform !== "instagram_reels" ? "direkt" : null);
  const option = options.find((o) => o.way === wayChosen && !o.off) ?? options.find((o) => !o.off);
  const way = option?.way;
  // Senare: en uppladdning vid tiden, eller en påminnelse om att posta själv på TikTok.
  const canLater = (way === "direkt" && !!platform && !!SCHEDULE[platform]) || (way === "sjalv" && platform === "tiktok");
  const [whenChosen, setWhen] = useState<When>(preset.later ? "senare" : "nu");
  const when: When = canLater ? whenChosen : "nu";
  const [local, setLocal] = useState("");

  const directTt = platform === "tiktok" && way === "direkt";
  const { creator, creatorError } = useCreatorInfo(directTt ? ttAccount?.open_id : undefined);
  const ttForm = useTikTokForm(clip, creator);

  function chooseTikTok(id: string) {
    setPickedTt(id);
    localStorage.setItem(PICKED_TIKTOK, id);
    // Valen beror på kontots creator_info. Robin väljer om, annars kan posten misslyckas.
    ttForm.setPrivacy("");
    ttForm.setAllowComment(false);
    ttForm.setAllowDuet(false);
    ttForm.setAllowStitch(false);
  }

  const schedulePlatform = platform ? SCHEDULE[platform] : undefined;
  const pendingHere = pending.filter((p) => p.platform === schedulePlatform);
  const busyHere = pendingHere.length > 0 && !!platform && !multi(platform);
  const at = stockholmIso(local);
  const reason =
    !platform
      ? "Klippet finns redan på alla kampanjens plattformar."
      : !option
        ? "Välj hur klippet ska postas."
        : busyHere && (way === "direkt" || when === "senare")
          ? `Klippet är redan schemalagt på ${PLATFORM_NAME[schedulePlatform!]}. Avbryt schemat ovanför först.`
          : when === "senare" && !at
            ? "Välj datum och tid."
            : when === "senare" && at && Date.parse(at) <= Date.now()
              ? "Tiden har redan passerat. Välj en senare tid."
              : way === "direkt" && platform === "youtube_shorts"
                ? ytForm.reason
                : directTt
                  ? (creatorError ?? (!creator ? "Läser kontot hos TikTok…" : (ttForm.reason ?? (ttForm.needDisclosure ? NEED_DISCLOSURE : null))))
                  : null;

  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  function copyCaption(): Promise<boolean> {
    // Startas i klicket, medan sidan har fokus; Chrome nekar annars. Alltid den sparade captionen.
    return navigator.clipboard.writeText(clip.caption ?? "").then(
      () => true,
      () => false
    );
  }

  async function postYourself(p: Platform) {
    const copying = copyCaption();
    // Inte "noopener" i window.open: då svarar den alltid null, och felet går inte att visa.
    const tab = window.open(STUDIO[p][0], "_blank");
    if (tab) tab.opener = null;
    const a = document.createElement("a");
    a.href = api.campaign.downloadUrl(clip);
    a.download = "";
    a.click();
    setDone({ kind: "self", copied: await copying, blocked: !tab });
  }

  async function send(call: () => Promise<void>) {
    setSending(true);
    setError(null);
    try {
      await call();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte. Försök igen.");
    } finally {
      setSending(false);
    }
  }

  function go() {
    if (reason || !platform || !way) return;
    if (when === "senare") {
      const body: ScheduleCreate =
        platform === "youtube_shorts" && ytForm.body
          ? { platform: "youtube", at: at!, account: ytForm.body.channel_id, fields: ytForm.body }
          : directTt && ttForm.fields
            ? { platform: "tiktok", at: at!, account: openId, fields: ttForm.fields }
            : { platform: "tiktok", at: at!, ...(openId ? { account: openId } : {}), fields: {} };
      return send(async () => setDone({ kind: "scheduled", post: await api.campaign.clips.schedule(clip.id, body) }));
    }
    if (way === "sjalv") return void postYourself(platform);
    if (way === "utkast") {
      const copying = copyCaption();
      return send(async () => {
        // Ett konto: ingen kropp, servern väljer det. Flera: det valda.
        const result = await api.campaign.clips.sendToTikTok(clip.id, ttAccounts.length > 1 ? { open_id: openId } : undefined);
        tt.setUpload({ ...result, display_name: result.display_name ?? ttAccount?.display_name ?? null });
        setDone({ kind: "draft", copied: await copying, message: result.message ?? null });
      });
    }
    if (platform === "youtube_shorts" && ytForm.body) {
      const body = ytForm.body;
      return send(async () => {
        yt.setUpload(await api.campaign.clips.sendToYouTube(clip.id, body));
        setDone({ kind: "direct" });
      });
    }
    if (directTt && ttForm.fields) {
      const fields = ttForm.fields;
      return send(async () => {
        tt.setUpload(await api.campaign.clips.sendToTikTok(clip.id, { open_id: openId, mode: "direct", ...fields }));
        setDone({ kind: "direct" });
      });
    }
  }

  function saved(updated: CampaignClip) {
    onUpdate(updated);
    onClose(true);
  }

  const action =
    when === "senare"
      ? way === "sjalv"
        ? { icon: <BellRing aria-hidden />, text: "Schemalägg påminnelse" }
        : { icon: <CalendarClock aria-hidden />, text: "Schemalägg" }
      : way === "utkast"
        ? { icon: <Upload aria-hidden />, text: draftWaiting(tt.upload) ? "Skicka utkastet igen" : "Skicka som utkast" }
        : way === "sjalv"
          ? { icon: <Monitor aria-hidden />, text: `Kopiera caption, ladda ner och öppna ${platform ? STUDIO[platform][1] : ""}` }
          : platform === "youtube_shorts"
            ? { icon: <SquarePlay aria-hidden />, text: "Ladda upp till YouTube" }
            : { icon: <Send aria-hidden />, text: TIKTOK_AUDITED ? "Publicera på TikTok" : "Publicera privat på TikTok" };

  const result = (() => {
    if (!done || !platform) return null;
    if (done.kind === "scheduled") {
      return (
        <div className="space-y-3">
          <Good>{done.post.reminder ? "Påminnelsen är schemalagd. Du får en notis på Macen vid tiden." : "Klippet är schemalagt. Appen laddar upp det vid tiden, om Macen är igång."}</Good>
          {done.post.warning && (
            <p data-schedule-warning className="flex items-start gap-1.5 rounded-lg border border-[#3a3018] px-2.5 py-1.5 text-[13px] text-warn/80">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {done.post.warning}
            </p>
          )}
          <Button className="h-10 w-full" variant="outline" onClick={() => onClose(true)}>
            Stäng
          </Button>
        </div>
      );
    }
    if (done.kind === "draft") {
      return (
        <div className="space-y-3">
          <Good>{tt.upload ? tiktokUploadText(tt.upload) : "Utkastet är skickat."} {done.message}</Good>
          <Steps
            items={[
              "Öppna TikTok-appen och utkastet i inkorgen.",
              done.copied ? "Klistra in captionen. Den är kopierad." : <>Klistra in captionen. <CopyButton text={clip.caption ?? ""} label="Kopiera caption" /></>,
              "Posta, kopiera länken till inlägget och klistra in den här.",
            ]}
          />
          <LinkStep clip={clip} platform="tiktok" onSaved={saved} />
        </div>
      );
    }
    if (done.kind === "self") {
      return (
        <div className="space-y-3">
          <Good>
            {done.copied ? "Captionen är kopierad och filen laddas ner." : "Filen laddas ner. Webbläsaren lät inte appen kopiera captionen."}
            {done.blocked ? ` Webbläsaren stoppade den nya fliken. Öppna ${STUDIO[platform][1]} själv.` : ""}
          </Good>
          <Steps
            items={[
              `Dra filen till ${STUDIO[platform][1]}.`,
              done.copied ? "Klistra in captionen." : <>Klistra in captionen. <CopyButton text={clip.caption ?? ""} label="Kopiera caption" /></>,
              "Posta, kopiera länken till inlägget och klistra in den här.",
            ]}
          />
          <LinkStep clip={clip} platform={platform} onSaved={saved} />
        </div>
      );
    }
    // Direkt: läget läses om av raden var 10:e sekund.
    if (platform === "youtube_shorts") {
      const u = yt.upload;
      if (!u) return null;
      return (
        <div className="space-y-3">
          {u.status === "failed" ? (
            <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
              <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
              {youtubeUploadText(u)}
            </p>
          ) : u.status === "processed" ? (
            <Good>Klar på YouTube.</Good>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              {youtubeUploadText(u)} Appen frågar YouTube var 10:e sekund.
            </p>
          )}
          {u.status !== "failed" && u.url && (
            <PostedLink url={u.url} label="Öppna på YouTube" saveable={!helper || u.privacy !== "public"} clip={clip} platform="youtube_shorts" onSaved={saved} />
          )}
        </div>
      );
    }
    const u = tt.upload?.mode === "direct" ? tt.upload : null;
    if (!u) return null;
    return (
      <div className="space-y-3">
        {u.status === "processing" && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Publiceras. Appen frågar TikTok var 10:e sekund.
          </p>
        )}
        {u.status === "failed" && (
          <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
            <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {tiktokUploadText(u)}
          </p>
        )}
        {u.status === "published" && u.post_url && (
          <>
            <Good>Publicerat på TikTok.</Good>
            <PostedLink url={u.post_url} label="Öppna på TikTok" saveable={!helper} clip={clip} platform="tiktok" onSaved={saved} />
          </>
        )}
        {u.status === "published" && !u.post_url && (
          <>
            <Good>Publicerat som privat (Bara jag). Inlägget har ingen länk än.</Good>
            <Steps
              items={[
                "Öppna inlägget i TikTok-appen och gör det offentligt.",
                "Kopiera länken till inlägget och klistra in den här. Sedan kan du skicka in den i Content Rewards.",
              ]}
            />
            <LinkStep clip={clip} platform="tiktok" onSaved={saved} />
          </>
        )}
      </div>
    );
  })();

  return (
    <Dialog open onOpenChange={(o) => !o && !sending && onClose(!!done)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-5 sm:max-w-[820px] sm:p-6">
        <div>
          <DialogTitle className="text-lg font-semibold">Publicera {clip.title || `klipp ${clip.id}`}</DialogTitle>
          <DialogDescription className="mt-1">
            Välj plattform, konto och hur klippet ska ut. Klippet blir Postad när länken till inlägget är sparad.
          </DialogDescription>
        </div>

        <div className="grid gap-6 sm:grid-cols-[200px_minmax(0,1fr)]">
          <video
            src={api.campaign.videoUrl(clip)}
            poster={api.campaign.posterUrl(clip)}
            controls
            playsInline
            preload="metadata"
            aria-label="Förhandsvisning av klippet"
            className="hidden aspect-[9/16] w-full rounded-xl bg-black object-contain sm:block"
          />

          <div className="min-w-0 space-y-4">
            <fieldset disabled={!!done} className="space-y-4">
              <div role="radiogroup" aria-label="Plattform" className="grid gap-2 sm:grid-cols-3">
                {platforms.map((p) => {
                  const off = !open.includes(p);
                  return (
                    <label
                      key={p}
                      className={cn(
                        "flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm transition-colors",
                        platform === p ? "border-brand bg-brand/10" : "border-line",
                        off ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-muted-foreground"
                      )}
                    >
                      <input
                        type="radio"
                        name={`publicera-plattform-${clip.id}`}
                        checked={platform === p}
                        disabled={off}
                        onChange={() => setChosen(p)}
                        className="size-4 accent-[var(--brand,#7c5cff)]"
                      />
                      <span className="font-medium">{PLATFORM_LABEL[p]}</span>
                      {off && <span className="ml-auto text-xs text-muted-foreground">Postat</span>}
                    </label>
                  );
                })}
              </div>

              {platform && <ScheduleLines pending={pendingHere} onChange={posting.onChanged} />}

              <div role="radiogroup" aria-label="Hur" className="space-y-2">
                {options.map((o) => (
                  <label
                    key={o.way}
                    data-way={o.way}
                    className={cn(
                      "flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors",
                      way === o.way ? "border-brand bg-brand/10" : "border-line",
                      o.off ? "cursor-not-allowed" : "cursor-pointer hover:border-muted-foreground"
                    )}
                  >
                    <input
                      type="radio"
                      name={`publicera-hur-${clip.id}`}
                      checked={way === o.way}
                      disabled={!!o.off}
                      onChange={() => setWay(o.way)}
                      className="mt-0.5 size-4 shrink-0 accent-[var(--brand,#7c5cff)]"
                    />
                    <span className="min-w-0">
                      <span className={cn("block font-medium", o.off && "opacity-50")}>{o.label}</span>
                      <span className={cn("block text-xs", o.way === "direkt" && !TIKTOK_AUDITED && platform === "tiktok" ? "text-warn/90" : "text-muted-foreground", o.off && "opacity-50")}>
                        {o.text}
                      </span>
                      {o.off && <span className="mt-0.5 block text-xs text-muted-foreground">{o.off}</span>}
                    </span>
                  </label>
                ))}
              </div>

              {canLater && (
                <div role="radiogroup" aria-label="När" className="flex gap-2">
                  {(["nu", "senare"] as When[]).map((w) => (
                    <label
                      key={w}
                      className={cn(
                        "flex flex-1 cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm",
                        when === w ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
                      )}
                    >
                      <input
                        type="radio"
                        name={`publicera-nar-${clip.id}`}
                        checked={when === w}
                        onChange={() => setWhen(w)}
                        className="size-4 accent-[var(--brand,#7c5cff)]"
                      />
                      {w === "nu" ? "Nu" : way === "sjalv" ? "Senare, med en påminnelse" : "Senare"}
                    </label>
                  ))}
                </div>
              )}
              {when === "senare" && <WhenField value={local} onChange={setLocal} />}

              {platform === "tiktok" && way !== "sjalv" && ttAccounts.length > 1 && (
                ttInfo?.multi ? (
                  <AccountList label="TikTok-konto" info={ttInfo} connected={ttAccounts.map((a) => a.open_id)} value={openId} onChange={chooseTikTok} />
                ) : (
                  <label className="block">
                    <span className="text-[13px] text-muted-foreground">TikTok-konto</span>
                    <select
                      aria-label="TikTok-konto"
                      value={openId}
                      onChange={(e) => chooseTikTok(e.target.value)}
                      className="mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand [&>option]:bg-panel"
                    >
                      {ttAccounts.map((a) => (
                        <option key={a.open_id} value={a.open_id}>
                          {accountName(a)}
                        </option>
                      ))}
                    </select>
                  </label>
                )
              )}

              {directTt && (
                <>
                  <p className="text-sm">
                    {creator ? (
                      <>
                        Postar till: <span className="font-semibold">{creator.nickname ?? (ttAccount ? accountName(ttAccount) : "")}</span>
                        {creator.username && <span className="text-muted-foreground"> (@{creator.username})</span>}
                      </>
                    ) : creatorError ? null : (
                      <span className="text-muted-foreground">Läser kontot hos TikTok…</span>
                    )}
                  </p>
                  {creatorError && (
                    <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
                      {creatorError}
                    </p>
                  )}
                  <SuggestText clipId={clip.id} platform="tiktok" account={openId} info={ttInfo} onText={(v) => ttForm.setTitle(v.caption)} />
                  <TikTokFields clip={clip} creator={creator} form={ttForm} rules={rules} />
                </>
              )}

              {platform === "youtube_shorts" && way === "direkt" && (
                <YouTubeFields clip={clip} channels={channels} form={ytForm} accounts={ytInfo}>
                  <SuggestText
                    clipId={clip.id}
                    platform="youtube"
                    account={ytForm.channelId}
                    info={ytInfo}
                    onText={(v) => {
                      ytForm.setTitle(v.title);
                      ytForm.setDescription(v.description ?? v.caption);
                    }}
                  />
                </YouTubeFields>
              )}

              {campaign && <CampaignRules detail={campaign} />}
            </fieldset>

            {result ?? (
              <div className="space-y-1.5">
                <Button className="h-auto min-h-10 w-full py-2 whitespace-normal" disabled={!!reason || sending} onClick={go}>
                  {sending ? <Loader2 className="animate-spin" aria-hidden /> : action.icon}
                  {sending ? "Skickar…" : action.text}
                </Button>
                {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
                {/* Har Robin redan postat från mobilen räcker det att klistra in länken. */}
                {way === "sjalv" && when === "nu" && platform && (
                  <details className="text-sm">
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Redan postat? Klistra in länken</summary>
                    <div className="mt-2">
                      <LinkStep clip={clip} platform={platform} onSaved={saved} />
                    </div>
                  </details>
                )}
              </div>
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
      setError(err instanceof Error ? err.message : "Kunde inte spara länken");
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
            Spara länken, klippet är postat
          </Button>
        )}
      </div>
      {!saveable && <p className="text-sm text-muted-foreground">Appen sparar inlägget själv. Skicka in länken i Content Rewards inom 30 minuter, på fliken Skicka in.</p>}
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

/** Knappen Publicera i klippraden, med klippets schema och senaste uppladdningar under. */
export function PublishButton({ clip, posting, block, onUpdate }: {
  clip: CampaignClip;
  posting: Posting;
  /** Varför klippet inte kan postas nu, till exempel osparad caption. */
  block: string | null;
  onUpdate: (clip: CampaignClip) => void;
}) {
  const [open, setOpen] = useState<{ platform?: Platform; later?: boolean } | null>(null);
  const tt = useTikTokUpload(clip.id, allows(clip, "tiktok"));
  const yt = useYouTubeUpload(clip.id, allows(clip, "youtube_shorts"));
  const { request, onRequestDone } = posting;
  const free = freePlatforms(clip);
  const first = clip.status === "godkand";

  useEffect(() => {
    if (request?.clipId !== clip.id) return;
    setOpen({ platform: request.platform, later: request.later });
    onRequestDone();
  }, [request, clip.id, onRequestDone]);

  // Ett postat klipp: bara när någon av kampanjens plattformar saknar inlägg.
  if (!first && free.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <Button className="h-9 w-full" disabled={!!block} onClick={() => setOpen({})}>
        <Send aria-hidden />
        {first ? "Publicera" : "Publicera på fler plattformar"}
      </Button>
      {block && <p className="text-xs text-muted-foreground">{block}</p>}
      {!first && <p className="text-xs text-muted-foreground">Saknar inlägg på {free.map((p) => PLATFORM_LABEL[p]).join(" och ")}.</p>}
      <ScheduleLines pending={posting.pending} onChange={posting.onChanged} />
      {tt.upload && (
        <p className={cn("text-xs", tt.upload.status === "failed" ? "text-bad" : "text-muted-foreground")}>
          {tiktokUploadText(tt.upload)}
          {tt.upload.mode === "direct" && tt.upload.post_url && (
            <>
              {" "}
              <a href={tt.upload.post_url} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                Öppna på TikTok
              </a>
            </>
          )}
        </p>
      )}
      {yt.upload && (
        <p className={cn("text-xs", yt.upload.status === "failed" ? "text-bad" : "text-muted-foreground")}>
          YouTube: {youtubeUploadText(yt.upload)}
          {yt.upload.status !== "failed" && yt.upload.url && (
            <>
              {" "}
              <a href={yt.upload.url} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                Öppna på YouTube
              </a>
            </>
          )}
        </p>
      )}
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
