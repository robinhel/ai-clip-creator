"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { CheckCircle2, Clock, RefreshCw, Upload, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { ClipResultRow, ignoreKey } from "@/components/ClipResultRow";
import { freePlatforms, type Posting, type PublishRequest } from "@/components/PublishDialog";
import { RemoveFromQueue } from "@/components/RemoveFromQueue";
import { ScheduledList } from "@/components/ScheduledList";
import { api } from "@/lib/api";
import { hasCampaign } from "@/lib/campaignName";
import { pollWhileVisible } from "@/lib/poll";
import { bySubmitTime, countByStatus, defaultStep, isStep, STEP_STATUSES, stepCount, stepOrder, submitDeadline, type ClipStep } from "@/lib/steps";
import { cn, SUMMARY_CHANGED } from "@/lib/utils";
import type {
  CampaignClip,
  CampaignProfile,
  CampaignProfileDetail,
  CampaignRunInfo,
  Platform,
  ScheduledPost,
  TikTokStatus,
  YouTubeStatus,
} from "@/lib/types";

const TABS: Record<ClipStep, { label: string; description: string }> = {
  granska: {
    label: "To review",
    description: "All clips waiting for your yes or no, from all projects and uploads. J and K switch clips, Y is yes and N is no.",
  },
  posta: {
    label: "Ready to post",
    description: "Approved clips. Press Publish and choose an account, now or later, draft, direct or post yourself.",
  },
  "skicka-in": {
    label: "Submit",
    description: "Posted clips. Submit the link in Content Rewards within 30 minutes of saving it.",
  },
};

const POLL_MS = 15_000;
const UPLOAD = "upload";
const PENDING = ["waiting", "running"];
const timeText = new Intl.DateTimeFormat("en-US", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Besked efter ett steg, fast längst ner, med länk till nästa steg och Ångra. */
interface Notice {
  text: string;
  href?: string;
  linkText?: string;
  undo?: () => Promise<CampaignClip>;
}

const back = (clip: CampaignClip, status: CampaignClip["status"]) => () => api.campaign.clips.patch(clip.id, { status });

// Beskedet per stegbyte, som "från>till". Att granska är både renderad och underkand.
const NOTICES: Record<string, (clip: CampaignClip, name: string) => Notice> = {
  "granska>godkand": (clip, name) => ({
    text: `Approved. ${name} is in Ready to post.`,
    href: `/queue?tab=posta#klipp-${clip.id}`,
    linkText: "Open Ready to post",
    undo: back(clip, "renderad"),
  }),
  "granska>nekad": (clip, name) => ({ text: `Rejected. ${name} is under Rejected.`, undo: back(clip, "renderad") }),
  "godkand>postad": (clip) => ({
    text: "Posted. Submit the link in Content Rewards within 30 minutes.",
    href: `/queue?tab=skicka-in#klipp-${clip.id}`,
    linkText: "Open Submit",
  }),
  "postad>inskickad": () => ({ text: "Submitted. Fill in the views on Stats when they come.", href: "/log", linkText: "Open Stats" }),
  "nekad>renderad": (clip, name) => ({ text: `${name} is in To review again.`, href: `/queue?tab=granska#klipp-${clip.id}`, linkText: "Show" }),
};

function transition(prev: CampaignClip, next: CampaignClip): string {
  const from = prev.status === "renderad" || prev.status === "underkand" ? "granska" : prev.status;
  // Efter Postad räknas allt utom Godkänd som inskickat: Inskickad, Analytics inskickad eller Betald.
  const sent = prev.status === "postad" && next.status !== "godkand";
  return `${from}>${sent ? "inskickad" : next.status}`;
}

const noticeFor = (prev: CampaignClip, next: CampaignClip): Notice | null =>
  NOTICES[transition(prev, next)]?.(next, next.title || "The clip") ?? null;

/** Reglerna för kampanjerna som har klipp att granska, posta eller skicka in. En kampanj som inte går att läsa hoppas över. */
async function profileDetails(clips: CampaignClip[]): Promise<CampaignProfileDetail[]> {
  const names = [...new Set(clips.filter((c) => c.status !== "nekad").map((c) => c.profile))].filter(hasCampaign);
  const list = await Promise.all(names.map((name) => api.campaign.profile.get(name).catch(() => null)));
  return list.filter((d): d is CampaignProfileDetail => d !== null);
}

/** J eller K när fokus inte står i ett fält, annars null. */
function navKey(e: KeyboardEvent): "j" | "k" | null {
  const key = e.key.toLowerCase();
  if (ignoreKey(e)) return null;
  return key === "j" || key === "k" ? key : null;
}

export function QueueClient({ tab: tabParam }: { tab?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const [clips, setClips] = useState<CampaignClip[] | null>(null);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [runs, setRuns] = useState<CampaignRunInfo[]>([]);
  const [details, setDetails] = useState<CampaignProfileDetail[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // null: servern har inget schema (äldre server), då döljs delen.
  const [schedule, setSchedule] = useState<ScheduledPost[] | null>(null);
  const [scheduleDone, setScheduleDone] = useState(false);
  const [youtube, setYoutube] = useState<YouTubeStatus | null>(null);
  const [tiktok, setTiktok] = useState<TikTokStatus | null>(null);
  const [showRejected, setShowRejected] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);
  const [request, setRequest] = useState<PublishRequest | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const loadSchedule = useCallback(() => {
    api.campaign.schedule
      .list()
      .then(setSchedule)
      .catch(() => setSchedule(null))
      .finally(() => setScheduleDone(true));
  }, []);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [clipData, profileData, runData] = await Promise.all([
        api.campaign.clips.list(),
        // Formaten och länken till projektet är extra; listan ska visas ändå.
        api.campaign.profiles().catch(() => [] as CampaignProfile[]),
        api.campaign.runs.list().catch(() => [] as CampaignRunInfo[]),
      ]);
      setClips(clipData);
      setProfiles(profileData);
      setRuns(runData);
      setError(null);
      profileDetails(clipData).then(setDetails).catch(() => {});
      loadSchedule();
      // Kontonas namn i schemat och valen i Publicera.
      api.campaign.youtube.status().then(setYoutube).catch(() => setYoutube(null));
      api.campaign.tiktok.status().then(setTiktok).catch(() => setTiktok(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the clips");
    } finally {
      setRefreshing(false);
    }
  }, [loadSchedule]);

  useEffect(() => {
    load();
  }, [load]);

  // Schemat och klippen ändras av servern (uppladdning vid tiden). Läs om dem tyst medan fliken syns.
  // Bara ett ändrat svar byter state, så att raderna inte ritas om och öppna formulär står kvar.
  useEffect(() => {
    const same = <T,>(next: T) => (prev: T) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next);
    return pollWhileVisible(() => {
      api.campaign.schedule.list().then((next) => setSchedule(same<ScheduledPost[] | null>(next))).catch(() => {});
      api.campaign.clips.list().then((next) => setClips(same<CampaignClip[] | null>(next))).catch(() => {});
    }, POLL_MS);
  }, []);

  const counts = useMemo(() => countByStatus(clips ?? []), [clips]);
  const order = stepOrder(counts);
  const tab: ClipStep = isStep(tabParam) ? tabParam : clips ? defaultStep(counts) : "granska";
  const deadline = submitDeadline(clips ?? [], now);

  // Klockan för Skicka in räknar ner medan något väntar.
  useEffect(() => {
    if (deadline == null) return;
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [deadline]);

  // Länkar som /queue?tab=posta#klipp-12 rullar till klippet när listan och schemat har kommit.
  // Schemat står ovanför klippen och trycker annars ner klippet efteråt.
  const scrolled = useRef(false);
  useEffect(() => {
    if (!clips || !scheduleDone || scrolled.current) return;
    scrolled.current = true;
    const id = window.location.hash.match(/^#klipp-(\d+)$/)?.[1];
    if (id) document.getElementById(`klipp-${id}`)?.scrollIntoView({ block: "start" });
  }, [clips, scheduleDone]);

  function openTab(step: ClipStep, clipId?: number) {
    router.replace(`${pathname}?tab=${step}${clipId ? `#klipp-${clipId}` : ""}`, { scroll: !clipId });
    if (clipId) setTimeout(() => document.getElementById(`klipp-${clipId}`)?.scrollIntoView({ block: "start" }), 100);
  }

  function handleUpdate(updated: CampaignClip) {
    const prev = clips?.find((c) => c.id === updated.id);
    setClips((list) => list?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    if (prev && prev.status !== updated.status) {
      setUndoError(null);
      setNotice(noticeFor(prev, updated));
      window.dispatchEvent(new Event(SUMMARY_CHANGED)); // siffrorna i menyn
    }
  }

  // Postad eller Ta bort i Att posta: klippet är postat utan länk eller nekat, och finns kvar i klipploggen.
  // Ångra visas fast längst ner.
  function handleRemoved(clip: CampaignClip, cancelled: number) {
    setClips((list) => list?.map((c) => (c.id === clip.id ? clip : c)) ?? null);
    window.dispatchEvent(new Event(SUMMARY_CHANGED));
    setUndoError(null);
    const done = clip.status === "postad_utan_lank" ? "marked as posted" : "removed from Ready to post";
    setNotice({
      text: `${clip.title || "The clip"} is ${done}.${cancelled > 0 ? " The scheduled upload is cancelled and does not start again on Undo." : ""}`,
      undo: () => api.campaign.clips.patch(clip.id, { status: "godkand" }),
    });
    if (cancelled > 0) loadSchedule();
  }

  async function undo() {
    if (!notice?.undo) return;
    setUndoing(true);
    try {
      const restored = await notice.undo();
      setClips((list) => list?.map((c) => (c.id === restored.id ? restored : c)) ?? null);
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
      setNotice(null);
      setUndoError(null);
    } catch (err) {
      setUndoError(err instanceof Error ? err.message : "Could not undo");
    } finally {
      setUndoing(false);
    }
  }

  function runFor(clip: CampaignClip): CampaignRunInfo | undefined {
    // Senaste projektet för samma VOD och kampanj.
    return runs.filter((r) => r.vod_id === clip.vod_id && r.profile === clip.profile).sort((a, b) => b.run_id - a.run_id)[0];
  }
  function sourceFor(clip: CampaignClip) {
    if (clip.vod_id === UPLOAD) return { label: "Own video", href: "/uploads" };
    const run = runFor(clip);
    return run ? { label: run.title || `VOD ${clip.vod_id}`, href: `/runs/${run.run_id}` } : undefined;
  }

  const onChanged = useCallback(() => {
    loadSchedule();
    api.campaign.clips.list().then(setClips).catch(() => {});
    window.dispatchEvent(new Event(SUMMARY_CHANGED));
  }, [loadSchedule]);
  const onRequestDone = useCallback(() => setRequest(null), []);
  const postingFor = (clip: CampaignClip): Posting => ({
    pending: (schedule ?? []).filter((p) => p.clip_id === clip.id && PENDING.includes(p.status)),
    youtube,
    tiktok,
    campaign: details.find((d) => d.name === clip.profile) ?? null,
    request,
    onRequestDone,
    onChanged,
  });

  function reschedule(post: ScheduledPost) {
    const platform: Platform = post.platform === "youtube" ? "youtube_shorts" : "tiktok";
    const clip = clips?.find((c) => c.id === post.clip_id);
    setRequest({ clipId: post.clip_id, platform, later: true });
    if (clip) openTab(clip.status === "godkand" ? "posta" : "skicka-in", clip.id);
  }
  const canReschedule = (post: ScheduledPost) =>
    clips?.some((c) => c.id === post.clip_id && ["godkand", "postad"].includes(c.status)) ?? false;

  // Att granska: bäst betyg först. Nekade: senast ändrade först.
  const byScore = (a: CampaignClip, b: CampaignClip) => (b.score ?? -1) - (a.score ?? -1) || b.id - a.id;
  const list = (step: ClipStep) => (clips ?? []).filter((c) => STEP_STATUSES[step].includes(c.status));
  const rejected = (clips ?? []).filter((c) => c.status === "nekad").sort((a, b) => b.updated.localeCompare(a.updated));
  const visible =
    tab === "granska"
      ? showRejected
        ? rejected
        : list("granska").sort(byScore)
      : tab === "skicka-in"
        ? list("skicka-in").sort(bySubmitTime(now))
        : list("posta");
  // Postade och inskickade klipp som saknar en av kampanjens plattformar. Hopfällda under Att posta.
  const morePlatforms = (clips ?? []).filter((c) => c.status === "inskickad" && freePlatforms(c).length > 0);

  // J och K flyttar mellan klippen i Att granska, rullar dit och markerar raden.
  const ids = useRef<number[]>([]);
  ids.current = tab === "granska" ? visible.map((c) => c.id) : [];
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const key = navKey(e);
      if (!key || ids.current.length === 0) return;
      e.preventDefault();
      setActiveId((current) => {
        const i = current == null ? -1 : ids.current.indexOf(current);
        const next = key === "j" ? Math.min(i + 1, ids.current.length - 1) : Math.max(i - 1, 0);
        const id = ids.current[next];
        document.getElementById(`klipp-${id}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
        return id;
      });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const minutesLeft = deadline != null ? Math.ceil((deadline - now) / 60_000) : null;
  const row = (clip: CampaignClip) => (
    <ClipResultRow
      // Ny nyckel när servern har sparat, så att fälten visar det sparade.
      key={`${clip.id}-${clip.updated}`}
      clip={clip}
      formats={profiles.find((p) => p.name === clip.profile)?.formats ?? []}
      runId={runFor(clip)?.run_id}
      source={sourceFor(clip)}
      active={tab === "granska" && clip.id === activeId}
      onUpdate={handleUpdate}
      onReload={load}
      posting={postingFor(clip)}
      approvedAction={
        clip.status === "godkand" ? (
          <RemoveFromQueue
            clip={clip}
            pending={(schedule ?? []).filter((p) => p.clip_id === clip.id && PENDING.includes(p.status))}
            onRemoved={handleRemoved}
          />
        ) : undefined
      }
    />
  );

  const empty = (text: ReactNode) => (
    <div className="mt-4 rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">{text}</div>
  );
  const linkClass = "text-brand underline-offset-4 hover:underline";

  return (
    <main className="w-full max-w-[1180px] px-4 py-6 pb-28 sm:px-6 md:px-11 md:py-9">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Clips</h1>
          <p className="mt-1.5 text-muted-foreground">Review, post and submit the clips from all projects and uploads.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {/* Egna videor finns inte i menyn. Härifrån når Robin sidan där han lägger in ett eget klipp. */}
          <Link href="/uploads" className={buttonVariants({ variant: "outline" })}>
            <Upload aria-hidden />
            Add own clip
          </Link>
          <Button variant="outline" onClick={load} disabled={refreshing}>
            <RefreshCw className={refreshing ? "animate-spin" : undefined} aria-hidden />
            Refresh
          </Button>
        </div>
      </div>

      <div role="tablist" aria-label="Steps" className="mt-6 flex gap-1 overflow-x-auto border-b border-line [scrollbar-width:none]">
        {order.map((step) => {
          const n = stepCount(counts, step);
          return (
            <button
              key={step}
              type="button"
              role="tab"
              aria-selected={tab === step}
              onClick={() => {
                setShowRejected(false);
                openTab(step);
              }}
              className={cn(
                "-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2.5 text-sm whitespace-nowrap transition-colors sm:gap-2 sm:px-3",
                tab === step ? "border-brand text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              {TABS[step].label}
              <span className={cn("rounded-full px-1.5 text-xs tabular-nums", n > 0 ? "bg-panel text-foreground" : "text-muted-foreground")}>{n}</span>
              {step === "skicka-in" && minutesLeft != null && minutesLeft > 0 && (
                // Smal skärm: klockan står i rutan under flikarna och i menyn, så att alla tre flikar får plats.
                <span className="hidden items-center gap-0.5 text-xs text-violet-200 tabular-nums sm:inline-flex">
                  <Clock className="size-3" aria-hidden />
                  {minutesLeft} min
                </span>
              )}
            </button>
          );
        })}
      </div>
      <p className="mt-3 text-sm text-muted-foreground">{TABS[tab].description}</p>

      {error && (
        <div
          role="alert"
          className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-4 text-sm text-[#fca5a5]"
        >
          <span>{error}</span>
          <Button size="sm" variant="outline" onClick={load}>
            Try again
          </Button>
        </div>
      )}

      {clips === null && !error && <p className="mt-6 text-sm text-muted-foreground">Loading clips…</p>}

      {clips && tab === "granska" && (
        <div role="group" aria-label="Visa" className="mt-4 inline-flex overflow-hidden rounded-lg border border-line text-sm">
          {[false, true].map((rej) => (
            <button
              key={String(rej)}
              type="button"
              aria-pressed={showRejected === rej}
              onClick={() => setShowRejected(rej)}
              className="px-3 py-1.5 text-muted-foreground hover:text-foreground aria-pressed:bg-panel aria-pressed:text-foreground"
            >
              {rej ? `Rejected (${rejected.length})` : `To review (${list("granska").length})`}
            </button>
          ))}
        </div>
      )}

      {clips && tab === "skicka-in" && deadline != null && (
        <p className="mt-4 flex items-center gap-2 rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-sm text-violet-100">
          <Clock className="size-4 shrink-0" aria-hidden />
          The first link must be submitted before {timeText.format(deadline)} ({minutesLeft} min left).
        </p>
      )}

      {clips && tab === "posta" && schedule && schedule.length > 0 && (
        <ScheduledList
          posts={schedule}
          youtube={youtube}
          tiktok={tiktok}
          onChange={onChanged}
          onReschedule={(post) => (canReschedule(post) ? reschedule(post) : undefined)}
        />
      )}

      {clips &&
        (visible.length > 0 ? (
          <section aria-label={showRejected ? "Rejected" : TABS[tab].label} className="mt-2">
            {visible.map(row)}
          </section>
        ) : tab === "granska" ? (
          empty(
            showRejected ? (
              "No rejected clips."
            ) : (
              <>
                No clips to review. Get clips from a stream on{" "}
                <Link href="/" className={linkClass}>
                  Home
                </Link>{" "}
                or upload your{" "}
                <Link href="/uploads" className={linkClass}>
                  own video
                </Link>
                .
              </>
            )
          )
        ) : tab === "posta" ? (
          empty(
            <>
              Nothing to post right now. Clips you say yes to under{" "}
              <button type="button" className={linkClass} onClick={() => openTab("granska")}>
                To review
              </button>{" "}
              end up here.
            </>
          )
        ) : (
          empty(
            <>
              Nothing to submit. You follow submitted clips on{" "}
              <Link href="/log" className={linkClass}>
                Stats
              </Link>
              .
            </>
          )
        ))}

      {clips && tab === "posta" && morePlatforms.length > 0 && (
        <details className="mt-8 rounded-2xl border border-line px-4 py-3">
          <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
            Submitted clips that can be posted on more platforms ({morePlatforms.length})
          </summary>
          {morePlatforms.map(row)}
        </details>
      )}

      {notice && (
        <div
          role="status"
          className="fixed inset-x-3 bottom-3 z-40 mx-auto flex max-w-xl flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-[#174a2a] bg-[#0d1a12] px-4 py-3 text-sm shadow-lg md:left-[calc(220px+1.5rem)]"
        >
          <CheckCircle2 className="size-4 shrink-0 text-good" aria-hidden />
          {/* basis-48: på smal skärm hamnar knapparna på en egen rad, i stället för att texten blir en smal spalt. */}
          <span className="min-w-0 grow basis-48 text-good">{notice.text}</span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {notice.href && (
              <Link href={notice.href} onClick={() => setNotice(null)} className="font-medium text-foreground underline underline-offset-4">
                {notice.linkText}
              </Link>
            )}
            {notice.undo && (
              <Button size="sm" variant="outline" disabled={undoing} onClick={undo}>
                Undo
              </Button>
            )}
            <Button size="icon-sm" variant="ghost" aria-label="Close" onClick={() => setNotice(null)}>
              <X aria-hidden />
            </Button>
          </span>
          {undoError && (
            <p role="alert" className="w-full text-bad">
              {undoError}
            </p>
          )}
        </div>
      )}
    </main>
  );
}
