"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ClipAccount, ClipAccounts, ClipVariant } from "@/lib/types";

type Platform = "tiktok" | "youtube";

const when = new Intl.DateTimeFormat("en-US", { timeZone: "Europe/Stockholm", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Kampanjens konton för klippet. null hos äldre servrar och innan svaret kommit: då är allt som förut. */
export function useClipAccounts(clipId: number, platform: Platform, enabled = true) {
  const [info, setInfo] = useState<ClipAccounts | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    api.campaign.clips
      .accounts(clipId, platform)
      .then((i) => live && setInfo(i))
      .catch(() => live && setInfo(null));
    return () => {
      live = false;
    };
  }, [clipId, platform, enabled, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { info, reload };
}

/** Ett konto går att välja när det är kopplat och klippet inte redan är postat där. */
// Ett schemalagt konto ger 409 hos servern, precis som ett postat.
const selectable = (a: ClipAccount, connected: string[]) => a.state === "free" && connected.includes(a.account);

/** Det valda kontot, eller första valbara när det valda inte går (postat eller okopplat). Utan multi: oförändrat. */
export function pickAccount(info: ClipAccounts | null, connected: string[], current: string): string {
  if (!info?.multi) return current;
  const ok = info.accounts.filter((a) => selectable(a, connected)).map((a) => a.account);
  return ok.includes(current) ? current : (ok[0] ?? "");
}

function stateText(a: ClipAccount, connected: boolean): string {
  if (a.state === "posted") return "Postat";
  if (a.state === "scheduled") return a.at ? `Schemalagt ${when.format(new Date(a.at))}` : "Schemalagt";
  return connected ? "Ledigt" : "Inte kopplat";
}

/** Kampanjens konton med läge, när kampanjen postar klippet på flera konton på plattformen. */
export function AccountList({ label, info, connected, value, onChange, disabled }: {
  label: string;
  info: ClipAccounts;
  connected: string[];
  value: string;
  onChange: (account: string) => void;
  disabled?: boolean;
}) {
  const name = useId();
  const used = info.accounts.filter((a) => a.state !== "free").length;
  return (
    <div role="radiogroup" aria-label={label} className="space-y-1.5">
      <p className="flex justify-between gap-3 text-[13px] text-muted-foreground">
        <span>{label}</span>
        <span className={cn("tabular-nums", used >= info.max_accounts && "text-warn")}>
          {used} av {info.max_accounts} konton används
        </span>
      </p>
      <div className="divide-y divide-line overflow-hidden rounded-lg border border-line">
        {info.accounts.map((a) => {
          const isConnected = connected.includes(a.account);
          const off = disabled || !selectable(a, connected);
          return (
            <label
              key={a.account}
              data-account={a.account}
              data-state={a.state}
              className={cn(
                "flex items-start gap-2.5 px-3 py-2 text-sm",
                value === a.account && "bg-brand/10",
                off ? "cursor-not-allowed opacity-55" : "cursor-pointer hover:bg-white/5"
              )}
            >
              <input
                type="radio"
                name={name}
                value={a.account}
                checked={value === a.account}
                disabled={off}
                onChange={() => onChange(a.account)}
                className="mt-0.5 size-4 shrink-0 accent-[var(--brand,#7c5cff)]"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.name ?? a.account}</span>
                {a.text && a.state !== "free" && <span className="block truncate text-xs text-muted-foreground">&quot;{a.text}&quot;</span>}
              </span>
              <span
                className={cn(
                  "shrink-0 text-xs tabular-nums",
                  a.state === "free" && isConnected ? "text-good" : a.state === "scheduled" ? "text-brand" : "text-muted-foreground"
                )}
              >
                {stateText(a, isConnected)}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** Föreslå ny text, när klippet redan ligger eller är schemalagt på ett annat av kampanjens konton. */
export function SuggestText({ clipId, platform, account, info, onText }: {
  clipId: number;
  platform: Platform;
  account: string;
  info: ClipAccounts | null;
  onText: (variant: ClipVariant) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!info?.multi || !info.accounts.some((a) => a.account !== account && a.state !== "free")) return null;

  async function suggest() {
    setBusy(true);
    setError(null);
    try {
      onText(await api.campaign.clips.variant(clipId, { platform, account }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte föreslå en ny text");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button variant="outline" className="h-9" disabled={busy || !account} onClick={suggest}>
          {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
          {busy ? "Skriver…" : "Föreslå ny text"}
        </Button>
        <span className="text-xs text-muted-foreground">Klippet finns på ett annat konto. Texten måste skilja sig.</span>
      </div>
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </div>
  );
}
