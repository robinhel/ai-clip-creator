import { useCallback, useEffect, useState } from "react";
import { errorMessage, watchesOf } from "./live";
import { liveApi, type LiveWatch } from "./liveApi";
import { pollWhileVisible } from "./poll";

const POLL_MS = 5_000;

/**
 * Läser bevakningen för kampanjen, eller de fria kanalerna när profile är null. Läser när sidan öppnas, och var 5:e sekund
 * medan någon av dem bevakas eller tills den första läsningen har gått. En kanal som inte bevakas håller inte igång läsningen.
 */
export function useLiveWatches(profile: string | null) {
  // undefined: listan har inte gått att läsa än.
  const [watches, setWatches] = useState<LiveWatch[] | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    liveApi
      .list()
      .then((all) => {
        setWatches(watchesOf(all, profile));
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, "Kunde inte läsa bevakningen")));
  }, [profile]);
  useEffect(load, [load]);

  const polling = watches === undefined || watches.some((w) => w.watching);
  useEffect(() => (polling ? pollWhileVisible(load, POLL_MS) : undefined), [polling, load]);

  return { watches, setWatches, error, setError };
}
