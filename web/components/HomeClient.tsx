"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Link2, Loader2, X } from "lucide-react";
import { ProjectCard } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import { clock, cn, duration, parseClock, usd } from "@/lib/utils";
import type { CampaignProfile, CampaignRunInfo, CampaignSummary, VodInfo } from "@/lib/types";

const FOCUS_MAX = 300;
const gb = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

/** VOD-id ur en Twitch-länk eller bara siffrorna. */
export function vodIdFrom(text: string): string | null {
  const value = text.trim();
  if (/^\d{5,}$/.test(value)) return value;
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

  const [profileName, setProfileName] = useState("");
  const [game, setGame] = useState("");
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [focus, setFocus] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const vodId = vodIdFrom(link);

  useEffect(() => {
    Promise.all([api.campaign.profiles(), api.campaign.summary(), api.campaign.runs.list()])
      .then(([p, s, r]) => {
        setProfiles(p);
        setSummary(s);
        setRuns(r);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Kunde inte hämta startsidan"));
  }, []);

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
    setMinS(profile ? String(profile.clip_min_s) : "");
    setMaxS(profile ? String(profile.clip_max_s) : "");
  }, [profile]);

  // Det som stoppar körningen, i den ordning det behöver rättas.
  const startS = start.trim() ? parseClock(start) : 0;
  const endS = end.trim() ? parseClock(end) : 0;
  const min = Number(minS);
  const max = Number(maxS);
  const blockers: string[] = [];
  if (vod) {
    if (matching.length === 0) {
      blockers.push(`Ingen kampanj passar kanalen ${vod.channel_name}. Lägg till en kampanj för kanalen först.`);
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
    if (profile && (!Number.isInteger(min) || !Number.isInteger(max) || min < profile.clip_min_s || max > profile.clip_max_s || min >= max)) {
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
        min_s: min,
        max_s: max,
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

          {link.trim() && !vodId && (
            <p className="mt-2 text-left text-sm text-warn">
              Det ser inte ut som en Twitch-VOD. Klistra in en länk som https://www.twitch.tv/videos/2925991945.
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
                  Cirka {vod.estimate.minutes} min och {usd.format(vod.estimate.cost_usd)} att göra klart.{" "}
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
            {starting ? "Startar…" : "Hämta klipp"}
          </button>

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
                {profile && (
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
                  {profile?.problems.map((text) => (
                    <li key={text} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
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

      {loadError && (
        <p role="alert" className="mt-8 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {loadError}
        </p>
      )}

      <h2 className="mt-12 mb-3 text-lg font-semibold">Det här väntar på dig</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TodoCard count={counts.renderad ?? 0} label="klipp att granska" href="/projects" cta="Granska" />
        <TodoCard count={counts.godkand ?? 0} label="godkända klipp att posta" href="/queue" cta="Posta" />
        <TodoCard count={counts.underkand ?? 0} label="underkända klipp att rätta" href="/projects" cta="Rätta" />
        <TodoCard count={counts.inskickad ?? 0} label="inskickade, fyll i statistik" href="/log" cta="Fyll i visningar" />
      </div>

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
