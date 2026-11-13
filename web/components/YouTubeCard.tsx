"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Plus, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import type { YouTubeChannel, YouTubeStatus } from "@/lib/types";

const REDIRECT_URI = "http://127.0.0.1:8000/campaign/youtube/callback/";

export const channelName = (c: Pick<YouTubeChannel, "title">) => c.title ?? "YouTube-kanal";

function AddChannel() {
  // Samma flik: Google leder tillbaka till Inställningar efter inloggningen.
  return (
    <a href={api.campaign.youtube.connectUrl} className={buttonVariants({ className: "h-9" })}>
      <Plus aria-hidden />
      Lägg till YouTube-kanal
    </a>
  );
}

function ChannelRow({ channel: c, onDisconnected }: { channel: YouTubeChannel; onDisconnected: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = channelName(c);
  const ok = c.connected && !c.needs_login;

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api.campaign.youtube.disconnect(c.channel_id);
      onDisconnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Kunde inte koppla från ${name}`);
      setBusy(false);
    }
  }

  return (
    <li data-channel={c.channel_id} className="py-3">
      <div className="flex flex-wrap items-center gap-3">
        {c.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- kanalens bild från YouTube.
          <img src={c.avatar_url} alt="" className="size-10 shrink-0 rounded-full object-cover" />
        ) : (
          <div className="grid size-10 shrink-0 place-items-center rounded-full bg-line text-sm font-semibold text-muted-foreground uppercase">
            {name.slice(0, 1)}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">
            {name}
            {c.handle && <span className="ml-1.5 font-normal text-muted-foreground">{c.handle}</span>}
          </p>
          <p className="text-[13px]">
            {ok ? (
              <span className="text-good">Kopplad</span>
            ) : (
              <a href={api.campaign.youtube.connectUrl} className="font-medium text-bad underline underline-offset-4">
                Logga in igen
              </a>
            )}
          </p>
        </div>
        {!confirm && (
          <Button variant="outline" className="h-8" onClick={() => setConfirm(true)}>
            Koppla från
          </Button>
        )}
      </div>
      {confirm && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm">
          <span className="mr-auto text-[#fca5a5]">Koppla från {name}?</span>
          <Button variant="destructive" className="h-8" disabled={busy} onClick={disconnect}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Ja, koppla från
          </Button>
          <Button variant="ghost" className="h-8" disabled={busy} onClick={() => setConfirm(false)}>
            Avbryt
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-bad">
          {error}
        </p>
      )}
    </li>
  );
}

/** YouTube-kanalerna, så att godkända klipp kan laddas upp som Shorts. */
export function YouTubeCard({ refresh }: { /** Ändras när YouTube-nycklarna sparas, så att läget läses om. */ refresh?: string }) {
  // ?youtube=ok eller ?youtube=fel&orsak= från serverns callback. Läses en gång, så att raden står kvar när frågan tas bort.
  const [callback, setCallback] = useState<{ result: string; reason: string } | null>(null);
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const result = url.searchParams.get("youtube");
    if (!result) return;
    setCallback({ result, reason: url.searchParams.get("orsak") ?? "" });
    url.searchParams.delete("youtube");
    url.searchParams.delete("orsak");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, []);

  useEffect(() => {
    if (callback && (status || error)) ref.current?.scrollIntoView({ block: "center" }); // kortet ligger långt ner
  }, [callback, status, error]);

  const load = useCallback(() => {
    api.campaign.youtube
      .status()
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte läsa läget för YouTube"));
  }, []);

  useEffect(load, [load, refresh]);

  const channels = status?.accounts ?? [];

  return (
    <section ref={ref} aria-label="YouTube-kanaler" className="mt-5 border-t border-line pt-4 text-sm">
      <h3 className="font-semibold">Kanaler</h3>

      {callback?.result === "ok" && (
        <p role="status" className="mt-2 flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-good">
          <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          YouTube är kopplat.
        </p>
      )}
      {callback?.result === "fel" && (
        <p role="alert" className="mt-2 flex items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-[#fca5a5]">
          <XCircle className="size-4 shrink-0" aria-hidden />
          YouTube kopplades inte: {callback.reason || "okänt fel"}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-bad">
          {error}
        </p>
      )}
      {!status && !error && <p className="mt-1 text-muted-foreground">Läser läget för YouTube…</p>}

      {status && !status.configured && (
        <p className="mt-1">YouTube är inte inställt. Client ID och Client secret saknas.</p>
      )}

      {status?.configured &&
        (channels.length === 0 ? (
          <div className="mt-2 rounded-xl border border-dashed border-line px-4 py-5 text-center">
            <p className="text-muted-foreground">Inga YouTube-kanaler är kopplade än.</p>
            <div className="mt-3">
              <AddChannel />
            </div>
          </div>
        ) : (
          <>
            <ul className="divide-y divide-line">
              {channels.map((c) => (
                <ChannelRow key={c.channel_id} channel={c} onDisconnected={load} />
              ))}
            </ul>
            <div className="mt-2">
              <AddChannel />
            </div>
          </>
        ))}

      {status && (
        <details className="mt-3">
          <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
            Så ställer du in YouTube
          </summary>
          <ol className="mt-3 list-decimal space-y-2.5 pl-5">
            <li>
              Aktivera YouTube Data API v3 i ett projekt på{" "}
              <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                console.cloud.google.com
              </a>
              .
            </li>
            <li>Skapa en OAuth-klient av typen Web application.</li>
            <li>
              Lägg in redirect-adressen:
              <span className="mt-1.5 flex flex-wrap items-center gap-2">
                <code className="rounded-md border border-line bg-background px-2 py-1 text-[13px]">{REDIRECT_URI}</code>
                <CopyButton text={REDIRECT_URI} label="Kopiera" />
              </span>
            </li>
            <li>Lägg till ditt Google-konto under Test users på sidan för inloggningen (OAuth consent screen).</li>
            <li>Klistra in Client ID och Client secret ovanför och tryck Spara.</li>
          </ol>
          <p className="mt-2 text-xs text-muted-foreground">
            Medan appen är i läget Testing går inloggningen ut efter 7 dagar. Då står det Logga in igen vid kanalen.
          </p>
        </details>
      )}
    </section>
  );
}
