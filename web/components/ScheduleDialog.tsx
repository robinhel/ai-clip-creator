"use client";

import { useState } from "react";
import { BellRing, CalendarClock, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { accountName, tiktokAccounts } from "@/components/TikTokCard";
import { NEED_DISCLOSURE, TikTokFields, useCreatorInfo, useTikTokForm } from "@/components/TikTokDirectPost";
import { useYouTubeForm, YouTubeFields } from "@/components/YouTubeSend";
import { api } from "@/lib/api";
import { allows, cn } from "@/lib/utils";
import type { CampaignClip, ScheduleCreate, ScheduledPost, SchedulePlatform, TikTokStatus, YouTubeStatus } from "@/lib/types";

const TZ = "Europe/Stockholm";
export const PLATFORM_NAME: Record<SchedulePlatform, string> = { youtube: "YouTube", tiktok: "TikTok" };

const short = new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const long = new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, dateStyle: "full", timeStyle: "short" });
const parts = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

/** "3 feb. 18:30" i svensk tid, var webbläsaren än står. */
export const atText = (iso: string) => short.format(new Date(iso));

/** Stockholms avvikelse från UTC i minuter vid en viss tidpunkt. */
function offsetMin(ms: number): number {
  const p = Object.fromEntries(parts.formatToParts(ms).map((x) => [x.type, Number(x.value)]));
  return (Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - ms) / 60000;
}

/** "2027-01-15T18:00" i svensk tid till "2027-01-15T18:00:00+01:00". null om värdet inte är en tid. */
export function stockholmIso(local: string): string | null {
  const m = local.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return null;
  const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  // Två steg, så att en tid nära bytet till sommartid får rätt avvikelse.
  const off = offsetMin(wall - offsetMin(wall) * 60000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${m[0]}:00${off < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`;
}

interface DialogProps {
  clip: CampaignClip;
  /** Väntande poster för klippet. En plattform med en väntande post går inte att välja. */
  pending: ScheduledPost[];
  youtube: YouTubeStatus | null;
  tiktok: TikTokStatus | null;
  onClose: (scheduled: boolean) => void;
}

function ScheduleDialog({ clip, pending, youtube, tiktok, onClose }: DialogProps) {
  const channels = youtube?.accounts.filter((c) => c.connected && !c.needs_login) ?? [];
  const accounts = tiktok ? tiktokAccounts(tiktok).filter((a) => a.connected) : [];
  const taken = (p: SchedulePlatform) => pending.some((x) => x.platform === p);
  const ytBlock = !allows(clip, "youtube_shorts")
    ? "Kampanjen postar inte på YouTube."
    : taken("youtube")
      ? "Redan schemalagt."
      : channels.length === 0
        ? "Ingen YouTube-kanal är kopplad."
        : null;
  const ttBlock = !allows(clip, "tiktok") ? "Kampanjen postar inte på TikTok." : taken("tiktok") ? "Redan schemalagt." : null;
  const [platform, setPlatform] = useState<SchedulePlatform>(ytBlock ? "tiktok" : "youtube");
  const [local, setLocal] = useState("");
  const [openId, setOpenId] = useState(accounts[0]?.open_id ?? "");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const yt = useYouTubeForm(clip, channels);
  const account = accounts.find((a) => a.open_id === openId);
  // Samma villkor som Publicera direkt: kontot har scopet video.publish. Annars blir det en påminnelse.
  const direct = platform === "tiktok" && !!account?.can_publish && !!account.open_id;
  const { creator, creatorError } = useCreatorInfo(direct ? account?.open_id : undefined);
  const tt = useTikTokForm(clip, creator);

  const at = stockholmIso(local);
  const timeReason = !at ? "Välj datum och tid." : Date.parse(at) <= Date.now() ? "Tiden har redan passerat. Välj en senare tid." : null;
  const formReason =
    platform === "youtube"
      ? ytBlock ?? yt.reason
      : ttBlock
        ? ttBlock
        : !direct
          ? null
          : creatorError
            ? creatorError
            : !creator
              ? "Läser kontot hos TikTok…"
              : tt.reason ?? (tt.needDisclosure ? NEED_DISCLOSURE : null);
  const reason = timeReason ?? formReason;

  async function schedule() {
    if (reason || !at) return;
    const body: ScheduleCreate =
      platform === "youtube" && yt.body
        ? { platform, at, account: yt.body.channel_id, fields: yt.body }
        : direct && tt.fields
          ? { platform, at, account: openId, fields: tt.fields }
          : { platform, at, ...(openId ? { account: openId } : {}), fields: {} };
    setSending(true);
    setError(null);
    try {
      await api.campaign.clips.schedule(clip.id, body);
      onClose(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte schemalägga klippet");
      setSending(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !sending && onClose(false)}>
      <DialogContent className="max-h-[calc(100vh-2rem)] gap-5 overflow-y-auto p-6 sm:max-w-[780px]">
        <div>
          <DialogTitle className="text-lg font-semibold">Schemalägg {clip.title ?? `klipp ${clip.id}`}</DialogTitle>
          <DialogDescription className="mt-1">Appen laddar upp klippet vid tiden, om Macen är igång. Klippet måste vara godkänt och utan problem då också.</DialogDescription>
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
            <div role="radiogroup" aria-label="Plattform" className="grid grid-cols-2 gap-2">
              {(["youtube", "tiktok"] as const).map((p) => {
                const off = p === "youtube" ? ytBlock : ttBlock;
                return (
                  <label
                    key={p}
                    className={cn(
                      "flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors",
                      platform === p ? "border-brand bg-brand/10" : "border-line",
                      off ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-muted-foreground"
                    )}
                  >
                    <input
                      type="radio"
                      name={`plattform-${clip.id}`}
                      checked={platform === p}
                      disabled={!!off}
                      onChange={() => setPlatform(p)}
                      className="size-4 accent-[var(--brand,#7c5cff)]"
                    />
                    <span className="font-medium">{PLATFORM_NAME[p]}</span>
                    {off && <span className="ml-auto text-right text-xs text-muted-foreground">{off}</span>}
                  </label>
                );
              })}
            </div>

            <label className="block">
              <span className="text-[13px] text-muted-foreground">Datum och tid, svensk tid</span>
              <input
                type="datetime-local"
                value={local}
                onChange={(e) => setLocal(e.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-line bg-background px-3 text-sm tabular-nums outline-none [color-scheme:dark] focus:border-brand"
              />
              {at && (
                <span className="mt-1.5 flex items-center gap-1.5 text-sm text-foreground">
                  <CalendarClock className="size-4 text-brand" aria-hidden />
                  {long.format(new Date(at))} svensk tid
                </span>
              )}
            </label>

            {platform === "youtube" ? (
              !ytBlock && <YouTubeFields clip={clip} channels={channels} form={yt} />
            ) : (
              <>
                {accounts.length > 1 && (
                  <label className="block">
                    <span className="text-[13px] text-muted-foreground">Konto</span>
                    <select
                      aria-label="TikTok-konto"
                      value={openId}
                      onChange={(e) => {
                        setOpenId(e.target.value);
                        // Valen beror på kontots creator_info. Robin väljer om, annars kan posten misslyckas vid tiden.
                        tt.setPrivacy("");
                        tt.setAllowComment(false);
                        tt.setAllowDuet(false);
                        tt.setAllowStitch(false);
                      }}
                      className="mt-1 h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand [&>option]:bg-panel"
                    >
                      {accounts.map((a) => (
                        <option key={a.open_id} value={a.open_id}>
                          {accountName(a)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {direct ? (
                  <>
                    <p className="rounded-lg border border-line bg-background px-3 py-2 text-sm">
                      Appen publicerar klippet direkt på TikTok vid tiden.
                      {creator && (
                        <span className="mt-0.5 block text-muted-foreground">
                          Postar till: <span className="font-semibold text-foreground">{creator.nickname ?? accountName(account!)}</span>
                        </span>
                      )}
                    </p>
                    <TikTokFields creator={creator} form={tt} />
                  </>
                ) : (
                  <p className="flex gap-2.5 rounded-lg border border-line bg-background px-3 py-2.5 text-sm">
                    <BellRing className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                    <span>
                      Det blir en påminnelse. {account ? `${accountName(account)} kan inte publicera direkt` : "Inget TikTok-konto kan publicera direkt"}, så
                      vid tiden får du en notis på Macen om att posta klippet själv.
                    </span>
                  </p>
                )}
              </>
            )}

            <div className="space-y-1.5">
              <Button className="h-10 w-full" disabled={!!reason || sending} onClick={schedule}>
                {sending ? <Loader2 className="animate-spin" aria-hidden /> : platform === "tiktok" && !direct ? <BellRing aria-hidden /> : <CalendarClock aria-hidden />}
                {sending ? "Schemalägger…" : platform === "tiktok" && !direct ? "Schemalägg påminnelse" : "Schemalägg"}
              </Button>
              {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
            </div>

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

interface BarProps {
  clip: CampaignClip;
  /** Klippets poster som väntar eller laddas upp just nu. */
  pending: ScheduledPost[];
  youtube: YouTubeStatus | null;
  tiktok: TikTokStatus | null;
  onScheduled: () => void;
}

/** Raden ovanför ett godkänt klipp: när det är schemalagt, och knappen Schemalägg för plattformar som är lediga. */
export function ScheduleBar({ clip, pending, youtube, tiktok, onScheduled }: BarProps) {
  const [open, setOpen] = useState(false);
  // Någon plattform som kampanjen postar på och som inte redan är schemalagd.
  const free = ([["youtube", "youtube_shorts"], ["tiktok", "tiktok"]] as const).some(
    ([p, name]) => allows(clip, name) && !pending.some((x) => x.platform === p)
  );
  const block = clip.problems.length > 0 ? "Rätta problemen först." : null;

  return (
    <div data-schedule-for={clip.id} className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-line bg-panel px-4 py-2.5 text-sm">
      <CalendarClock className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      {pending.length === 0 && <p className="text-muted-foreground">Inte schemalagt.</p>}
      {pending.map((p) => (
        <p key={p.id} className="tabular-nums">
          {p.status === "running" ? `Laddas upp nu på ${PLATFORM_NAME[p.platform]}` : `Schemalagt ${atText(p.at)} på ${PLATFORM_NAME[p.platform]}`}
        </p>
      ))}
      {free && (
        <div className="ml-auto flex items-center gap-3">
          {block && <span className="text-xs text-muted-foreground">{block}</span>}
          <Button size="sm" variant="outline" disabled={!!block} onClick={() => setOpen(true)}>
            Schemalägg
          </Button>
        </div>
      )}
      {open && (
        <ScheduleDialog
          clip={clip}
          pending={pending}
          youtube={youtube}
          tiktok={tiktok}
          onClose={(scheduled) => {
            setOpen(false);
            if (scheduled) onScheduled();
          }}
        />
      )}
    </div>
  );
}
