"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, Plus, XCircle } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import { TIKTOK_APP, TIKTOK_REDIRECT_URI, TIKTOK_VERIFIED_PREFIX } from "@/lib/appLinks";
import { cn } from "@/lib/utils";
import type { TikTokAccount, TikTokStatus } from "@/lib/types";

/** Kontona. En äldre server har bara ett konto på toppnivå; det får open_id "". */
export function tiktokAccounts(status: TikTokStatus): TikTokAccount[] {
  if (status.accounts) return status.accounts;
  if (!status.connected) return [];
  const { display_name, avatar_url, scopes, refresh_expires_at, pending_24h } = status;
  return [{ open_id: "", display_name, avatar_url, scopes, connected: true, refresh_expires_at, pending_24h }];
}

export const accountName = (a: Pick<TikTokAccount, "display_name">) => a.display_name ?? "TikTok account";

function AddAccount() {
  // Samma flik: TikTok leder tillbaka till Inställningar efter inloggningen.
  return (
    <a href={api.campaign.tiktok.connectUrl} className={buttonVariants({ className: "h-9" })}>
      <Plus aria-hidden />
      Add TikTok account
    </a>
  );
}

/** Adresserna som är inlagda i TikTok-appen. Visas alltid, så att de går att hitta när något ska ändras där.
 *  redirect: adressen som servern använder (TIKTOK_REDIRECT_URI), så att samma adress står i portalen. */
function AppLinks({ redirect }: { redirect: string }) {
  return (
    <details className="mt-3" data-app-links>
      <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
        Settings in the TikTok app
      </summary>
      <p className="mt-2 text-muted-foreground">
        This is what the app robin-klipp-verktyg has on{" "}
        <a href="https://developers.tiktok.com/apps" target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
          developers.tiktok.com
        </a>
        . The pages are in the repo robinhel/robinhel.github.io (GitHub Pages). TikTok has verified the prefix{" "}
        <code className="text-[13px]">{TIKTOK_VERIFIED_PREFIX}</code>.
      </p>
      <dl className="mt-3 space-y-3">
        {TIKTOK_APP.map((l) => (l.id === "redirect" ? { ...l, url: redirect } : l)).map((link) => (
          <div key={link.id}>
            <dt className="text-[13px] text-muted-foreground">{link.label}</dt>
            <dd className="mt-1 flex flex-wrap items-center gap-2">
              {link.page ? (
                <a
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 rounded-md border border-line bg-background px-2 py-1 text-[13px] break-all underline-offset-4 hover:underline"
                >
                  {link.url}
                  <ExternalLink className="size-3 shrink-0" aria-hidden />
                </a>
              ) : (
                // Adressen på den egna datorn öppnas inte: servern väntar bara på TikToks svar där.
                <code className="rounded-md border border-line bg-background px-2 py-1 text-[13px] break-all">{link.url}</code>
              )}
              <CopyButton text={link.url} label="Copy" />
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function AccountRow({ account: a, limit, onDisconnected }: { account: TikTokAccount; limit: number; onDisconnected: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = accountName(a);

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      // En äldre server kan bara koppla från alla, och har då bara ett konto.
      await (a.open_id ? api.campaign.tiktok.disconnectAccount(a.open_id) : api.campaign.tiktok.disconnect());
      onDisconnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${name}`);
      setBusy(false);
    }
  }

  return (
    <li data-account={a.open_id} className="py-3">
      <div className="flex flex-wrap items-center gap-3">
        {a.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- TikToks profilbild.
          <img src={a.avatar_url} alt="" className="size-10 shrink-0 rounded-full object-cover" />
        ) : (
          <div className="grid size-10 shrink-0 place-items-center rounded-full bg-line text-sm font-semibold text-muted-foreground uppercase">
            {name.slice(0, 1)}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{name}</p>
          <p className="flex flex-wrap items-center gap-x-2 text-[13px] text-muted-foreground">
            {a.connected ? (
              <span className="text-good">Connected</span>
            ) : (
              <a href={api.campaign.tiktok.connectUrl} className="font-medium text-bad underline underline-offset-4">
                Log in again
              </a>
            )}
            <span className="tabular-nums">
              {a.pending_24h} of {limit} drafts waiting
            </span>
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

interface Props {
  /** ?tiktok=ok eller ?tiktok=fel från serverns callback. */
  result?: string;
  reason?: string;
  /** Ändras när TikTok-nycklarna sparas, så att läget läses om. */
  refresh?: string;
}

/** TikTok-kontona, så att godkända klipp kan skickas som utkast till rätt kontos inkorg. */
export function TikTokCard({ result: fromUrl, reason: reasonFromUrl, refresh }: Props) {
  // Svaret från callbacken sparas här, så att meddelandet står kvar när ?tiktok= tas bort ur adressen.
  const [{ result, reason }] = useState({ result: fromUrl, reason: reasonFromUrl });
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLElement>(null);

  // När meddelandet har visats: ta bort ?tiktok= och ?orsak=, så att det inte visas igen vid omladdning.
  useEffect(() => {
    if (!result || (!status && !error)) return;
    ref.current?.scrollIntoView({ block: "center" }); // kortet ligger långt ner på sidan
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
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the TikTok status"));
  }, []);

  useEffect(load, [load, refresh]);

  const accounts = status ? tiktokAccounts(status) : [];
  const redirect = status?.redirect_uri || TIKTOK_REDIRECT_URI;

  return (
    <section ref={ref} aria-label="TikTok accounts" className="mt-5 border-t border-line pt-4 text-sm">
      <h3 className="font-semibold">Accounts</h3>

      {result === "ok" && status && accounts.some((a) => a.connected) && (
        <p role="status" className="mt-2 flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-good">
          <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          TikTok is connected.
        </p>
      )}
      {result === "fel" && (
        <p role="alert" className="mt-2 flex items-center gap-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-[#fca5a5]">
          <XCircle className="size-4 shrink-0" aria-hidden />
          TikTok was not connected: {reason || "unknown error"}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-bad">
          {error}
        </p>
      )}
      {!status && !error && <p className="mt-1 text-muted-foreground">Loading the TikTok status…</p>}

      {status && !status.configured && (
        <>
          <p className="mt-1">TikTok is not set up. Client key and Client secret are missing.</p>
          <p className="mt-1 text-muted-foreground">
            When TikTok is set up, you can connect accounts and send approved clips as drafts to the TikTok inbox.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
              How to set up TikTok
            </summary>
            <ol className="mt-3 list-decimal space-y-2.5 pl-5">
              <li>
                Create an app of type Desktop on{" "}
                <a href="https://developers.tiktok.com/apps" target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                  developers.tiktok.com
                </a>
                .
              </li>
              <li>Add the products Login Kit and Content Posting API.</li>
              <li>
                Choose the scopes <code className="text-[13px]">user.info.basic</code>,{" "}
                <code className="text-[13px]">video.upload</code> and <code className="text-[13px]">video.publish</code>.
                Publish directly needs <code className="text-[13px]">video.publish</code>.
              </li>
              <li>
                Add the redirect address:
                <span className="mt-1.5 flex flex-wrap items-center gap-2">
                  <code className="rounded-md border border-line bg-background px-2 py-1 text-[13px] break-all">{redirect}</code>
                  <CopyButton text={redirect} label="Copy" />
                </span>
              </li>
              <li>Add your TikTok accounts under Target users in Sandbox.</li>
              <li>Paste Client key and Client secret above and press Save. The server uses them right away.</li>
            </ol>
          </details>
        </>
      )}

      {status?.configured && (
        <>
          {accounts.length === 0 ? (
            <div className="mt-2 rounded-xl border border-dashed border-line px-4 py-5 text-center">
              <p className="text-muted-foreground">No TikTok accounts are connected yet.</p>
              <div className="mt-3">
                <AddAccount />
              </div>
            </div>
          ) : (
            <>
              <ul className="divide-y divide-line">
                {accounts.map((a) => (
                  <AccountRow key={a.open_id} account={a} limit={status.pending_limit} onDisconnected={load} />
                ))}
              </ul>
              <div className="mt-2">
                <AddAccount />
              </div>
            </>
          )}
          <p className={cn("mt-2 text-xs text-muted-foreground", accounts.length === 0 && "text-center")}>
            TikTok asks which account you want to use. While the app is in sandbox, the account must be under Target users in
            the developer portal.
          </p>
        </>
      )}

      {status && <AppLinks redirect={redirect} />}
    </section>
  );
}
