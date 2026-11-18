"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

type CountryChoice = { code: string; name: string };
type ScoutCountry = { country: string; choices: CountryChoice[] };

/** Samma feltexter som apiFetch i lib/api.ts, i kort form. */
function errorText(status: number, detail: unknown): string {
  if (status === 503 && detail === "Backend unavailable") return "Appens server är inte igång. Starta den och tryck Försök igen.";
  if (typeof detail === "string") return detail;
  if (status === 422) return "Servern godtog inte landet. Välj ett land i listan.";
  return `Servern svarade med fel ${status}. Försök igen.`;
}

// Anropen går via proxyn som apiFetch i lib/api.ts. Flytta dem till api.campaign.scout i api.ts senare.
async function countryFetch(init?: RequestInit): Promise<ScoutCountry> {
  const res = await fetch("/api/campaign/scout/country", { ...init, headers: { "Content-Type": "application/json" } });
  if (!res.ok) {
    const detail: unknown = await res.json().then((body) => body?.detail, () => undefined);
    throw new Error(errorText(res.status, detail));
  }
  return res.json();
}

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function CountrySelect({ data, busy, onChoose }: { data: ScoutCountry | null; busy: boolean; onChoose: (code: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      {busy && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Sparar" />}
      <select
        aria-labelledby="scout-country"
        value={data?.country ?? ""}
        onChange={(e) => onChoose(e.target.value)}
        disabled={!data || busy}
        className="h-9 rounded-lg border border-line bg-panel px-2.5 text-sm outline-none focus:border-brand disabled:opacity-60"
      >
        {data ? (
          data.choices.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))
        ) : (
          <option value="">Läser…</option>
        )}
      </select>
    </div>
  );
}

/** Landet som betyget i Bevakning räknar med. Sparas direkt; vid fel står valet kvar som innan. */
export function ScoutCountryCard() {
  const [data, setData] = useState<ScoutCountry | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    countryFetch()
      .then(setData)
      .catch((err) => setError(message(err, "Kunde inte läsa landet")));
  }, []);

  async function choose(country: string) {
    setBusy(true);
    setError(null);
    try {
      setData(await countryFetch({ method: "PUT", body: JSON.stringify({ country }) }));
    } catch (err) {
      setError(message(err, "Kunde inte spara landet"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Ditt land för kampanjer" className="mt-4 rounded-2xl border border-line bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 id="scout-country" className="text-lg font-semibold">
            Ditt land för kampanjer
          </h2>
          <p className="mt-0.5 max-w-[640px] text-sm text-muted-foreground">
            Betyget räknar med vad kampanjen betalar för visningar från ditt land. Kampanjer som inte betalar för landet
            sorteras bort.
          </p>
        </div>
        <CountrySelect data={data} busy={busy} onChoose={choose} />
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </section>
  );
}
