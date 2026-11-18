"use client";

import { useCallback, useEffect, useState } from "react";
import { Radio } from "lucide-react";
import { Peaks, Problems } from "@/components/LiveWatch";
import { Button } from "@/components/ui/button";
import { channelLogin, channelStateText, freeChannels, withChannel } from "@/lib/live";
import { liveApi, type LiveWatch as Watch } from "@/lib/liveApi";
import { pollWhileVisible } from "@/lib/poll";
import { cn } from "@/lib/utils";

const POLL_MS = 5_000;

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** Läser de fria kanalerna när Hem öppnas, och var 5:e sekund medan någon bevakas eller tills den första läsningen har gått. */
function useChannels() {
  // undefined: listan har inte gått att läsa än.
  const [channels, setChannels] = useState<Watch[] | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    liveApi
      .list()
      .then((all) => {
        setChannels(freeChannels(all));
        setError(null);
      })
      .catch((err) => setError(message(err, "Kunde inte läsa bevakningen")));
  }, []);
  useEffect(load, [load]);

  const polling = channels === undefined || channels.some((w) => w.watching);
  useEffect(() => (polling ? pollWhileVisible(load, POLL_MS) : undefined), [polling, load]);

  return { channels, setChannels, error, setError };
}

/** Bevaka live utan kampanj: Robin skriver en Twitch-kanal, och appen gör 9:16-klipp när chatten exploderar. */
export function LiveChannelCard() {
  const { channels, setChannels, error, setError } = useChannels();
  const [text, setText] = useState("");
  // Kanalen som slås på eller av just nu.
  const [busy, setBusy] = useState<string | null>(null);

  function change(login: string, watching: boolean, done?: () => void) {
    setBusy(login);
    setError(null);
    liveApi
      .setChannelWatching(login, watching)
      .then((watch) => {
        setChannels((list) => withChannel(list ?? [], watch));
        done?.();
      })
      .catch((err) => setError(message(err, "Det gick inte. Försök igen.")))
      .finally(() => setBusy(null));
  }

  function start() {
    const input = channelLogin(text);
    if ("error" in input) setError(input.error);
    else change(input.login, true, () => setText(""));
  }

  return (
    <section aria-label="Live-klipp från en Twitch-kanal" className="mx-auto mt-6 max-w-[720px] rounded-2xl border border-line bg-panel p-5">
      <h2 className="font-semibold">Live-klipp</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Skriv en Twitch-kanal. När den är live gör appen 9:16-klipp när chatten exploderar, utan kampanj. Du kan välja kampanj för
        ett klipp efteråt.
      </p>
      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          start();
        }}
      >
        <input
          aria-label="Twitch-kanal"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="ishowspeed eller länk till kanalen"
          autoComplete="off"
          spellCheck={false}
          className="h-9 min-w-0 flex-1 basis-56 rounded-lg border border-line bg-transparent px-3 text-base outline-none placeholder:text-muted-foreground focus-visible:border-brand md:text-sm"
        />
        <Button type="submit" className="h-9" disabled={busy !== null}>
          <Radio aria-hidden />
          Bevaka live
        </Button>
      </form>
      <Problems warning={null} error={error} />
      {channels && channels.length > 0 && (
        <ul aria-label="Kanaler som bevakas" className="mt-4">
          {channels.map((w) => (
            <ChannelRow key={w.channel} watch={w} busy={busy === w.channel} onToggle={() => change(w.channel, !w.watching)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ChannelRow({ watch, busy, onToggle }: { watch: Watch; busy: boolean; onToggle: () => void }) {
  const live = watch.watching && watch.live;
  return (
    <li className="border-t border-line py-4 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium break-all">twitch.tv/{watch.channel}</p>
          <p className={cn("mt-0.5 flex items-center gap-1.5 text-sm", live ? "text-foreground" : "text-muted-foreground")}>
            {live && <span className="size-2 shrink-0 rounded-full bg-bad" aria-hidden />}
            {channelStateText(watch)}
          </p>
        </div>
        <Button variant={watch.watching ? "outline" : "default"} onClick={onToggle} disabled={busy}>
          <Radio aria-hidden />
          {watch.watching ? "Sluta bevaka" : "Bevaka live"}
        </Button>
      </div>
      <Problems warning={watch.error} error={null} />
      {(watch.watching || watch.peaks.length > 0) && <Peaks peaks={watch.peaks} />}
    </li>
  );
}
