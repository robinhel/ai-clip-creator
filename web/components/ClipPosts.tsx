"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Clock, Copy, ExternalLink, Plus, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { CampaignStatusBadge, PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
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
const stockholm = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" });

/** Klippets inlägg, ett per plattform, och ett formulär för att lägga till fler. */
export function ClipPosts({ clip, onUpdate }: { clip: CampaignClip; onUpdate: (clip: CampaignClip) => void }) {
  const posts = clip.posts ?? [];
  const free = (Object.keys(PLATFORM_LABEL) as Platform[]).filter((p) => !posts.some((post) => post.platform === p));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editUrl, setEditUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [platform, setPlatform] = useState<Platform | "">("");
  const [url, setUrl] = useState("");
  // Hjälpen att skicka in. Av tills servern har svarat, och av hos äldre servrar: då är allt som förut.
  const [helper, setHelper] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [asked, setAsked] = useState<number | null>(null);
  const [helpNote, setHelpNote] = useState<string | null>(null);
  const waiting = posts.some((p) => p.status === "postad");

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
    if (!copied) setHelpNote("Webbläsaren lät inte appen kopiera länken. Kopiera den från raden ovanför.");
    else if (!tab) setHelpNote("Webbläsaren stoppade den nya fliken. Länken är kopierad; öppna kampanjen i Content Rewards själv.");
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
      setError(err instanceof Error ? err.message : "Det gick inte att spara");
      return false;
    } finally {
      setBusy(false);
    }
  }

  function remove(post: ClipPost) {
    if (!window.confirm(`Ta bort inlägget på ${PLATFORM_LABEL[post.platform]}? Länken och siffrorna försvinner.`)) return;
    run(() => api.campaign.posts.remove(post.id));
  }

  return (
    <section aria-label="Inlägg" className="space-y-2">
      <p className="text-[13px] text-muted-foreground">Inlägg</p>
      <ul className="space-y-2">
        {posts.map((post) => (
          <li key={post.id} data-post={post.platform} className="space-y-2 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{PLATFORM_LABEL[post.platform]}</span>
              <CampaignStatusBadge status={post.status} />
              {post.views != null && (
                <span className="text-xs text-muted-foreground tabular-nums">{post.views.toLocaleString("sv-SE")} visningar</span>
              )}
            </div>
            {editing === post.id ? (
              <div className="space-y-2">
                <Input
                  aria-label={`Ny länk till inlägget på ${PLATFORM_LABEL[post.platform]}`}
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
                    Spara länken
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
            {helper && post.status === "postad" && editing !== post.id && (() => {
              const deadline = Date.parse(post.created) + WINDOW_MS;
              const left = Math.ceil((deadline - now) / 60_000);
              return (
                <div data-submit-help className="space-y-2 rounded-lg border border-line bg-background p-2.5">
                  {left > 0 ? (
                    <p className="flex items-center gap-1.5 text-[13px] tabular-nums">
                      <Clock className="size-3.5 shrink-0 text-brand" aria-hidden />
                      Skicka in före kl. {stockholm.format(deadline)} ({left} min kvar)
                    </p>
                  ) : (
                    <p className="flex items-start gap-1.5 rounded-lg border border-[#3a3018] px-2.5 py-1.5 text-[13px] text-warn/80">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                      Mer än 30 minuter har gått. Content Rewards tar troligen inte emot inlägget.
                    </p>
                  )}
                  {clip.content_rewards_url ? (
                    <Button size="sm" disabled={busy} onClick={() => openRewards(post, clip.content_rewards_url!)}>
                      <Copy aria-hidden />
                      Kopiera länken och öppna Content Rewards
                    </Button>
                  ) : (
                    <p className="text-xs text-muted-foreground">Länken till kampanjen i Content Rewards saknas.</p>
                  )}
                  {asked === post.id && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
                      <span>Gick inskicket igenom?</span>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api.campaign.posts.patch(post.id, { status: "inskickad" }))}>
                        Ja, markera inskickad
                      </Button>
                    </div>
                  )}
                  {asked === post.id && helpNote && <p className="text-xs text-muted-foreground">{helpNote}</p>}
                </div>
              );
            })()}
            {editing !== post.id && (
              <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                {post.status === "postad" && (
                  <Button size="sm" variant={helper ? "outline" : "default"} disabled={busy} onClick={() => run(() => api.campaign.posts.patch(post.id, { status: "inskickad" }))}>
                    <Send aria-hidden />
                    Inskickad i Content Rewards
                  </Button>
                )}
                {BACK[post.status] && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => run(() => api.campaign.posts.patch(post.id, { status: BACK[post.status] }))}
                  >
                    Ångra
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
                      Ändra länk
                    </Button>
                    <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={busy} onClick={() => remove(post)}>
                      Ta bort
                    </Button>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>

      {free.length > 0 &&
        (adding ? (
          <div className="space-y-2 rounded-[10px] border border-line bg-panel p-3">
            <NativeSelect aria-label="Plattform för det nya inlägget" value={platform} onChange={(e) => setPlatform(e.target.value as Platform | "")} disabled={busy}>
              <option value="" disabled>
                Plattform…
              </option>
              {free.map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABEL[p]}
                </option>
              ))}
            </NativeSelect>
            <Input
              aria-label="Länk till det nya inlägget"
              type="url"
              placeholder="https://…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={busy || !platform || !url.trim()}
                onClick={async () => {
                  if (await run(() => api.campaign.posts.add(clip.id, { platform: platform as Platform, url: url.trim() }))) {
                    setAdding(false);
                    setPlatform("");
                    setUrl("");
                  }
                }}
              >
                Spara inlägget
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setAdding(false)}>
                Avbryt
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setAdding(true)}>
            <Plus aria-hidden />
            Postat på fler plattformar
          </Button>
        ))}

      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </section>
  );
}
