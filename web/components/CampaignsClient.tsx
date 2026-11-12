"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Plus } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfile } from "@/lib/types";

// Profilens problem som gör varje klipp underkänt i regelkontrollen (campaign/rules.py).
const FAILS_CLIPS = ["Overlay-bilden saknas", "on_screen_text.text är tom"];

/** Vad kampanjens läge betyder för körningen. */
export function readyText(p: CampaignProfile): string {
  if (!p.ready) return "Inte redo";
  if (p.problems.length === 0) return "Redo";
  if (p.problems.some((problem) => FAILS_CLIPS.some((start) => problem.startsWith(start)))) {
    return "Går att köra, men klippen blir underkända tills varningarna är rättade";
  }
  return "Går att köra, men rätta varningarna i kampanjens inställningar först";
}

// Problem som rättas under en flik på kampanjsidan. Texten kommer från servern (campaign/profile.py).
const FIX_TAB = [
  { start: "Overlay-bilden saknas", tab: "overlay", label: "Ladda upp overlay-bilden" },
  { start: "Facecam- och spelrutan är inte inställd", tab: "facecam", label: "Ställ in rutorna" },
];

/** Länken till fliken där problemet rättas, eller inget. */
export function ProblemFix({ id, problem }: { id: string; problem: string }) {
  const fix = FIX_TAB.find((f) => problem.startsWith(f.start));
  if (!fix) return null;
  return (
    <>
      {" "}
      {/* relative z-10: ovanför kampanjkortets länk, som täcker hela kortet. */}
      <Link
        href={`/campaigns/${id}?tab=${fix.tab}`}
        className="relative z-10 font-medium underline underline-offset-4 hover:text-foreground"
      >
        {fix.label}
      </Link>
    </>
  );
}

export function ReadyBadge({ profile: p }: { profile: CampaignProfile }) {
  return p.ready && p.problems.length === 0 ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-[#174a2a] px-2.5 py-0.5 text-xs text-good">
      <CheckCircle2 className="size-3.5" aria-hidden />
      Redo
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full border border-[#5a4416] px-2.5 py-0.5 text-xs text-warn">
      <AlertTriangle className="size-3.5" aria-hidden />
      {readyText(p)}
    </span>
  );
}

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

export function CampaignsClient({ children }: { children?: React.ReactNode }) {
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

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Kampanjer</h1>
          <p className="mt-1.5 text-muted-foreground">
            Varje kampanj hör till en Twitch-kanal. En VOD kan bara bli klipp för kampanjer med samma kanal.
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Stäng av en kampanj för att dölja den på Hem, Projekt, Att posta och Statistik. Inget raderas.
          </p>
        </div>
        <Link href="/campaigns/new" className={buttonVariants({ className: "h-9" })}>
          <Plus aria-hidden />
          Ny kampanj
        </Link>
      </div>
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {profiles === null && !error && <p className="text-sm text-muted-foreground">Hämtar kampanjerna…</p>}
      {profiles?.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga kampanjer än.{" "}
          <Link href="/campaigns/new" className="text-brand underline-offset-4 hover:underline">
            Skapa den första kampanjen
          </Link>
          .
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {profiles?.map((p) => (
          // Hela kortet är en länk via h2-länkens ::after. Knappen ligger ovanpå, inte i länken.
          <div
            key={p.name}
            className={cn(
              "relative rounded-2xl border border-line bg-panel p-5 transition-colors hover:border-[#3a3a44] has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-brand",
              !p.active && "self-start border-dashed bg-transparent"
            )}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className={cn("min-w-0", !p.active && "opacity-60")}>
                <h2 className="text-lg font-semibold">
                  <Link href={`/campaigns/${p.name}`} className="outline-none after:absolute after:inset-0 after:rounded-2xl">
                    {p.streamer}
                  </Link>
                </h2>
                <p className="text-sm break-words text-muted-foreground">{p.campaign}</p>
              </div>
              <div className="flex items-center gap-3">
                {p.active && <ReadyBadge profile={p} />}
                <ActiveSwitch profile={p} busy={busy === p.name} onChange={() => toggle(p)} />
              </div>
            </div>
            {!p.active ? (
              <p className="mt-3 text-sm text-muted-foreground">Avstängd. Kampanjen döljs på Hem, Projekt, Att posta och Statistik.</p>
            ) : (
              <>
                <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Kanal</dt>
                  <dd>twitch.tv/{p.channel}</dd>
                  <dt className="text-muted-foreground">Språk</dt>
                  <dd>{p.language}</dd>
                  <dt className="text-muted-foreground">Klipplängd</dt>
                  <dd className="tabular-nums">
                    {p.clip_min_s != null && p.clip_max_s != null ? `${p.clip_min_s}–${p.clip_max_s} s` : "okänd"}
                  </dd>
                  <dt className="text-muted-foreground">Format</dt>
                  <dd>{p.formats.length ? p.formats.map((f) => f.name).join(", ") : "inga"}</dd>
                </dl>
                {p.problems.length > 0 && (
                  <ul className="mt-4 space-y-1.5 text-sm">
                    {p.problems.map((problem) => (
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
        ))}
      </div>
      {children}
    </main>
  );
}
