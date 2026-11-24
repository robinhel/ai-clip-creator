"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Clock, Copy, ExternalLink, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CampaignStatusBadge, PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import { useClipAccounts } from "@/components/ClipAccounts";
import { api } from "@/lib/api";
import { SUMMARY_CHANGED } from "@/lib/utils";
import type { CampaignClip, ClipPost, Platform, PostStatus } from "@/lib/types";

// Ett steg bakåt per inlägg, samma steg som servern tillåter. Ett postat inlägg ångras genom att det tas bort.
const BACK: Partial<Record<PostStatus, PostStatus>> = {
  inskickad: "postad",
  analytics_inskickad: "inskickad",
  betald: "inskickad",
};

// Content Rewards tar bara emot ett inlägg inom 30 minuter efter att det publicerats.
const WINDOW_MS = 30 * 60_000;
const stockholm = new Intl.DateTimeFormat("en-US", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Klippets inlägg, ett per plattform. Fler plattformar läggs till med Publicera i klippraden.
 * readOnly: utanför Klippen visas bara inläggen; inskicket görs i Klippen. */
export function ClipPosts({ clip, onUpdate, readOnly = false }: { clip: CampaignClip; onUpdate: (clip: CampaignClip) => void; readOnly?: boolean }) {
  const posts = clip.posts ?? [];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editUrl, setEditUrl] = useState("");
  // Hjälpen att skicka in. Av tills servern har svarat, och av hos äldre servrar: då är allt som förut.
  const [helper, setHelper] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [asked, setAsked] = useState<number | null>(null);
  const [helpNote, setHelpNote] = useState<string | null>(null);
  const waiting = posts.some((p) => p.status === "postad");
  // Kontonas namn, bara när något inlägg har ett konto.
  const withAccount = (p: Platform) => posts.some((post) => post.platform === p && post.account);
  const { info: ttNames } = useClipAccounts(clip.id, "tiktok", withAccount("tiktok"));
  const { info: ytNames } = useClipAccounts(clip.id, "youtube", withAccount("youtube_shorts"));
  const accountLabel = (post: ClipPost) =>
    (post.platform === "tiktok" ? ttNames : post.platform === "youtube_shorts" ? ytNames : null)?.accounts.find((a) => a.account === post.account)?.name ??
    post.account;

  useEffect(() => {
    api.campaign.preferences
      .get()
      .then((p) => setHelper(p.submit_helper))
      .catch(() => setHelper(false));
  }, []);

  // Tiden kvar räknas om varje minut, och direkt när ett inlägg börjar vänta.
  useEffect(() => {
    if (!helper || !waiting) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, [helper, waiting]);

  async function openRewards(post: ClipPost, rewardsUrl: string) {
    setHelpNote(null);
    // Kopieringen startar medan sidan har fokus (Chrome nekar annars), sedan öppnas fliken.
    const copying = navigator.clipboard.writeText(post.url);
    const tab = window.open(rewardsUrl, "_blank");
    if (tab) tab.opener = null;
    let copied = true;
    try {
      await copying;
    } catch {
      copied = false;
    }
    setAsked(post.id);
    if (!copied) setHelpNote("The browser did not let the app copy the link. Copy it from the row above.");
    else if (!tab) setHelpNote("The browser blocked the new tab. The link is copied; open the campaign in Content Rewards yourself.");
  }

  /** true när servern sparade. */
  async function run(call: () => Promise<CampaignClip>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await call());
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
      return false;
    } finally {
      setBusy(false);
    }
  }

  function remove(post: ClipPost) {
    if (!window.confirm(`Remove the post on ${PLATFORM_LABEL[post.platform]}? The link and the numbers are deleted.`)) return;
    run(() => api.campaign.posts.remove(post.id));
  }

  return (
    <section aria-label="Posts" className="space-y-2">
      <p className="text-[13px] text-muted-foreground">Posts</p>
      <ul className="space-y-2">
        {posts.map((post) => (
          <li key={post.id} data-post={post.platform} className="space-y-2 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{PLATFORM_LABEL[post.platform]}</span>
              {post.account && (
                <span data-post-account className="text-xs text-muted-foreground">
                  {accountLabel(post)}
                </span>
              )}
              <CampaignStatusBadge status={post.status} />
              {post.views != null && (
                <span className="text-xs text-muted-foreground tabular-nums">{post.views.toLocaleString("en-US")} views</span>
              )}
            </div>
            {editing === post.id ? (
              <div className="space-y-2">
                <Input
                  aria-label={`New link to the post on ${PLATFORM_LABEL[post.platform]}`}
                  type="url"
                  value={editUrl}
                  onChange={(e) => setEditUrl(e.target.value)}
                  disabled={busy}
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    disabled={busy || !editUrl.trim() || editUrl.trim() === post.url}
                    onClick={async () => {
                      if (await run(() => api.campaign.posts.patch(post.id, { url: editUrl.trim() }))) setEditing(null);
                    }}
                  >
                    Save the link
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>
                    Avbryt
                  </Button>
                </div>
              </div>
            ) : (
              <a
                href={post.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex max-w-full min-w-0 items-center gap-1 break-all underline underline-offset-4"
              >
                {post.url}
                <ExternalLink className="size-3.5 shrink-0" aria-hidden />
              </a>
            )}
            {!readOnly && helper && post.status === "postad" && editing !== post.id && (() => {
              const deadline = Date.parse(post.created) + WINDOW_MS;
              const left = Math.ceil((deadline - now) / 60_000);
              return (
                <div data-submit-help className="space-y-2 rounded-lg border border-line bg-background p-2.5">
                  {left > 0 ? (
                    <p className="flex items-start gap-1.5 text-[13px] tabular-nums">
                      <Clock className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden />
                      <span>
                        Submit before {stockholm.format(deadline)} ({left} min left).{" "}
                        <span className="text-muted-foreground">The app counts 30 minutes from when the link was saved.</span>
                      </span>
                    </p>
                  ) : (
                    <p className="flex items-start gap-1.5 rounded-lg border border-[#3a3018] px-2.5 py-1.5 text-[13px] text-warn/80">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                      More than 30 minutes have passed. Content Rewards will probably not accept the post.
                    </p>
                  )}
                  {clip.content_rewards_url ? (
                    <Button size="sm" className="h-auto min-h-7 py-1.5 whitespace-normal" disabled={busy} onClick={() => openRewards(post, clip.content_rewards_url!)}>
                      <Copy aria-hidden />
                      Copy the link and open Content Rewards
                    </Button>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      The link to the campaign in Content Rewards is missing.{" "}
                      <Link href={`/campaigns/${clip.profile}?tab=installningar&del=oversikt#lankar`} className="underline underline-offset-4 hover:text-foreground">
                        Add it under the campaign's Set up
                      </Link>
                      .
                    </p>
                  )}
                  {asked === post.id && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
                      <span>Did the submission go through?</span>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api.campaign.posts.patch(post.id, { status: "inskickad" }))}>
                        Yes, mark submitted
                      </Button>
                    </div>
                  )}
                  {asked === post.id && helpNote && <p className="text-xs text-muted-foreground">{helpNote}</p>}
                </div>
              );
            })()}
            {!readOnly && editing !== post.id && (
              <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                {/* Efter Copy the link and open Content Rewards frågar hjälpen själv; en knapp räcker. */}
                {post.status === "postad" && asked !== post.id && (
                  <Button size="sm" variant={helper ? "outline" : "default"} disabled={busy} onClick={() => run(() => api.campaign.posts.patch(post.id, { status: "inskickad" }))}>
                    <Send aria-hidden />
                    Submitted in Content Rewards
                  </Button>
                )}
                {BACK[post.status] && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => run(() => api.campaign.posts.patch(post.id, { status: BACK[post.status] }))}
                  >
                    Undo
                  </Button>
                )}
                {post.status === "postad" && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setEditUrl(post.url);
                        setEditing(post.id);
                      }}
                    >
                      Change link
                    </Button>
                    <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={busy} onClick={() => remove(post)}>
                      Remove
                    </Button>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>

      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </section>
  );
}
