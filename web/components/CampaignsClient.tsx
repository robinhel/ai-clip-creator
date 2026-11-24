"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, KeyRound, Plus } from "lucide-react";
import { ScoutClient } from "@/components/ScoutClient";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfile } from "@/lib/types";

// Profilens problem som gör varje klipp underkänt i regelkontrollen (campaign/rules.py).
const FAILS_CLIPS = ["Overlay-bilden saknas", "on_screen_text.text är tom"];

/** Vad kampanjens läge betyder för körningen. Servern skickar samma text som Kontroll (state). */
export function readyText(p: CampaignProfile): string {
  if (p.state) return p.state;
  if (!p.ready) return "Not ready";
  if (p.problems.length === 0) return "Ready";
  if (p.problems.some((problem) => FAILS_CLIPS.some((start) => problem.startsWith(start)))) {
    return "Can run, but the clips fail the check until the warnings are fixed";
  }
  return "Can run, but fix the warnings in the campaign settings first";
}

/** Nycklar som saknas. En äldre server har dem bara i problems. */
export const keyProblems = (p: CampaignProfile) =>
  p.key_problems ?? p.problems.filter((problem) => /API_KEY (saknas|is missing)/.test(problem));

/** Kampanjens egna problem, utan nycklarna, som visas en gång överst. */
export const ownProblems = (p: CampaignProfile) => {
  const keys = keyProblems(p);
  return p.problems.filter((problem) => !keys.includes(problem));
};

// Problem som rättas på ett annat ställe. Texten kommer från servern (campaign/profile.py och campaign/api.py).
const FIX = [
  { start: "Overlay-bilden saknas", href: (id: string) => `/campaigns/${id}?tab=installningar&del=overlay`, label: "Upload the overlay image" },
  { start: "Facecam- och spelrutan är inte inställd", href: (id: string) => `/campaigns/${id}?tab=installningar&del=facecam`, label: "Set up the boxes" },
  { start: "on_screen_text.text är tom", href: (id: string) => `/campaigns/${id}?tab=installningar&del=oversikt#caption`, label: "Write the text under Set up" },
  { start: "Kampanjen saknar clipsmith_config", href: (id: string) => `/campaigns/${id}?tab=installningar&del=profil`, label: "Open Advanced (YAML)" },
  { start: "Facecam and game boxes", href: (id: string) => `/campaigns/${id}?tab=installningar&del=facecam`, label: "Set up the boxes" },
  { start: "The campaign has no clipsmith_config", href: (id: string) => `/campaigns/${id}?tab=installningar&del=profil`, label: "Open Advanced (YAML)" },
  { start: "Claude key", href: () => "/settings#anthropic", label: "Add the key in Settings" },
  { start: "Berget key", href: () => "/settings#berget", label: "Add the key in Settings" },
  { start: "Claude-nyckeln", href: () => "/settings#anthropic", label: "Add the key in Settings" },
  { start: "Berget-nyckeln", href: () => "/settings#berget", label: "Add the key in Settings" },
  { start: "ANTHROPIC_API_KEY saknas", href: () => "/settings#anthropic", label: "Add the key in Settings" },
  { start: "BERGET_API_KEY saknas", href: () => "/settings#berget", label: "Add the key in Settings" },
];

/** Länken till stället där problemet rättas, eller inget. */
export function ProblemFix({ id, problem }: { id: string; problem: string }) {
  const fix = FIX.find((f) => problem.startsWith(f.start));
  if (!fix) return null;
  return (
    <>
      {" "}
      {/* relative z-10: ovanför kampanjkortets länk, som täcker hela kortet. */}
      <Link href={fix.href(id)} className="relative z-10 font-medium underline underline-offset-4 hover:text-foreground">
        {fix.label}
      </Link>
    </>
  );
}

/** En rad överst om nycklar saknas, i stället för en gång per kampanj. */
export function MissingKeys({ problems, className }: { problems: string[]; className?: string }) {
  if (problems.length === 0) return null;
  const names = problems.map((p) => (p.startsWith("Berget") || p.startsWith("BERGET") ? "The Berget key" : "The Claude key"));
  return (
    <p className={cn("flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn", className)}>
      <KeyRound className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>
        {names.length === 1 ? `${names[0]} is missing.` : `Two keys are missing: ${names.join(" and ")}.`} Without them the app cannot
        get clips.{" "}
        <Link href={`/settings#${names[0] === "The Berget key" ? "berget" : "anthropic"}`} className="font-medium underline underline-offset-4 hover:text-foreground">
          Add {names.length === 1 ? "it" : "them"} under Settings
        </Link>
      </span>
    </p>
  );
}

export function ReadyBadge({ profile: p }: { profile: CampaignProfile }) {
  const text = readyText(p);
  return text === "Redo" ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-[#174a2a] px-2.5 py-0.5 text-xs text-good">
      <CheckCircle2 className="size-3.5" aria-hidden />
      Redo
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full border border-[#5a4416] px-2.5 py-0.5 text-xs text-warn">
      <AlertTriangle className="size-3.5" aria-hidden />
      {text}
    </span>
  );
}

/** Appens klipplängd och kampanjens gräns, i en rad. */
export function lengthText(p: CampaignProfile): string {
  const app = p.clip_min_s != null && p.clip_max_s != null ? `The app clips ${p.clip_min_s}–${p.clip_max_s} s.` : "The app's clip length is unknown.";
  const allowed = p.allowed_min_s != null && p.allowed_max_s != null ? ` The campaign allows ${p.allowed_min_s}–${p.allowed_max_s} s.` : "";
  return `${app}${allowed}`;
}

/** Källan i ord: Twitch-kanalen eller egna videor. */
export const sourceText = (p: CampaignProfile) =>
  p.source === "local" || (!p.channel && p.source !== "twitch") ? "Uploads" : `twitch.tv/${p.channel}`;

/** Av eller på. Avstängda kampanjer döljs i appen utom här. */
function ActiveSwitch({ profile: p, busy, onChange }: { profile: CampaignProfile; busy: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={p.active}
      aria-label={`Show ${p.streamer} in the app`}
      disabled={busy}
      onClick={onChange}
      className="relative z-10 inline-flex items-center gap-2 rounded-full text-xs text-muted-foreground focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-60"
    >
      <span
        className={cn(
          "flex h-5 w-9 items-center rounded-full border p-0.5 transition-colors",
          p.active ? "border-brand bg-brand" : "border-line bg-[#1c1c22]"
        )}
      >
        <span className={cn("size-3.5 rounded-full bg-white transition-transform", p.active && "translate-x-4")} />
      </span>
      {p.active ? "On" : "Off"}
    </button>
  );
}

const TABS = [
  { id: "mina", label: "My campaigns", href: "/campaigns" },
  { id: "nya", label: "New campaigns", href: "/campaigns?flik=nya" },
] as const;

export function CampaignsClient({ tab = "mina" }: { tab?: "mina" | "nya" }) {
  const [profiles, setProfiles] = useState<CampaignProfile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function toggle(p: CampaignProfile) {
    setBusy(p.name);
    setError(null);
    try {
      const saved = await api.campaign.profile.setActive(p.name, !p.active);
      setProfiles((list) => list?.map((q) => (q.name === p.name ? { ...q, active: saved.active } : q)) ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the campaign");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    api.campaign
      .profiles()
      .then(setProfiles)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the campaigns"));
  }, []);

  // Nycklarna gäller alla kampanjer. Visa dem en gång.
  const keys = profiles?.length ? keyProblems(profiles[0]) : [];

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-3xl font-bold tracking-tight">Campaigns</h1>
        <Link href="/campaigns/new" className={buttonVariants({ className: "h-9" })}>
          <Plus aria-hidden />
          New campaign
        </Link>
      </div>

      <nav aria-label="Campaigns" className="mt-5 mb-6 flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--color-line)]">
        {TABS.map((t) => (
          <Link
            key={t.id}
            href={t.href}
            aria-current={tab === t.id ? "page" : undefined}
            scroll={false}
            className="shrink-0 border-b-2 border-transparent px-3 py-2 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground aria-[current=page]:border-brand aria-[current=page]:text-foreground"
          >
            {t.label}
          </Link>
        ))}
      </nav>

      {tab === "nya" ? (
        <ScoutClient campaigns={profiles} />
      ) : (
        <>
          <p className="max-w-[760px] text-muted-foreground">
            A campaign clips streams from a Twitch channel or uploads. A stream can only become clips for campaigns with
            the same channel. Turn off a campaign to hide it on Home, Projects, Clips and Stats. Nothing is deleted.
          </p>
          <MissingKeys problems={keys} className="mt-4 max-w-[760px]" />
          {error && (
            <p role="alert" className="mt-4 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
              {error}
            </p>
          )}
          {profiles === null && !error && <p className="mt-6 text-sm text-muted-foreground">Loading the campaigns…</p>}
          {profiles?.length === 0 && (
            <p className="mt-6 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
              No campaigns yet.{" "}
              <Link href="/campaigns/new" className="text-brand underline-offset-4 hover:underline">
                Create the first campaign
              </Link>{" "}
              or look under{" "}
              <Link href="/campaigns?flik=nya" className="text-brand underline-offset-4 hover:underline">
                New campaigns
              </Link>
              .
            </p>
          )}
          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            {profiles?.map((p) => (
              <CampaignCard key={p.name} profile={p} busy={busy === p.name} onToggle={() => toggle(p)} />
            ))}
          </div>
        </>
      )}
    </main>
  );
}

function CampaignCard({ profile: p, busy, onToggle }: { profile: CampaignProfile; busy: boolean; onToggle: () => void }) {
  const problems = ownProblems(p);
  return (
    // Hela kortet är en länk via h2-länkens ::after. Knappen ligger ovanpå, inte i länken.
    <div
      data-campaign={p.name}
      className={cn(
        "relative rounded-2xl border border-line bg-panel p-5 transition-colors hover:border-[#3a3a44] has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-brand",
        !p.active && "self-start border-dashed bg-transparent"
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className={cn("min-w-0", !p.active && "opacity-60")}>
          <h2 className="text-lg font-semibold">
            <Link href={`/campaigns/${p.name}`} className="outline-none after:absolute after:inset-0 after:rounded-2xl">
              {p.streamer || p.name}
            </Link>
          </h2>
          <p className="text-sm break-words text-muted-foreground">{p.campaign}</p>
        </div>
        <div className="flex items-center gap-3">
          {p.active && <ReadyBadge profile={p} />}
          <ActiveSwitch profile={p} busy={busy} onChange={onToggle} />
        </div>
      </div>
      {!p.active ? (
        <p className="mt-3 text-sm text-muted-foreground">Turned off. The campaign is hidden on Home, Projects, Clips and Stats.</p>
      ) : (
        <>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">Source</dt>
            <dd>{sourceText(p)}</dd>
            <dt className="text-muted-foreground">Language</dt>
            <dd>{p.language}</dd>
            <dt className="text-muted-foreground">Clip length</dt>
            <dd className="tabular-nums">{lengthText(p)}</dd>
            <dt className="text-muted-foreground">Format</dt>
            <dd>{p.formats.length ? p.formats.map((f) => f.name).join(", ") : "none"}</dd>
          </dl>
          {problems.length > 0 && (
            <ul className="mt-4 space-y-1.5 text-sm">
              {problems.map((problem) => (
                <li key={problem} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <span>
                    {problem}
                    <ProblemFix id={p.name} problem={problem} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
