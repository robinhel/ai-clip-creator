"use client";

import { useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PLATFORM_NAME } from "@/components/ScheduleDialog";
import { accountName, tiktokAccounts } from "@/components/TikTokCard";
import { channelName } from "@/components/YouTubeCard";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ScheduledPost, ScheduleStatus, TikTokStatus, YouTubeStatus } from "@/lib/types";

// Svensk tid, var webbläsaren än står.
const dayText = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short" });
const timeText = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" });

const STATUS: Record<ScheduleStatus, [string, string]> = {
  waiting: ["Väntar", "border-line text-muted-foreground"],
  running: ["Laddas upp", "border-brand/50 text-brand"],
  done: ["Klar", "border-[#174a2a] text-good"],
  reminded: ["Påmind", "border-[#4a3a17] text-warn"],
  failed: ["Misslyckades", "border-[#4d1d1d] text-bad"],
  cancelled: ["Avbruten", "border-line text-muted-foreground"],
};

interface Props {
  posts: ScheduledPost[];
  youtube: YouTubeStatus | null;
  tiktok: TikTokStatus | null;
  /** Läs om listan efter en avbruten post. */
  onChange: () => void;
}

function Row({ post, account, onChange }: { post: ScheduledPost; account: string | null; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [label, tone] = STATUS[post.status] ?? [post.status, "border-line text-muted-foreground"];
  const url = typeof post.result?.url === "string" ? post.result.url : null;

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await api.campaign.schedule.cancel(post.id);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte avbryta posten");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li data-schedule-id={post.id} className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-t border-line py-3 first:border-t-0">
      <p className="text-right leading-tight tabular-nums">
        <span className="block text-xs text-muted-foreground">{dayText.format(new Date(post.at))}</span>
        <span className="text-base font-semibold">{timeText.format(new Date(post.at))}</span>
      </p>
      <div className="min-w-0">
        <p className="truncate font-medium">{post.clip_title ?? `Klipp ${post.clip_id}`}</p>
        <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
          <span>{PLATFORM_NAME[post.platform] ?? post.platform}</span>
          {account && <span>{account}</span>}
          {post.late && <span className="text-warn">försenad</span>}
        </p>
        {post.error && <p className="mt-0.5 text-xs text-[#fca5a5]">{post.error}</p>}
        {error && (
          <p role="alert" className="mt-0.5 text-xs text-[#fca5a5]">
            {error}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-brand underline-offset-4 hover:underline">
            Öppna
            <ExternalLink className="size-3" aria-hidden />
          </a>
        )}
        <span className={cn("rounded-full border px-2 py-0.5 text-xs", tone)}>{label}</span>
        {post.status === "waiting" && (
          <Button size="sm" variant="outline" disabled={busy} onClick={cancel}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Avbryt
          </Button>
        )}
      </div>
    </li>
  );
}

/** Överst under Att posta: kommande uppladdningar och de senast avslutade. Ordningen är serverns. */
export function ScheduledList({ posts, youtube, tiktok, onChange }: Props) {
  const accounts = tiktok ? tiktokAccounts(tiktok) : [];
  const accountFor = (p: ScheduledPost) => {
    if (!p.account) return null;
    if (p.platform === "youtube") {
      const c = youtube?.accounts.find((x) => x.channel_id === p.account);
      return c ? channelName(c) : null;
    }
    const a = accounts.find((x) => x.open_id === p.account);
    return a ? accountName(a) : null;
  };
  const upcoming = posts.filter((p) => p.status === "waiting" || p.status === "running");
  const finished = posts.filter((p) => !upcoming.includes(p));

  return (
    <section aria-label="Schemalagt" className="mt-6 rounded-2xl border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">
        Schemalagt <span className="font-normal text-muted-foreground">({upcoming.length})</span>
      </h2>
      <p className="text-sm text-muted-foreground">
        Appen laddar upp vid tiden om Macen är igång. Efter uppladdningen får du en notis om att skicka in länken i Content Rewards.
      </p>
      {upcoming.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">Inget schemalagt. Välj Schemalägg på ett godkänt klipp nedan.</p>
      ) : (
        <ul className="mt-2">
          {upcoming.map((p) => (
            <Row key={p.id} post={p} account={accountFor(p)} onChange={onChange} />
          ))}
        </ul>
      )}
      {finished.length > 0 && (
        <details className="mt-3 border-t border-line pt-3" open>
          <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">Senast avslutade ({finished.length})</summary>
          <ul className="mt-1">
            {finished.map((p) => (
              <Row key={p.id} post={p} account={accountFor(p)} onChange={onChange} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
