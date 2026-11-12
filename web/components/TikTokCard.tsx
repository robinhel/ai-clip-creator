"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import type { TikTokStatus } from "@/lib/types";

const REDIRECT_URI = "http://127.0.0.1:8000/campaign/tiktok/callback/";

interface Props {
  /** ?tiktok=ok eller ?tiktok=fel från serverns callback. */
  result?: string;
  reason?: string;
}

/** Koppla TikTok-kontot, så att godkända klipp kan skickas som utkast till inkorgen. */
export function TikTokCard({ result: fromUrl, reason: reasonFromUrl }: Props) {
  // Svaret från callbacken sparas här, så att meddelandet står kvar när ?tiktok= tas bort ur adressen.
  const [{ result, reason }] = useState({ result: fromUrl, reason: reasonFromUrl });
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // När meddelandet har visats: ta bort ?tiktok= och ?orsak=, så att det inte visas igen vid omladdning.
  useEffect(() => {
    if (!result || (!status && !error)) return;
    const url = new URL(window.location.href);
    if (!url.searchParams.has("tiktok")) return;
    url.searchParams.delete("tiktok");
    url.searchParams.delete("orsak");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, [result, status, error]);

  const load = useCallback(() => {
    api.campaign.tiktok
      .status()
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte läsa läget för TikTok"));
  }, []);

  useEffect(load, [load]);

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api.campaign.tiktok.disconnect();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte koppla från TikTok");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-10">
      <h2 className="mb-3 text-lg font-semibold">TikTok</h2>

      {result === "ok" && status?.connected && (
        <p role="status" className="mb-3 flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
          <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          TikTok är kopplat.
        </p>
      )}
      {result === "fel" && (
        <p role="alert" className="mb-3 flex items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          <XCircle className="size-4 shrink-0" aria-hidden />
          TikTok kopplades inte: {reason || "okänt fel"}
        </p>
      )}

      <div className="max-w-[640px] rounded-2xl border border-line bg-panel p-5 text-sm">
        {error && (
          <p role="alert" className="mb-3 text-bad">
            {error}
          </p>
        )}
        {!status && !error && <p className="text-muted-foreground">Läser läget för TikTok…</p>}

        {status && !status.configured && (
          <>
            <p>
              TikTok är inte inställt. Nycklarna saknas i <code className="text-[13px]">.env</code> på servern.
            </p>
            <p className="mt-1 text-muted-foreground">
              När TikTok är kopplat kan du skicka godkända klipp som utkast till TikTok-inkorgen och posta dem i appen.
            </p>
            <details className="mt-4">
              <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
                Så ställer du in TikTok
              </summary>
              <ol className="mt-3 list-decimal space-y-2.5 pl-5">
                <li>
                  Skapa en app av typen Desktop på{" "}
                  <a href="https://developers.tiktok.com/apps" target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                    developers.tiktok.com
                  </a>
                  .
                </li>
                <li>Lägg till produkterna Login Kit och Content Posting API.</li>
                <li>
                  Välj scopes <code className="text-[13px]">user.info.basic</code> och{" "}
                  <code className="text-[13px]">video.upload</code>.
                </li>
                <li>
                  Lägg in redirect-adressen:
                  <span className="mt-1.5 flex flex-wrap items-center gap-2">
                    <code className="rounded-md border border-line bg-background px-2 py-1 text-[13px]">{REDIRECT_URI}</code>
                    <CopyButton text={REDIRECT_URI} label="Kopiera" />
                  </span>
                </li>
                <li>Lägg till ditt TikTok-konto som testanvändare under Sandbox.</li>
                <li>
                  Lägg <code className="text-[13px]">TIKTOK_CLIENT_KEY</code> och{" "}
                  <code className="text-[13px]">TIKTOK_CLIENT_SECRET</code> i <code className="text-[13px]">.env</code> och
                  starta om servern.
                </li>
              </ol>
            </details>
          </>
        )}

        {status?.configured && !status.connected && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-muted-foreground">TikTok är inställt men inget konto är kopplat.</p>
            {/* Samma flik: TikTok leder tillbaka hit efter inloggningen. */}
            <a href={api.campaign.tiktok.connectUrl} className={buttonVariants({ className: "h-9" })}>
              Koppla TikTok
            </a>
          </div>
        )}

        {status?.configured && status.connected && (
          <div className="flex flex-wrap items-center gap-4">
            {status.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element -- TikToks profilbild.
              <img src={status.avatar_url} alt="" className="size-12 rounded-full object-cover" />
            ) : (
              <div className="size-12 rounded-full bg-line" />
            )}
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{status.display_name ?? "Kopplat konto"}</p>
              <p className="text-muted-foreground tabular-nums">
                Utkast senaste 24 h: {status.pending_24h} av {status.pending_limit}
              </p>
            </div>
            <Button variant="outline" className="h-9" disabled={busy} onClick={disconnect}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              Koppla från
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
