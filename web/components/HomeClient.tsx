"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Link2, Loader2, Send, X } from "lucide-react";
import { ProblemFix, readyText } from "@/components/CampaignsClient";
import { scoreTone } from "@/components/ClipResultRow";
import { ProjectCard } from "@/components/ProjectCard";
import { ScoutMiniCard } from "@/components/ScoutCard";
import { api } from "@/lib/api";
import { clock, cn, duration, parseClock, serverTime, usd } from "@/lib/utils";
import type { CampaignProfile, CampaignRunInfo, CampaignStatus, CampaignSummary, ScoutCampaign, StreamInfo, VodInfo } from "@/lib/types";

const FOCUS_MAX = 300;
const RUNS_POLL_MS = 5000;
const gb = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

/** Siffrorna ur en Twitch-länk, eller texten om den bara är siffror. */
function vodCandidate(text: string): string | null {
  const value = text.trim();
  if (/^\d+$/.test(value)) return value;
  return value.match(/twitch\.tv\/videos\/(\d+)/)?.[1] ?? null;
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

function TodoCard({ count, label, href, cta }: { count: number; label: string; href: string; cta: string }) {
  return (
    <Link
      href={href}
      className="rounded-2xl border border-line bg-panel p-4 transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand"
    >
      <p className={cn("text-3xl font-bold tabular-nums", count === 0 && "text-muted-foreground")}>{count}</p>
      <p className="text-[13px] text-muted-foreground">{label}</p>
      <p className="mt-2 text-[13px] text-brand">{cta}</p>
    </Link>
  );
}

/** Projektet med flest klipp med statusen, med filtret på projektsidan. Inga sådana klipp: alla projekt. */
function busiestHref(runs: CampaignRunInfo[] | null, status: CampaignStatus, filter: string): string {
  const count = (r: CampaignRunInfo | null) => r?.clip_counts[status] ?? 0;
  const top = runs?.reduce<CampaignRunInfo | null>((best, r) => (count(r) > count(best) ? r : best), null);
  return top ? `/runs/${top.run_id}?filter=${filter}` : "/projects";
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
        href="/queue"
        className="inline-flex h-9 items-center rounded-lg bg-brand px-3.5 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      >
        Öppna Att posta
      </Link>
    </section>
  );
}

const streamDay = new Intl.DateTimeFormat("sv-SE", { day: "numeric", month: "short" });
/** Twitch kan sakna datumet; då sorteras streamen sist bland de nyaste. */
const streamTime = (s: StreamInfo) => (s.created_at ? serverTime(s.created_at) : 0);

function streamTag(s: StreamInfo): { text: string; tone: string } | null {
  if (s.run_id == null || !s.run_status) return null;
  if (s.run_status === "failed") return { text: "Misslyckades", tone: "bg-bad font-semibold text-white" };
  if (s.run_status === "done") return { text: `Klar, ${s.clip_count} klipp`, tone: "bg-black/70" };
  return { text: "Pågår", tone: "bg-brand text-white" };
}

type StreamSort = "best" | "new";

/** Bäst att klippa: betyg fallande, streams med körning och live sist, nyast först vid lika. */
function sortStreams(list: StreamInfo[], by: StreamSort): StreamInfo[] {
  const newest = (a: StreamInfo, b: StreamInfo) => streamTime(b) - streamTime(a);
  if (by === "new") return [...list].sort(newest);
  const late = (s: StreamInfo) => (s.run_id != null || s.live ? 1 : 0);
  return [...list].sort((a, b) => late(a) - late(b) || (b.score ?? 0) - (a.score ?? 0) || newest(a, b));
}

/** En stream: klick fyller i fältet, eller öppnar projektet om VOD:en redan har en körning. */
function StreamCard({ stream: s, selected, onPick }: { stream: StreamInfo; selected: boolean; onPick: (vodId: string) => void }) {
  const tag = s.live
    ? { text: "Live", tone: "bg-bad font-semibold text-white" }
    : s.recommended
      ? { text: "Rekommenderad", tone: "bg-brand font-semibold text-white" }
      : streamTag(s);
  const body = (
    <>
      <div className="relative aspect-video bg-[#1f1f26]">
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
      <div className="flex gap-2.5 px-2.5 py-2 text-left">
        {s.score != null && (
          <p className="shrink-0 text-center" aria-label={`Betyg ${s.score} av 100`}>
            <span data-score className={cn("block text-lg leading-none font-extrabold tabular-nums", scoreTone(s.score))}>
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
    // flex-col: en knapp centrerar annars innehållet, och bilden hamnar mitt i kortet.
    "flex w-52 shrink-0 snap-start flex-col overflow-hidden rounded-xl border bg-panel transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand",
    selected || s.recommended ? "border-brand" : "border-line",
    s.recommended && "ring-2 ring-brand/30",
    s.live && "cursor-not-allowed opacity-75 hover:border-line"
  );
  if (s.live) {
    return (
      <button type="button" disabled data-vod={s.vod_id} title="Streamen pågår. Vänta tills den är slut." className={box}>
        {body}
      </button>
    );
  }
  return s.run_id != null ? (
    <Link href={`/runs/${s.run_id}`} data-vod={s.vod_id} className={box}>
      {body}
    </Link>
  ) : (
    <button type="button" data-vod={s.vod_id} data-recommended={s.recommended || undefined} onClick={() => onPick(s.vod_id)} className={box}>
      {body}
    </button>
  );
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [streams, setStreams] = useState<StreamInfo[] | null>(null);
  const [streamsError, setStreamsError] = useState(false);
  const [streamSort, setStreamSort] = useState<StreamSort>("best");
  // De tre bästa från bevakningen. Ett fel eller en äldre server döljer rutan tyst.
  const [scout, setScout] = useState<ScoutCampaign[]>([]);
  // Gammal server utan betyg: listan som servern skickar, nyast först.
  const scored = streams?.some((s) => s.score != null) ?? false;
  const shownStreams = streams && scored ? sortStreams(streams, streamSort) : streams;

  const [profileName, setProfileName] = useState("");
  const [game, setGame] = useState("");
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [focus, setFocus] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const candidate = vodCandidate(link);
  // Samma gräns som servern: 6–15 siffror.
  const vodId = candidate && /^\d{6,15}$/.test(candidate) ? candidate : null;
  const [pollTick, setPollTick] = useState(0);

  function loadStreams() {
    setStreamsError(false);
    api.campaign.streams().then(setStreams).catch(() => setStreamsError(true));
  }

  useEffect(() => {
    Promise.all([api.campaign.profiles(), api.campaign.summary(), api.campaign.runs.list()])
      .then(([p, s, r]) => {
        setProfiles(p);
        setSummary(s);
        setRuns(r);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Kunde inte hämta startsidan"));
    // Egen hämtning: Twitch kan vara långsam, och ett fel här ska inte stoppa resten av sidan.
    loadStreams();
    api.campaign.scout
      .status()
      .then((s) => setScout(s.campaigns.filter((c) => !c.excluded).sort((a, b) => b.score - a.score).slice(0, 3)))
      .catch(() => setScout([]));
  }, []);

  // Läs om projekten var femte sekund medan något projekt pågår, också efter ett misslyckat anrop.
  const anyActive = runs?.some((r) => r.status === "pending" || r.status === "running") ?? false;
  useEffect(() => {
    if (!anyActive) return;
    const timer = setTimeout(() => {
      api.campaign.runs
        .list()
        .then(setRuns)
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, RUNS_POLL_MS);
    return () => clearTimeout(timer);
  }, [anyActive, pollTick]);

  // Hämta VOD:en så fort en giltig länk står i fältet.
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
        setStart("");
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
  }, [vodId]);

  const matching = useMemo(
    () => profiles.filter((p) => vod?.campaigns.includes(p.name)),
    [profiles, vod]
  );

  // Förval: den enda kampanjen som passar kanalen.
  useEffect(() => {
    setProfileName(matching.length === 1 ? matching[0].name : "");
  }, [matching]);

  const profile = matching.find((p) => p.name === profileName) ?? null;

  useEffect(() => {
    setMinS(profile?.clip_min_s != null ? String(profile.clip_min_s) : "");
    setMaxS(profile?.clip_max_s != null ? String(profile.clip_max_s) : "");
  }, [profile]);

  // Det som stoppar körningen, i den ordning det behöver rättas.
  const startS = start.trim() ? parseClock(start) : 0;
  const endS = end.trim() ? parseClock(end) : 0;
  const min = Number(minS);
  const max = Number(maxS);
  const blockers: string[] = [];
  if (vod) {
    // Serverns hinder (live-stream, SSD:n saknas). Äldre servrar skickar bara live.
    blockers.push(...(vod.problems ?? (vod.live ? ["Streamen pågår. Vänta tills den är slut."] : [])));
    if (matching.length === 0) {
      blockers.push(`Ingen kampanj passar kanalen ${vod.channel_name}. Skapa en kampanj för kanalen först.`);
    } else if (!profile) {
      blockers.push("Välj en kampanj.");
    } else if (!profile.ready) {
      blockers.push(`Kampanjen ${profile.name} är inte redo. Rätta problemen nedan.`);
    }
    if (!vod.estimate.fits) {
      blockers.push(
        `Disken räcker inte. VOD:en behöver ${gb.format(vod.estimate.download_gb)} GB, men bara ${gb.format(
          vod.estimate.disk_free_gb
        )} GB är ledigt. Frigör plats och försök igen.`
      );
    }
    if (profile && profile.clip_min_s != null && profile.clip_max_s != null && (!Number.isInteger(min) || !Number.isInteger(max) || min < profile.clip_min_s || max > profile.clip_max_s || min >= max)) {
      blockers.push(
        `Klipplängden måste ligga inom ${profile.clip_min_s}–${profile.clip_max_s} s, och kortast måste vara mindre än längst.`
      );
    }
    if (startS === null || endS === null) {
      blockers.push("Skriv start och slut som t:mm:ss, till exempel 1:05:00.");
    } else if (endS > 0 && (endS <= startS || endS > vod.duration_s)) {
      blockers.push(`Slutet måste ligga efter starten och inom VOD:ens längd (${clock(vod.duration_s)}).`);
    } else if (startS >= vod.duration_s) {
      blockers.push(`Starten måste ligga inom VOD:ens längd (${clock(vod.duration_s)}).`);
    }
    if (focus.length > FOCUS_MAX) blockers.push(`Leta efter får vara högst ${FOCUS_MAX} tecken.`);
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
        focus: focus.trim() || undefined,
        min_s: minS ? min : undefined,
        max_s: maxS ? max : undefined,
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setStartError(err instanceof Error ? err.message : "Kunde inte starta körningen");
      setStarting(false);
    }
  }

  const counts = summary?.counts ?? {};

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <section className="mx-auto max-w-[720px] pt-7 pb-2 text-center">
        <h1 className="text-4xl font-bold tracking-tight">Gör klipp av en stream</h1>
        <p className="mt-1.5 text-muted-foreground">Klistra in en Twitch-länk. Appen gör resten.</p>

        <form
          className="mt-6"
          onSubmit={(e) => {
            e.preventDefault();
            handleStart();
          }}
        >
          <div className="flex items-center gap-3 rounded-xl border border-line bg-panel px-4.5 py-4 text-left focus-within:border-brand">
            <Link2 className="size-5 shrink-0 text-muted-foreground" aria-hidden />
            <input
              aria-label="Twitch-länk eller VOD-id"
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://www.twitch.tv/videos/…"
              className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground"
              autoFocus
            />
            {vodLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Hämtar VOD:en" />}
            {link && (
              <button
                type="button"
                onClick={() => setLink("")}
                className="inline-flex items-center gap-1 rounded-md px-1.5 text-sm text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" aria-hidden />
                Ta bort
              </button>
            )}
          </div>

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
                <img src={vod.thumbnail_url} alt="" className="aspect-video w-40 shrink-0 rounded-lg object-cover" />
              ) : (
                <div className="aspect-video w-40 shrink-0 rounded-lg bg-[#1f1f26]" />
              )}
              <div className="min-w-0 space-y-1">
                <p className="line-clamp-2 font-semibold leading-snug break-words">{vod.title}</p>
                <p className="text-[13px] text-muted-foreground">
                  {vod.channel_name}, {vod.game ?? "okänt spel"}, {duration(vod.duration_s)}
                </p>
                <p className="text-[13px] text-muted-foreground">
                  Cirka {vod.estimate.minutes} min
                  {vod.estimate.cost_usd != null ? ` och ${usd.format(vod.estimate.cost_usd)}` : ", okänd kostnad,"} att göra klart
                  {vod.estimate.cost
                    ? ` (Berget ${usd.format(vod.estimate.cost.berget_usd)}, Claude ${usd.format(vod.estimate.cost.claude_usd)})`
                    : ""}
                  .{" "}
                  {vod.downloaded
                    ? "VOD:en finns redan på disken."
                    : `Hämtar ${gb.format(vod.estimate.download_gb)} GB, ${gb.format(vod.estimate.disk_free_gb)} GB ledigt.`}
                </p>
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={!canStart}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-[10px] bg-[#fafafa] p-4 text-[17px] font-semibold text-[#111] transition-opacity focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
          >
            {starting && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {starting ? "Startar…" : existing ? "Hämta fler klipp" : "Hämta klipp"}
          </button>

          {existing && (
            <p className="mt-2 text-left text-sm text-muted-foreground">
              Streamen har redan ett projekt.{" "}
              <Link href={`/runs/${existing.run_id}`} className="font-medium text-foreground underline underline-offset-4">
                Öppna projektet
              </Link>
              . Hämta fler klipp gör nya klipp i samma projekt.
            </p>
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

              {(blockers.length > 0 || (profile?.problems.length ?? 0) > 0) && (
                <ul className="mt-3 space-y-1.5 text-left text-sm" aria-label="Varningar före start">
                  {blockers.map((text) => (
                    <li key={text} className="flex gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-[#fca5a5]">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
                      <span>
                        <span className="sr-only">Stoppar körningen: </span>
                        {text}
                      </span>
                    </li>
                  ))}
                  {/* "Går att köra" stämmer inte när något annat stoppar körningen, till exempel en live-stream. */}
                  {profile?.ready && profile.problems.length > 0 && blockers.length === 0 && (
                    <li className="px-1 pt-1 font-medium text-warn">{readyText(profile)}.</li>
                  )}
                  {profile?.problems.map((text) => (
                    <li key={text} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                      <span>
                        {text}
                        <ProblemFix id={profile.name} problem={text} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {matching.length === 0 && (
                <Link
                  href={`/campaigns/new?${new URLSearchParams({ channel: vod.channel, streamer: vod.channel_name })}`}
                  className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand px-3 text-sm font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
                >
                  Skapa kampanj för {vod.channel_name}
                </Link>
              )}
            </>
          )}

          {startError && (
            <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-left text-sm text-[#fca5a5]">
              {startError}
            </p>
          )}
        </form>
      </section>

      {streamsError ? (
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
                <StreamCard key={s.vod_id} stream={s} selected={s.vod_id === vodId} onPick={(id) => {
                  setLink(id);
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }} />
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

      {loadError && (
        <p role="alert" className="mt-8 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {loadError}
        </p>
      )}

      <section aria-label="Det här väntar på dig">
        <h2 className="mt-12 mb-3 text-lg font-semibold">Det här väntar på dig</h2>
        {(counts.postad ?? 0) > 0 && <WhopCard count={counts.postad ?? 0} />}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <TodoCard count={counts.renderad ?? 0} label="klipp att granska" href={busiestHref(runs, "renderad", "granska")} cta="Granska" />
          <TodoCard count={counts.godkand ?? 0} label="godkända klipp att posta" href="/queue" cta="Posta" />
          <TodoCard count={counts.underkand ?? 0} label="underkända klipp att rätta" href={busiestHref(runs, "underkand", "underkanda")} cta="Rätta" />
          <TodoCard count={counts.inskickad ?? 0} label="inskickade, fyll i statistik" href="/log" cta="Fyll i visningar" />
        </div>
      </section>

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

      <div className="mt-9 mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-semibold">Senaste projekten</h2>
        {runs && runs.length > 6 && (
          <Link href="/projects" className="text-sm text-muted-foreground hover:text-foreground">
            Alla projekt ({runs.length})
          </Link>
        )}
      </div>
      {runs === null && !loadError && <p className="text-sm text-muted-foreground">Hämtar projekten…</p>}
      {runs?.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga projekt än. Klistra in en Twitch-länk ovanför för att göra det första.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {runs?.slice(0, 6).map((run) => <ProjectCard key={run.run_id} run={run} />)}
      </div>
    </main>
  );
}
