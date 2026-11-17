"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, FolderOpen, Link2, Loader2, Send, Upload, X } from "lucide-react";
import { ProblemFix, readyText } from "@/components/CampaignsClient";
import { ProjectCard, UploadsCard, groupTime, groupUploads, isActive, runTime } from "@/components/ProjectCard";
import { ScoutMiniCard } from "@/components/ScoutCard";
import { api } from "@/lib/api";
import { handOverFiles } from "@/lib/handoff";
import { stepCount } from "@/lib/steps";
import { parseTwitchLink } from "@/lib/twitchLink";
import { clock, cn, duration, parseClock, serverTime, usd } from "@/lib/utils";
import type { CampaignClip, CampaignProfile, CampaignRunInfo, CampaignSummary, ScoutCampaign, StreamInfo, VodInfo } from "@/lib/types";

const FOCUS_MAX = 300;
const RUNS_POLL_MS = 5000;
const SHOWN_PROJECTS = 6;
const VIDEO_TYPES = [".mp4", ".mov", ".m4v", ".webm", ".mkv"];
const gb = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

/** Ett hinder före start. Med en länk till stället där det rättas, när det finns ett. */
type Blocker = { text: string; href?: string; linkText?: string };

/** Nycklar läggs in under Inställningar. Texten kommer från servern, till exempel "BERGET_API_KEY saknas i .env." */
const isKeyProblem = (text: string) => /API_KEY|nyckel/i.test(text);

function KeyFix({ problem }: { problem: string }) {
  if (!isKeyProblem(problem)) return null;
  return (
    <>
      {" "}
      <Link href="/settings" className="font-medium underline underline-offset-4 hover:text-foreground">
        Lägg in nyckeln i Inställningar
      </Link>
    </>
  );
}

function Chip({ label, children, warn }: { label: string; children: React.ReactNode; warn?: boolean }) {
  return (
    <label
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-line bg-panel py-1 pr-1.5 pl-3 text-[13px] text-muted-foreground focus-within:border-brand",
        warn && "border-[#5a4416]"
      )}
    >
      {label}
      {children}
    </label>
  );
}

// Fält som ser ut som text i chipet tills man klickar.
const chipField =
  "rounded-full bg-transparent px-1.5 py-0.5 font-medium text-foreground outline-none hover:bg-white/5 focus:bg-white/5";

function TodoCard({
  count,
  label,
  note,
  href,
  cta,
}: {
  count: number;
  label: string;
  /** En del av siffran, till exempel underkända klipp i Att granska. */
  note?: string;
  href: string;
  cta: string;
}) {
  return (
    <Link
      href={href}
      className="rounded-2xl border border-line bg-panel p-4 transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand"
    >
      <p className={cn("text-3xl font-bold tabular-nums", count === 0 && "text-muted-foreground")}>{count}</p>
      <p className="text-[13px] text-muted-foreground">{label}</p>
      {note && <p className="text-[13px] text-warn">{note}</p>}
      <p className="mt-2 text-[13px] text-brand">{cta}</p>
    </Link>
  );
}

function WhopCard({ count }: { count: number }) {
  return (
    <section
      aria-label="Skicka in på Whop"
      className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-3 rounded-2xl border border-brand/60 bg-brand/10 px-4 py-3.5"
    >
      <Send className="size-5 shrink-0 text-brand" aria-hidden />
      <div className="min-w-0 flex-1">
        <h3 className="font-semibold">Skicka in på Whop</h3>
        <p className="text-[13px] text-muted-foreground">
          {count === 1 ? "1 postat klipp är inte inskickat än." : `${count} postade klipp är inte inskickade än.`} Skicka
          in länkarna för att få betalt.
        </p>
      </div>
      <Link
        href="/queue?tab=skicka-in"
        className="inline-flex h-9 items-center rounded-lg bg-brand px-3.5 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      >
        Öppna Skicka in
      </Link>
    </section>
  );
}

const streamDay = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });
/** Twitch kan sakna datumet; då sorteras streamen sist bland de nyaste. */
const streamTime = (s: StreamInfo) => (s.created_at ? serverTime(s.created_at) : 0);

function streamTag(s: StreamInfo): { text: string; tone: string } | null {
  if (s.run_id == null || !s.run_status) return null;
  if (s.run_cancelled) return { text: "Avbruten", tone: "bg-black/70" };
  if (s.run_status === "failed") return { text: "Misslyckades", tone: "bg-bad font-semibold text-white" };
  if (s.run_status === "done") return { text: `Klar, ${s.clip_count} klipp`, tone: "bg-black/70" };
  return { text: "Pågår", tone: "bg-brand text-white" };
}

type StreamSort = "best" | "new";

/** Bäst att klippa: betyg fallande, streams med projekt och live sist, nyast först vid lika. */
function sortStreams(list: StreamInfo[], by: StreamSort): StreamInfo[] {
  const newest = (a: StreamInfo, b: StreamInfo) => streamTime(b) - streamTime(a);
  if (by === "new") return [...list].sort(newest);
  const late = (s: StreamInfo) => (s.run_id != null || s.live ? 1 : 0);
  return [...list].sort((a, b) => late(a) - late(b) || (b.score ?? 0) - (a.score ?? 0) || newest(a, b));
}

/**
 * En stream. Klick på kortet fyller i fältet, eller öppnar projektet om streamen redan har ett.
 * Hämta klipp startar direkt när det går, annars fyller den i fältet så att hindren syns under knappen.
 */
function StreamCard({
  stream: s,
  selected,
  onPick,
  onFetch,
  busy,
  error,
}: {
  stream: StreamInfo;
  selected: boolean;
  onPick: (vodId: string) => void;
  onFetch: (stream: StreamInfo) => void;
  busy: boolean;
  error?: string;
}) {
  const tag = s.live
    ? { text: "Live", tone: "bg-bad font-semibold text-white" }
    : s.recommended
      ? { text: "Rekommenderad", tone: "bg-brand font-semibold text-white" }
      : streamTag(s);
  const body = (
    <>
      <div className="relative aspect-video w-full bg-[#1f1f26]">
        {s.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- Twitch-bild.
          <img src={s.thumbnail_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="grid h-full place-content-center gap-0.5 text-center text-xs text-muted-foreground">
            <span>Ingen bild än</span>
            <span className="text-[11px] tabular-nums">VOD {s.vod_id}</span>
          </div>
        )}
        {tag && <span className={cn("absolute top-1.5 left-1.5 rounded-md px-1.5 py-0.5 text-[11px]", tag.tone)}>{tag.text}</span>}
        <span className="absolute right-1.5 bottom-1.5 rounded-md bg-black/70 px-1.5 text-[11px] tabular-nums">{clock(s.duration_s)}</span>
      </div>
      <div className="flex w-full gap-2.5 px-2.5 pt-2 text-left">
        {s.score != null && (
          // Neutral färg: betyget säger hur bra streamen är att klippa, inte att något är fel.
          <p className="shrink-0 text-center" aria-label={`Betyg ${s.score} av 100`}>
            <span data-score className="block text-lg leading-none font-extrabold text-foreground tabular-nums">
              {s.score}
            </span>
            <span className="text-[10px] text-muted-foreground">betyg</span>
          </p>
        )}
        <div className="min-w-0 space-y-0.5">
          <p className="line-clamp-2 text-[13px] leading-snug font-medium break-words">{s.title}</p>
          <p className="truncate text-xs text-muted-foreground">
            {[s.game, s.created_at && streamDay.format(streamTime(s))].filter(Boolean).join(", ")}
          </p>
          {s.reason && <p className="line-clamp-2 text-xs text-muted-foreground">{s.reason}</p>}
          {s.live && <p className="text-xs text-warn">Streamen pågår. Vänta tills den är slut.</p>}
        </div>
      </div>
    </>
  );
  const box = cn(
    "flex w-52 shrink-0 snap-start flex-col overflow-hidden rounded-xl border bg-panel transition-colors hover:border-[#3a3a44]",
    selected || s.recommended ? "border-brand" : "border-line",
    s.recommended && "ring-2 ring-brand/30",
    s.live && "opacity-75 hover:border-line"
  );
  const area = "flex w-full flex-col focus-visible:outline-2 focus-visible:outline-brand";
  const action =
    "inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-lg text-[13px] font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-50";
  return (
    <div className={box} data-vod={s.vod_id} data-recommended={s.recommended || undefined}>
      {s.run_id != null ? (
        <Link href={`/runs/${s.run_id}`} className={area}>
          {body}
        </Link>
      ) : (
        <button type="button" disabled={s.live} onClick={() => onPick(s.vod_id)} className={cn(area, s.live && "cursor-not-allowed")}>
          {body}
        </button>
      )}
      <div className="mt-auto space-y-1.5 px-2.5 pt-2 pb-2.5">
        {s.run_id != null ? (
          <Link href={`/runs/${s.run_id}`} className={cn(action, "border border-line hover:bg-white/5")}>
            Öppna projektet
          </Link>
        ) : (
          <button
            type="button"
            disabled={s.live || busy}
            title={s.live ? "Streamen pågår. Vänta tills den är slut." : undefined}
            onClick={() => onFetch(s)}
            className={cn(action, "bg-[#fafafa] text-[#111] hover:bg-white")}
          >
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
            {busy ? "Startar…" : "Hämta klipp"}
          </button>
        )}
        {error && (
          <p role="alert" className="text-xs break-words text-[#fca5a5]">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

/** Videofilerna bland de indragna. Andra filer tas inte med. */
function videoFiles(list: FileList | null): File[] {
  return Array.from(list ?? []).filter((f) => VIDEO_TYPES.some((type) => f.name.toLowerCase().endsWith(type)));
}

export function HomeClient() {
  const router = useRouter();
  const [link, setLink] = useState("");
  const [vod, setVod] = useState<VodInfo | null>(null);
  const [vodError, setVodError] = useState<string | null>(null);
  const [vodLoading, setVodLoading] = useState(false);
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [summary, setSummary] = useState<CampaignSummary | null>(null);
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [uploads, setUploads] = useState<CampaignClip[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [streams, setStreams] = useState<StreamInfo[] | null>(null);
  const [streamsError, setStreamsError] = useState(false);
  const [streamSort, setStreamSort] = useState<StreamSort>("best");
  // De tre bästa från bevakningen. Ett fel eller en äldre server döljer rutan tyst.
  const [scout, setScout] = useState<ScoutCampaign[]>([]);
  // Gammal server utan betyg: listan som servern skickar, nyast först.
  const scored = streams?.some((s) => s.score != null) ?? false;
  const shownStreams = streams && scored ? sortStreams(streams, streamSort) : streams;
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  // Hämta klipp på ett streamkort: vilken stream som startar, och felet per stream.
  const [quickBusy, setQuickBusy] = useState<string | null>(null);
  const [quickErrors, setQuickErrors] = useState<Record<string, string>>({});

  const [profileName, setProfileName] = useState("");
  const [game, setGame] = useState("");
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [focus, setFocus] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const parsed = parseTwitchLink(link);
  const candidate = parsed.vodId;
  // Samma gräns som servern: 6–15 siffror.
  const vodId = candidate && /^\d{6,15}$/.test(candidate) ? candidate : null;
  const [pollTick, setPollTick] = useState(0);

  function loadStreams() {
    setStreamsError(false);
    api.campaign.streams().then(setStreams).catch(() => setStreamsError(true));
  }

  const load = useCallback(() => {
    setLoadError(null);
    Promise.all([api.campaign.profiles(), api.campaign.summary(), api.campaign.runs.list()])
      .then(([p, s, r]) => {
        setProfiles(p);
        setSummary(s);
        setRuns(r);
      })
      .catch((err) => {
        // Siffrorna visas inte när servern inte svarar: 0 skulle se ut som att inget väntar.
        setSummary(null);
        setLoadError(err instanceof Error ? err.message : "Kunde inte hämta startsidan");
      });
    // Egna videor visas som projekt. Ett fel här döljer bara korten för dem.
    api.campaign.clips.list({ vod_id: "upload" }).then(setUploads).catch(() => setUploads([]));
    // Egen hämtning: Twitch kan vara långsam, och ett fel här ska inte stoppa resten av sidan.
    loadStreams();
    api.campaign.scout
      .status()
      .then((s) => setScout(s.campaigns.slice(0, 3))) // servern sorterar och skickar inte bortsorterade
      .catch(() => setScout([]));
  }, []);

  useEffect(() => {
    load();
    // ?vod=<VOD-id> väljer VOD:en, till exempel från en kampanjsida.
    const wanted = new URLSearchParams(window.location.search).get("vod");
    if (wanted) setLink(wanted);
  }, [load]);

  // Läs om projekten och siffrorna var femte sekund medan något projekt pågår, också efter ett misslyckat anrop.
  const anyActive = runs?.some(isActive) ?? false;
  useEffect(() => {
    if (!anyActive) return;
    const timer = setTimeout(() => {
      Promise.all([api.campaign.runs.list().then(setRuns), api.campaign.summary().then(setSummary)])
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, RUNS_POLL_MS);
    return () => clearTimeout(timer);
  }, [anyActive, pollTick]);

  // Hämta VOD:en så fort en giltig länk står i fältet. ?t= i länken blir starten.
  const linkStart = parsed.startS;
  useEffect(() => {
    if (!vodId) {
      setVod(null);
      setVodError(null);
      return;
    }
    let cancelled = false;
    setVodLoading(true);
    setVodError(null);
    setStartError(null);
    api.campaign
      .vod(vodId)
      .then((info) => {
        if (cancelled) return;
        setVod(info);
        setGame(info.game ?? "");
        setStart(linkStart ? clock(linkStart) : "");
        setEnd("");
      })
      .catch((err) => {
        if (cancelled) return;
        setVod(null);
        setVodError(err instanceof Error ? err.message : "Kunde inte hämta VOD:en");
      })
      .finally(() => !cancelled && setVodLoading(false));
    return () => {
      cancelled = true;
    };
  }, [vodId, linkStart]);

  const matching = useMemo(
    () => profiles.filter((p) => vod?.campaigns.includes(p.name)),
    [profiles, vod]
  );

  // Förval: den enda kampanjen som passar kanalen.
  useEffect(() => {
    setProfileName(matching.length === 1 ? matching[0].name : "");
  }, [matching]);

  const profile = matching.find((p) => p.name === profileName) ?? null;
  // Snabbläget: signalerna väljer, utan Claude. Då finns inget Leta efter och ingen Claude-kostnad.
  const fast = !!profile && !!vod?.fast_campaigns?.includes(profile.name);

  useEffect(() => {
    setMinS(profile?.clip_min_s != null ? String(profile.clip_min_s) : "");
    setMaxS(profile?.clip_max_s != null ? String(profile.clip_max_s) : "");
  }, [profile]);

  // Servern kör ett projekt i taget. Det som pågår stoppar en ny start.
  const running = runs?.find(isActive);

  // Det som stoppar körningen, i den ordning det behöver rättas.
  const startS = start.trim() ? parseClock(start) : 0;
  const endS = end.trim() ? parseClock(end) : 0;
  const min = Number(minS);
  const max = Number(maxS);
  const blockers: Blocker[] = [];
  if (vod) {
    // Serverns hinder (live-stream, SSD:n saknas). Äldre servrar skickar bara live.
    blockers.push(...(vod.problems ?? (vod.live ? ["Streamen pågår. Vänta tills den är slut."] : [])).map((text) => ({ text })));
    if (running) {
      blockers.push({
        text:
          running.vod_id === vod.vod_id
            ? "Streamen hämtar redan klipp. Vänta tills det är klart."
            : `Ett annat projekt hämtar klipp just nu: ${running.title || running.vod_id}. Appen gör ett i taget.`,
        href: `/runs/${running.run_id}`,
        linkText: "Öppna projektet",
      });
    }
    if (matching.length === 0) {
      blockers.push({ text: `Ingen kampanj passar kanalen ${vod.channel_name}. Skapa en kampanj för kanalen först.` });
    } else if (!profile) {
      blockers.push({ text: "Välj en kampanj." });
    } else if (!profile.ready) {
      blockers.push({ text: `Kampanjen ${profile.streamer || profile.name} är inte redo. Rätta det som står nedan.` });
    }
    if (!vod.estimate.fits) {
      blockers.push({
        text: `Disken räcker inte. VOD:en behöver ${gb.format(vod.estimate.download_gb)} GB, men bara ${gb.format(
          vod.estimate.disk_free_gb
        )} GB är ledigt. Frigör plats och försök igen.`,
      });
    }
    if (profile && profile.clip_min_s != null && profile.clip_max_s != null && (!Number.isInteger(min) || !Number.isInteger(max) || min < profile.clip_min_s || max > profile.clip_max_s || min >= max)) {
      blockers.push({
        text: `Klipplängden måste ligga inom ${profile.clip_min_s}–${profile.clip_max_s} s, och kortast måste vara mindre än längst.`,
      });
    }
    if (startS === null || endS === null) {
      blockers.push({ text: "Skriv start och slut som t:mm:ss, till exempel 1:05:00." });
    } else if (endS > 0 && (endS <= startS || endS > vod.duration_s)) {
      blockers.push({ text: `Slutet måste ligga efter starten och inom VOD:ens längd (${clock(vod.duration_s)}).` });
    } else if (startS >= vod.duration_s) {
      blockers.push({ text: `Starten måste ligga inom VOD:ens längd (${clock(vod.duration_s)}).` });
    }
    if (!fast && focus.length > FOCUS_MAX) blockers.push({ text: `Leta efter får vara högst ${FOCUS_MAX} tecken.` });
  }
  const canStart = !!vod && !!profile && blockers.length === 0 && !starting;
  // Samma stream och kampanj är samma projekt. En ny körning hamnar i det projektet.
  const existing = vod && profile ? runs?.find((r) => r.vod_id === vod.vod_id && r.profile === profile.name) : undefined;

  async function handleStart() {
    if (!vod || !profile || !canStart) return;
    setStarting(true);
    setStartError(null);
    try {
      const run = await api.campaign.runs.create({
        profile: profile.name,
        vod_id: vod.vod_id,
        game: game.trim() || undefined,
        start_s: startS ?? 0,
        end_s: endS ?? 0,
        focus: fast ? undefined : focus.trim() || undefined,
        min_s: minS ? min : undefined,
        max_s: maxS ? max : undefined,
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setStartError(err instanceof Error ? err.message : "Kunde inte starta");
      setStarting(false);
    }
  }

  function pick(id: string) {
    setLink(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /** Hämta klipp på kortet: startar direkt med kampanjens val när en enda kampanj passar och är redo. */
  async function quickFetch(s: StreamInfo) {
    const fits = profiles.filter((p) => p.active && s.campaigns.includes(p.name));
    if (fits.length !== 1 || !fits[0].ready || running) {
      pick(s.vod_id); // valet eller hindret syns under knappen överst
      return;
    }
    setQuickBusy(s.vod_id);
    setQuickErrors((all) => ({ ...all, [s.vod_id]: "" }));
    try {
      const run = await api.campaign.runs.create({ profile: fits[0].name, vod_id: s.vod_id, game: s.game ?? undefined });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setQuickErrors((all) => ({ ...all, [s.vod_id]: err instanceof Error ? err.message : "Kunde inte starta" }));
      setQuickBusy(null);
    }
  }

  /** En egen video: Egna videor tar över filerna, med valen för kampanjen. */
  function takeFiles(list: FileList | null) {
    const files = videoFiles(list);
    if (files.length === 0) {
      setFileError(`Det är ingen video. Välj ${VIDEO_TYPES.join(", ")}.`);
      return;
    }
    handOverFiles(files);
    router.push(profileName ? `/uploads?kampanj=${encodeURIComponent(profileName)}` : "/uploads");
  }

  const counts = summary?.counts ?? {};
  const underkand = counts.underkand ?? 0;
  const projects = useMemo(() => {
    const groups = groupUploads(uploads, profiles);
    return [
      ...(runs ?? []).map((run) => ({ key: `run-${run.run_id}`, time: runTime(run), card: <ProjectCard run={run} /> })),
      ...groups.map((g) => ({ key: `upload-${g.profile}`, time: groupTime(g), card: <UploadsCard group={g} /> })),
    ].sort((a, b) => b.time - a.time);
  }, [runs, uploads, profiles]);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <section className="mx-auto max-w-[720px] pt-7 pb-2 text-center">
        <h1 className="text-4xl font-bold tracking-tight">Gör klipp</h1>
        <p className="mt-1.5 text-muted-foreground">Klistra in en Twitch-länk, eller dra in en egen video. Appen gör resten.</p>

        <form
          className="mt-6"
          onSubmit={(e) => {
            e.preventDefault();
            handleStart();
          }}
        >
          <div
            onDragOver={(e) => {
              if (!e.dataTransfer.types.includes("Files")) return;
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              if (!e.dataTransfer.files.length) return;
              e.preventDefault();
              setDragging(false);
              takeFiles(e.dataTransfer.files);
            }}
            className={cn(
              "flex flex-wrap items-center gap-3 rounded-xl border bg-panel px-4.5 py-4 text-left focus-within:border-brand",
              dragging ? "border-2 border-dashed border-brand bg-brand/10" : "border-line"
            )}
          >
            <Link2 className="size-5 shrink-0 text-muted-foreground" aria-hidden />
            <input
              aria-label="Twitch-länk eller VOD-id"
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder={dragging ? "Släpp videon här" : "https://www.twitch.tv/videos/…"}
              className="min-w-0 flex-1 basis-40 bg-transparent text-base outline-none placeholder:text-muted-foreground"
              autoFocus
            />
            {vodLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Hämtar VOD:en" />}
            {link ? (
              <button
                type="button"
                onClick={() => setLink("")}
                className="inline-flex items-center gap-1 rounded-md px-1.5 text-sm text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" aria-hidden />
                Ta bort
              </button>
            ) : (
              <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-sm text-muted-foreground hover:text-foreground focus-within:outline-2 focus-within:outline-brand">
                <Upload className="size-3.5" aria-hidden />
                Egen video
                <input
                  type="file"
                  accept={VIDEO_TYPES.join(",")}
                  multiple
                  className="sr-only"
                  onChange={(e) => {
                    takeFiles(e.target.files);
                    e.target.value = "";
                  }}
                />
              </label>
            )}
          </div>

          {fileError && (
            <p role="alert" className="mt-2 text-left text-sm text-bad">
              {fileError}
            </p>
          )}
          {link.trim() && !candidate && (
            <p className="mt-2 text-left text-sm text-warn">
              Det ser inte ut som en Twitch-VOD. Klistra in en länk som https://www.twitch.tv/videos/2925991945.
            </p>
          )}
          {candidate && !vodId && (
            <p role="alert" className="mt-2 text-left text-sm text-bad">
              VOD-id:t {candidate} har {candidate.length} siffror. Ett VOD-id har 6–15 siffror, till exempel 2925991945.
            </p>
          )}
          {vodError && (
            <p role="alert" className="mt-2 text-left text-sm text-bad">
              {vodError}
            </p>
          )}

          {vod && (
            <div className="mt-3 flex gap-4 rounded-xl border border-line bg-panel p-3 text-left">
              {vod.thumbnail_url ? (
                // eslint-disable-next-line @next/next/no-img-element -- Twitch-bild.
                <img src={vod.thumbnail_url} alt="" className="aspect-video w-28 shrink-0 rounded-lg object-cover sm:w-40" />
              ) : (
                <div className="aspect-video w-28 shrink-0 rounded-lg bg-[#1f1f26] sm:w-40" />
              )}
              <div className="min-w-0 space-y-1">
                <p className="line-clamp-2 font-semibold leading-snug break-words">{vod.title}</p>
                <p className="text-[13px] text-muted-foreground">
                  {vod.channel_name}, {vod.game ?? "okänt spel"}, {duration(vod.duration_s)}
                </p>
                <p className="text-[13px] text-muted-foreground">
                  Cirka {vod.estimate.minutes} min
                  {fast && vod.estimate.cost
                    ? ` och ${usd.format(vod.estimate.cost.berget_usd)} att göra klart (bara Berget: signalerna väljer, utan Claude)`
                    : `${vod.estimate.cost_usd != null ? ` och ${usd.format(vod.estimate.cost_usd)}` : ", okänd kostnad,"} att göra klart${
                        vod.estimate.cost
                          ? ` (Berget ${usd.format(vod.estimate.cost.berget_usd)}, Claude ${usd.format(vod.estimate.cost.claude_usd)})`
                          : ""
                      }`}
                  .{" "}
                  {vod.downloaded
                    ? "VOD:en finns redan på disken."
                    : `Hämtar ${gb.format(vod.estimate.download_gb)} GB, ${gb.format(vod.estimate.disk_free_gb)} GB ledigt.`}
                </p>
              </div>
            </div>
          )}

          {vod && (
            <>
              <div className="mt-3.5 flex flex-wrap justify-center gap-2">
                <Chip label="Kampanj:" warn={matching.length === 0}>
                  {matching.length === 0 ? (
                    <span className="px-1.5 font-medium text-warn">ingen passar</span>
                  ) : (
                    <select
                      value={profileName}
                      onChange={(e) => setProfileName(e.target.value)}
                      className={cn(chipField, "cursor-pointer [&>option]:bg-panel")}
                    >
                      {matching.length > 1 && (
                        <option value="" disabled>
                          Välj…
                        </option>
                      )}
                      {matching.map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.streamer}
                        </option>
                      ))}
                    </select>
                  )}
                </Chip>
                <Chip label="Spel:">
                  <input
                    value={game}
                    onChange={(e) => setGame(e.target.value)}
                    placeholder="okänt"
                    className={cn(chipField, "w-28 placeholder:text-muted-foreground")}
                  />
                </Chip>
                {profile?.clip_min_s != null && profile.clip_max_s != null && (
                  <Chip label="Längd:">
                    <input
                      aria-label="Kortaste klipp i sekunder"
                      inputMode="numeric"
                      value={minS}
                      onChange={(e) => setMinS(e.target.value)}
                      className={cn(chipField, "w-9 text-right tabular-nums")}
                    />
                    –
                    <input
                      aria-label="Längsta klipp i sekunder"
                      inputMode="numeric"
                      value={maxS}
                      onChange={(e) => setMaxS(e.target.value)}
                      className={cn(chipField, "w-9 tabular-nums")}
                    />
                    <span className="pr-1.5">s</span>
                  </Chip>
                )}
                <Chip label="Del av VOD:en:">
                  <input
                    aria-label="Start i VOD:en (t:mm:ss)"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                    placeholder="0:00:00"
                    className={cn(chipField, "w-[4.5rem] text-right tabular-nums placeholder:text-muted-foreground")}
                  />
                  –
                  <input
                    aria-label="Slut i VOD:en (t:mm:ss)"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                    placeholder={clock(vod.duration_s)}
                    className={cn(chipField, "w-[4.5rem] tabular-nums placeholder:text-muted-foreground")}
                  />
                </Chip>
              </div>

              {fast ? (
                <p className="mt-3 text-left text-[13px] text-muted-foreground">
                  Kampanjen väljer klippen med signalerna (chatt, ljud och tittarklipp), utan Claude.
                </p>
              ) : (
                <label className="mt-3 block text-left">
                  <span className="flex justify-between text-[13px] text-muted-foreground">
                    Leta efter
                    <span className={cn("tabular-nums", focus.length > FOCUS_MAX && "text-bad")}>
                      {focus.length} / {FOCUS_MAX}
                    </span>
                  </span>
                  <textarea
                    value={focus}
                    onChange={(e) => setFocus(e.target.value)}
                    rows={2}
                    placeholder="Till exempel: clutch, rage, roliga dödsfall. Tomt betyder kampanjens vanliga format."
                    className="mt-1 w-full resize-y rounded-xl border border-line bg-panel px-3.5 py-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-brand"
                  />
                </label>
              )}
            </>
          )}

          <button
            type="submit"
            disabled={!canStart}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-[10px] bg-[#fafafa] p-4 text-[17px] font-semibold text-[#111] transition-opacity focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
          >
            {starting && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {starting ? "Startar…" : existing ? "Hämta fler klipp" : "Hämta klipp"}
          </button>

          {/* Hindren står direkt under knappen, så att Robin ser varför den är grå. */}
          {vod && (blockers.length > 0 || (profile?.problems.length ?? 0) > 0) && (
            <ul className="mt-3 space-y-1.5 text-left text-sm" aria-label="Det här stoppar starten">
              {blockers.map((b) => (
                <li key={b.text} className="flex gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-[#fca5a5]">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
                  <span>
                    <span className="sr-only">Stoppar starten: </span>
                    {b.text}
                    {b.href && (
                      <>
                        {" "}
                        <Link href={b.href} className="font-medium underline underline-offset-4 hover:text-foreground">
                          {b.linkText}
                        </Link>
                      </>
                    )}
                  </span>
                </li>
              ))}
              {/* "Går att köra" stämmer inte när något annat stoppar starten, till exempel en live-stream. */}
              {profile?.ready && profile.problems.length > 0 && blockers.length === 0 && (
                <li className="px-1 pt-1 font-medium text-warn">{readyText(profile)}.</li>
              )}
              {profile?.problems.map((text) => (
                <li key={text} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <span>
                    {text}
                    <KeyFix problem={text} />
                    <ProblemFix id={profile.name} problem={text} />
                  </span>
                </li>
              ))}
            </ul>
          )}
          {vod && matching.length === 0 && (
            <Link
              href={`/campaigns/new?${new URLSearchParams({ channel: vod.channel, streamer: vod.channel_name })}`}
              className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand px-3 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            >
              Skapa kampanj för {vod.channel_name}
            </Link>
          )}

          {startError && (
            <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-left text-sm text-[#fca5a5]">
              {startError}
              <KeyFix problem={startError} />
            </p>
          )}

          {existing && (
            <p className="mt-2 text-left text-sm text-muted-foreground">
              Streamen har redan ett projekt.{" "}
              <Link href={`/runs/${existing.run_id}`} className="font-medium text-foreground underline underline-offset-4">
                Öppna projektet
              </Link>
              . Hämta fler klipp gör nya klipp i samma projekt.
            </p>
          )}
        </form>

        <nav aria-label="Projekt och egna videor" className="mt-5 flex flex-wrap justify-center gap-2 text-sm">
          <Link
            href="/projects"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
          >
            <FolderOpen className="size-4" aria-hidden />
            Alla projekt{runs ? ` (${projects.length})` : ""}
          </Link>
          <Link
            href="/uploads"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
          >
            <Upload className="size-4" aria-hidden />
            Egna videor
          </Link>
        </nav>
      </section>

      {loadError && (
        <div role="alert" className="mt-8 flex flex-wrap items-center gap-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          <span className="flex-1">{loadError}</span>
          <button
            type="button"
            onClick={load}
            className="inline-flex h-8 items-center rounded-lg border border-[#4d1d1d] px-3 font-medium hover:bg-white/5"
          >
            Försök igen
          </button>
        </div>
      )}

      {/* Svarar inte servern är det samma fel: då visas bara rutan ovanför. */}
      {streamsError && !loadError ? (
        <p className="mx-auto mt-6 max-w-[720px] text-center text-xs text-muted-foreground">
          Kunde inte hämta de senaste streamsen från Twitch.{" "}
          <button type="button" onClick={loadStreams} className="font-medium text-foreground underline underline-offset-4">
            Försök igen
          </button>
        </p>
      ) : (
        streams &&
        streams.length > 0 && (
          <section className="mt-9" aria-label="Senaste streams">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">Senaste streams</h2>
              {scored && (
                <div className="flex overflow-hidden rounded-[9px] border border-line text-[13px]">
                  {(
                    [
                      ["best", "Bäst att klippa"],
                      ["new", "Nyast"],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={streamSort === id}
                      onClick={() => setStreamSort(id)}
                      className="px-3 py-1.5 text-muted-foreground hover:text-foreground aria-pressed:bg-panel aria-pressed:text-foreground"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pt-1 pb-2">
              {shownStreams?.map((s) => (
                <StreamCard
                  key={s.vod_id}
                  stream={s}
                  selected={s.vod_id === vodId}
                  onPick={pick}
                  onFetch={quickFetch}
                  busy={quickBusy === s.vod_id}
                  error={quickErrors[s.vod_id]}
                />
              ))}
            </div>
            {streams.every((s) => s.viewer_clips === null) && (
              <p className="mt-1 text-xs text-muted-foreground">
                Tittarklipp räknas inte än. Lägg in Twitch-nycklarna i{" "}
                <Link href="/settings" className="underline underline-offset-4 hover:text-foreground">
                  Inställningar
                </Link>
                .
              </p>
            )}
          </section>
        )
      )}

      {summary && (
        <section aria-label="Det här väntar på dig">
          <h2 className="mt-12 mb-3 text-lg font-semibold">Det här väntar på dig</h2>
          {(counts.postad ?? 0) > 0 && <WhopCard count={counts.postad ?? 0} />}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            {/* Samma siffra som menyn och fliken Att granska: underkända klipp ingår, de ska rättas eller nekas. */}
            <TodoCard
              count={stepCount(counts, "granska")}
              label="klipp att granska"
              note={underkand > 0 ? `varav ${underkand} ${underkand === 1 ? "underkänt" : "underkända"} att rätta` : undefined}
              href="/queue?tab=granska"
              cta="Granska"
            />
            <TodoCard count={counts.godkand ?? 0} label="godkända klipp att posta" href="/queue?tab=posta" cta="Posta" />
            <TodoCard count={counts.inskickad ?? 0} label="inskickade, fyll i statistik" href="/log?status=inskickad" cta="Fyll i visningar" />
          </div>
        </section>
      )}

      {scout.length > 0 && (
        <section aria-label="Bästa kampanjerna just nu">
          <div className="mt-9 mb-3 flex items-baseline justify-between gap-4">
            <h2 className="text-lg font-semibold">Bästa kampanjerna just nu</h2>
            <Link href="/scout" className="text-sm text-muted-foreground hover:text-foreground">
              Alla kampanjer i bevakningen
            </Link>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {scout.map((c) => (
              <ScoutMiniCard key={c.ref} campaign={c} />
            ))}
          </div>
        </section>
      )}

      {/* Servern svarade inte från början: bara felet ovanför, ingen tom rubrik. */}
      {(runs !== null || !loadError) && (
        <div className="mt-9 mb-3 flex items-baseline justify-between gap-4">
          <h2 className="text-lg font-semibold">Senaste projekten</h2>
          <Link href="/projects" className="text-sm text-muted-foreground hover:text-foreground">
            Alla projekt{runs ? ` (${projects.length})` : ""}
          </Link>
        </div>
      )}
      {runs === null && !loadError && <p className="text-sm text-muted-foreground">Hämtar projekten…</p>}
      {runs?.length === 0 && projects.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga projekt än. Klistra in en Twitch-länk eller dra in en egen video ovanför för att göra det första.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {projects.slice(0, SHOWN_PROJECTS).map((p) => (
          <Fragment key={p.key}>{p.card}</Fragment>
        ))}
      </div>
    </main>
  );
}
