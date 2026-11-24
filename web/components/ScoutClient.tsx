"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScoutCard, existingCampaign } from "@/components/ScoutCard";
import { api } from "@/lib/api";
import { serverTime } from "@/lib/utils";
import type { CampaignProfile, ScoutCampaign, ScoutStatus } from "@/lib/types";

const views = new Intl.NumberFormat("en-US");
const when = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
// Servern kör hämtningen i bakgrunden efter 202, och den tar upp till cirka 25 s.
// Läs om var 3:e sekund tills updated_at eller error ändras, högst 30 s.
const POLL_MS = 3000;
const POLL_MAX_MS = 30_000;
const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

/** Fliken Nya kampanjer under Kampanjer. campaigns: de inlagda, så att korten kan visa Finns redan. */
export function ScoutClient({ campaigns = null }: { campaigns?: CampaignProfile[] | null }) {
  const [status, setStatus] = useState<ScoutStatus | null>(null);
  // Äldre server utan /campaign/scout, eller servern svarar inte.
  const [off, setOff] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [hidden, setHidden] = useState<ScoutCampaign | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.campaign.scout
      .status()
      .then((s) => {
        setStatus(s);
        setOff(false);
      })
      .catch(() => setOff(true));
  }, []);

  useEffect(load, [load]);

  async function refresh() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api.campaign.scout.refresh();
      for (const start = Date.now(); Date.now() - start < POLL_MAX_MS; ) {
        await sleep(POLL_MS);
        const next = await api.campaign.scout.status().catch(() => null);
        if (!next) continue; // ett misslyckat anrop: försök igen nästa varv
        setStatus(next);
        if (next.updated_at !== status?.updated_at || next.error !== status?.error) return;
      }
      setRefreshError("The check takes longer than usual. The list reloads the next time you open the page.");
    } catch (err) {
      setRefreshError(err instanceof Error ? err.message : "Could not start the check");
    } finally {
      setRefreshing(false);
    }
  }

  async function setDismissed(c: ScoutCampaign, dismissed: boolean) {
    setBusy(c.ref);
    setError(null);
    try {
      await api.campaign.scout.setDismissed(c.ref, dismissed);
      setHidden(dismissed ? c : null);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the campaign");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-label="New campaigns">
      <p className="max-w-[640px] text-muted-foreground">
        New clipping campaigns from ClippingAlpha with scores. What weighs most is how many views clips of the campaign's content
        usually get. Then come fresh campaigns with few clippers, a low payment limit and budget that lasts. Add the
        campaign fills in New campaign for you.
      </p>

      {off && !status && (
        <p role="alert" className="mt-7 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          The app server is not responding, so the list cannot be read. Start the server and reload the page.
        </p>
      )}
      {!status && !off && <p className="mt-7 text-sm text-muted-foreground">Loading the list…</p>}

      {status && (
        <>
          <section
            aria-label="Scout status"
            className="mt-7 flex flex-wrap items-start justify-between gap-4 rounded-2xl border border-line bg-panel px-5 py-4"
          >
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">Last checked</dt>
                <dd className="tabular-nums">{status.updated_at ? when.format(serverTime(status.updated_at)) : "Not yet"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Fetches today</dt>
                <dd className="tabular-nums" title="ClippingAlpha allows a certain number of fetches per day.">
                  {status.calls_today} of at most {status.call_limit}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Your typical views per clip</dt>
                <dd className="tabular-nums" title="The score assumes a clip gets that many views.">
                  {views.format(status.median_views)}{" "}
                  <span className="text-muted-foreground">
                    ({status.median_source === "dina inlägg" ? "from your posts" : "estimated, no posts yet"})
                  </span>
                </dd>
              </div>
            </dl>
            <div className="space-y-1.5 text-right">
              <Button variant="outline" className="h-9" disabled={refreshing} onClick={refresh}>
                {refreshing ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
                {refreshing ? "Checking…" : "Check now"}
              </Button>
              {refreshError && <p className="text-xs text-muted-foreground">{refreshError}</p>}
            </div>
            {status.error && (
              <p role="alert" className="w-full rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
                {status.error}
              </p>
            )}
            {!status.enabled && (
              <p className="w-full text-xs text-muted-foreground">
                The list is not checked automatically, because KLIPP_SCOUT=0 is set in .env. Press Check now.
              </p>
            )}
          </section>

          {hidden && (
            <p role="status" className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {hidden.title} is hidden.
              <Button variant="ghost" className="h-8" disabled={busy === hidden.ref} onClick={() => setDismissed(hidden, false)}>
                Undo
              </Button>
            </p>
          )}
          {error && (
            <p role="alert" className="mt-4 text-sm text-bad">
              {error}
            </p>
          )}

          {/* Vid fel visas bara felet, inte också att listan är tom. */}
          {status.campaigns.length === 0 && !status.error ? (
            <p className="mt-6 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
              No new campaigns yet. The list is checked every 10 minutes.
            </p>
          ) : (
            <div className="mt-6 space-y-3">
              {status.campaigns.map((c) => (
                <ScoutCard
                  key={c.ref}
                  campaign={c}
                  busy={busy === c.ref}
                  existing={existingCampaign(c, campaigns)}
                  onDismiss={() => setDismissed(c, true)}
                />
              ))}
            </div>
          )}

          {status.excluded.length > 0 && (
            <details className="mt-8">
              <summary className="cursor-pointer text-sm text-muted-foreground select-none hover:text-foreground">
                Filtered out ({status.excluded.length})
              </summary>
              <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-panel px-5">
                {status.excluded.map((c) => (
                  <li key={c.ref} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 py-2.5 text-sm">
                    <span className="font-medium">{c.title}</span>
                    <span className="text-muted-foreground">{c.excluded}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
