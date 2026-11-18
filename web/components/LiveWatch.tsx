"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import { newestFirst, statusText, timeText } from "@/lib/live";
import { liveApi, type LiveWatch as Watch, type Peak, type PeakStatus } from "@/lib/liveApi";
import { pollWhileVisible } from "@/lib/poll";

const POLL_MS = 5_000;

const STATUS_TONE: Partial<Record<PeakStatus, string>> = { klar: "text-good", fel: "text-bad" };

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** Läser bevakningen när sidan öppnas, och var 5:e sekund medan den står på eller tills den första läsningen har gått. */
function useWatch(profile: string) {
  // undefined: listan har inte gått att läsa än. null: kampanjen finns inte i listan.
  const [watch, setWatch] = useState<Watch | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    liveApi
      .list()
      .then((all) => {
        setWatch(all.find((w) => w.profile === profile) ?? null);
        setError(null);
      })
      .catch((err) => setError(message(err, "Kunde inte läsa bevakningen")));
  }, [profile]);
  useEffect(load, [load]);

  const polling = watch === undefined || watch?.watching === true;
  useEffect(() => (polling ? pollWhileVisible(load, POLL_MS) : undefined), [polling, load]);

  return { watch, setWatch, error, setError };
}

/** Bevaka live: medan kanalen är live gör appen klipp när chatten exploderar. Visas bara för kampanjer med en kanal. */
export function LiveWatch({ profile }: { profile: string }) {
  const { watch, setWatch, error, setError } = useWatch(profile);
  const [busy, setBusy] = useState(false);

  if (watch === undefined) return error ? <LoadError error={error} /> : null;
  // Utan kanal finns kampanjen inte i listan, och då visas inget.
  if (watch === null) return null;
  const watching = watch.watching;

  function toggle() {
    setBusy(true);
    setError(null);
    liveApi
      .setWatching(profile, !watching)
      .then(setWatch)
      .catch((err) => setError(message(err, "Det gick inte. Försök igen.")))
      .finally(() => setBusy(false));
  }

  return (
    <section aria-label="Live-klipp" className="mt-4 max-w-[760px] rounded-2xl border border-line bg-panel p-5">
      <Header channel={watch.channel} watching={watching} busy={busy} onToggle={toggle} />
      <WatchStatus watching={watching} live={watch.live} />
      <Problems warning={watch.error} error={error} />
      {(watching || watch.peaks.length > 0) && <Peaks peaks={watch.peaks} />}
    </section>
  );
}

/** Den första läsningen gick inte. Kortet visar felet, och sidan läser om tills det går. */
function LoadError({ error }: { error: string }) {
  return (
    <section aria-label="Live-klipp" className="mt-4 max-w-[760px] rounded-2xl border border-line bg-panel p-5">
      <h2 className="font-semibold">Live-klipp</h2>
      <Problems warning={null} error={error} />
    </section>
  );
}

function Header({ channel, watching, busy, onToggle }: { channel: string; watching: boolean; busy: boolean; onToggle: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h2 className="font-semibold">Live-klipp</h2>
        <p className="text-sm text-muted-foreground">
          Gör klipp av stunden före toppen när chatten på twitch.tv/{channel} exploderar.
        </p>
      </div>
      <Button variant={watching ? "outline" : "default"} onClick={onToggle} disabled={busy}>
        <Radio aria-hidden />
        {watching ? "Sluta bevaka" : "Bevaka live"}
      </Button>
    </div>
  );
}

/** warning: bevakningens eget fel från servern. error: anropet från sidan gick inte. */
function Problems({ warning, error }: { warning: string | null; error: string | null }) {
  return (
    <>
      {warning && (
        <p role="status" className="mt-3 flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{warning}</span>
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-bad">
          {error}
        </p>
      )}
    </>
  );
}

function WatchStatus({ watching, live }: { watching: boolean; live: boolean }) {
  if (!watching) return null;
  if (!live) return <p className="mt-3 text-sm text-muted-foreground">Väntar på att kanalen går live</p>;
  return (
    <p className="mt-3">
      <span className="rounded-md bg-bad px-2 py-0.5 text-xs font-semibold text-white">Live nu</span>
    </p>
  );
}

function Peaks({ peaks }: { peaks: Peak[] }) {
  if (peaks.length === 0) return <p className="mt-4 text-sm text-muted-foreground">Inga toppar än.</p>;
  return (
    <ul aria-label="Toppar i chatten" className="mt-4 rounded-2xl border border-line px-3">
      {newestFirst(peaks).map((p) => (
        <PeakRow key={p.n} peak={p} />
      ))}
    </ul>
  );
}

function PeakRow({ peak }: { peak: Peak }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line py-3 text-sm last:border-b-0">
      <span className="text-muted-foreground tabular-nums">{timeText(peak.at)}</span>
      <span className="font-medium">Topp {peak.n}</span>
      <span className={STATUS_TONE[peak.status] ?? "text-muted-foreground"}>{statusText(peak.status)}</span>
      {peak.run_id != null && (
        <Link
          href={`/runs/${peak.run_id}`}
          className="ml-auto font-medium text-foreground underline underline-offset-4 hover:text-brand"
        >
          Öppna körningen
        </Link>
      )}
      {peak.reason && <p className="basis-full text-muted-foreground">{peak.reason}</p>}
    </li>
  );
}
