"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Plus, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import { YOUTUBE_REDIRECT_URI } from "@/lib/appLinks";
import type { YouTubeChannel, YouTubeStatus } from "@/lib/types";

export const channelName = (c: Pick<YouTubeChannel, "title">) => c.title ?? "YouTube channel";

function AddChannel() {
  // Samma flik: Google leder tillbaka till Inställningar efter inloggningen.
  return (
    <a href={api.campaign.youtube.connectUrl} className={buttonVariants({ className: "h-9" })}>
      <Plus aria-hidden />
      Add YouTube channel
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
      setError(err instanceof Error ? err.message : `Could not disconnect ${name}`);
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
              <span className="text-good">Connected</span>
            ) : (
              <a href={api.campaign.youtube.connectUrl} className="font-medium text-bad underline underline-offset-4">
                Log in again
              </a>
            )}
          </p>
        </div>
        {!confirm && (
          <Button variant="outline" className="h-8" onClick={() => setConfirm(true)}>
            Disconnect
          </Button>
        )}
      </div>
      {confirm && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm">
          <span className="mr-auto text-[#fca5a5]">Disconnect {name}?</span>
          <Button variant="destructive" className="h-8" disabled={busy} onClick={disconnect}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Yes, disconnect
          </Button>
          <Button variant="ghost" className="h-8" disabled={busy} onClick={() => setConfirm(false)}>
            Cancel
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
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the YouTube status"));
  }, []);

  useEffect(load, [load, refresh]);

  const channels = status?.accounts ?? [];
  // Adressen som servern använder (YOUTUBE_REDIRECT_URI). En äldre server skickar den inte.
  const redirect = status?.redirect_uri || YOUTUBE_REDIRECT_URI;

  return (
    <section ref={ref} aria-label="YouTube channels" className="mt-5 border-t border-line pt-4 text-sm">
      <h3 className="font-semibold">Channels</h3>

      {callback?.result === "ok" && (
        <p role="status" className="mt-2 flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-good">
          <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          YouTube is connected.
        </p>
      )}
      {callback?.result === "fel" && (
        <p role="alert" className="mt-2 flex items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-[#fca5a5]">
          <XCircle className="size-4 shrink-0" aria-hidden />
          YouTube was not connected: {callback.reason || "unknown error"}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-bad">
          {error}
        </p>
      )}
      {!status && !error && <p className="mt-1 text-muted-foreground">Loading the YouTube status…</p>}

      {status && !status.configured && (
        <p className="mt-1">YouTube is not set up. Client ID and Client secret are missing.</p>
      )}

      {status?.configured &&
        (channels.length === 0 ? (
          <div className="mt-2 rounded-xl border border-dashed border-line px-4 py-5 text-center">
            <p className="text-muted-foreground">No YouTube channels are connected yet.</p>
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
            How to set up YouTube
          </summary>
          <ol className="mt-3 list-decimal space-y-2.5 pl-5">
            <li>
              Enable YouTube Data API v3 in a project on{" "}
              <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                console.cloud.google.com
              </a>
              .
            </li>
            <li>Create an OAuth client of type Web application.</li>
            <li>
              Add the redirect address:
              <span className="mt-1.5 flex flex-wrap items-center gap-2">
                <code className="rounded-md border border-line bg-background px-2 py-1 text-[13px] break-all">{redirect}</code>
                <CopyButton text={redirect} label="Copy" />
              </span>
            </li>
            <li>Add your Google account under Test users on the login page (OAuth consent screen).</li>
            <li>Paste Client ID and Client secret above and press Save. The server uses them right away.</li>
          </ol>
          <p className="mt-2 text-xs text-muted-foreground">
            Medan appen är i läget Testing går inloggningen ut efter 7 dagar. Då står det Log in again vid kanalen.
          </p>
        </details>
      )}
    </section>
  );
}
