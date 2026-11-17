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
  if (!p.ready) return "Inte redo";
  if (p.problems.length === 0) return "Redo";
  if (p.problems.some((problem) => FAILS_CLIPS.some((start) => problem.startsWith(start)))) {
    return "Går att köra, men klippen blir underkända tills varningarna är rättade";
  }
  return "Går att köra, men rätta varningarna i kampanjens inställningar först";
}

/** Nycklar som saknas. En äldre server har dem bara i problems. */
export const keyProblems = (p: CampaignProfile) =>
  p.key_problems ?? p.problems.filter((problem) => /API_KEY saknas/.test(problem));

/** Kampanjens egna problem, utan nycklarna, som visas en gång överst. */
export const ownProblems = (p: CampaignProfile) => {
  const keys = keyProblems(p);
  return p.problems.filter((problem) => !keys.includes(problem));
};

// Problem som rättas på ett annat ställe. Texten kommer från servern (campaign/profile.py och campaign/api.py).
const FIX = [
  { start: "Overlay-bilden saknas", href: (id: string) => `/campaigns/${id}?tab=installningar&del=overlay`, label: "Ladda upp overlay-bilden" },
  { start: "Facecam- och spelrutan är inte inställd", href: (id: string) => `/campaigns/${id}?tab=installningar&del=facecam`, label: "Ställ in rutorna" },
  { start: "on_screen_text.text är tom", href: (id: string) => `/campaigns/${id}?tab=installningar&del=oversikt#caption`, label: "Skriv texten under Ställ in" },
  { start: "Kampanjen saknar clipsmith_config", href: (id: string) => `/campaigns/${id}?tab=installningar&del=profil`, label: "Öppna Avancerat (YAML)" },
  { start: "Claude-nyckeln", href: () => "/settings#anthropic", label: "Lägg in nyckeln i Inställningar" },
  { start: "Berget-nyckeln", href: () => "/settings#berget", label: "Lägg in nyckeln i Inställningar" },
  { start: "ANTHROPIC_API_KEY saknas", href: () => "/settings#anthropic", label: "Lägg in nyckeln i Inställningar" },
  { start: "BERGET_API_KEY saknas", href: () => "/settings#berget", label: "Lägg in nyckeln i Inställningar" },
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
  const names = problems.map((p) => (p.startsWith("Berget") || p.startsWith("BERGET") ? "Berget-nyckeln" : "Claude-nyckeln"));
  return (
    <p className={cn("flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn", className)}>
      <KeyRound className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>
        {names.length === 1 ? `${names[0]} saknas.` : `Två nycklar saknas: ${names.join(" och ")}.`} Utan dem går det inte
        att hämta klipp.{" "}
        <Link href={`/settings#${names[0] === "Berget-nyckeln" ? "berget" : "anthropic"}`} className="font-medium underline underline-offset-4 hover:text-foreground">
          Lägg in {names.length === 1 ? "den" : "dem"} under Inställningar
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
  const app = p.clip_min_s != null && p.clip_max_s != null ? `Appen klipper ${p.clip_min_s}–${p.clip_max_s} s.` : "Appens klipplängd är okänd.";
  const allowed = p.allowed_min_s != null && p.allowed_max_s != null ? ` Kampanjen tillåter ${p.allowed_min_s}–${p.allowed_max_s} s.` : "";
  return `${app}${allowed}`.replace(/(\d)\.(\d)/g, "$1,$2");
}

/** Källan i ord: Twitch-kanalen eller egna videor. */
export const sourceText = (p: CampaignProfile) =>
  p.source === "local" || (!p.channel && p.source !== "twitch") ? "Egna videor" : `twitch.tv/${p.channel}`;

/** Av eller på. Avstängda kampanjer döljs i appen utom här. */
function ActiveSwitch({ profile: p, busy, onChange }: { profile: CampaignProfile; busy: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={p.active}
      aria-label={`Visa ${p.streamer} i appen`}
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
      {p.active ? "På" : "Av"}
    </button>
  );
}

const TABS = [
  { id: "mina", label: "Mina kampanjer", href: "/campaigns" },
  { id: "nya", label: "Nya kampanjer", href: "/campaigns?flik=nya" },
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
      setError(err instanceof Error ? err.message : "Det gick inte att ändra kampanjen");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    api.campaign
      .profiles()
      .then(setProfiles)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta kampanjerna"));
  }, []);

  // Nycklarna gäller alla kampanjer. Visa dem en gång.
  const keys = profiles?.length ? keyProblems(profiles[0]) : [];

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-3xl font-bold tracking-tight">Kampanjer</h1>
        <Link href="/campaigns/new" className={buttonVariants({ className: "h-9" })}>
          <Plus aria-hidden />
          Ny kampanj
        </Link>
      </div>

      <nav aria-label="Kampanjer" className="mt-5 mb-6 flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--color-line)]">
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
            En kampanj klipper streams från en Twitch-kanal eller egna videor. En stream kan bara bli klipp för kampanjer med
            samma kanal. Stäng av en kampanj för att dölja den på Hem, Projekt, Klippen och Statistik. Inget raderas.
          </p>
          <MissingKeys problems={keys} className="mt-4 max-w-[760px]" />
          {error && (
            <p role="alert" className="mt-4 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
              {error}
            </p>
          )}
          {profiles === null && !error && <p className="mt-6 text-sm text-muted-foreground">Hämtar kampanjerna…</p>}
          {profiles?.length === 0 && (
            <p className="mt-6 rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
              Inga kampanjer än.{" "}
              <Link href="/campaigns/new" className="text-brand underline-offset-4 hover:underline">
                Skapa den första kampanjen
              </Link>{" "}
              eller titta under{" "}
              <Link href="/campaigns?flik=nya" className="text-brand underline-offset-4 hover:underline">
                Nya kampanjer
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
        <p className="mt-3 text-sm text-muted-foreground">Avstängd. Kampanjen döljs på Hem, Projekt, Klippen och Statistik.</p>
      ) : (
        <>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">Källa</dt>
            <dd>{sourceText(p)}</dd>
            <dt className="text-muted-foreground">Språk</dt>
            <dd>{p.language}</dd>
            <dt className="text-muted-foreground">Klipplängd</dt>
            <dd className="tabular-nums">{lengthText(p)}</dd>
            <dt className="text-muted-foreground">Format</dt>
            <dd>{p.formats.length ? p.formats.map((f) => f.name).join(", ") : "inga"}</dd>
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
