"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { CampaignFacecam } from "@/components/CampaignFacecam";
import { CampaignOverlay } from "@/components/CampaignOverlay";
import { CampaignYaml } from "@/components/CampaignYaml";
import { ReadyBadge } from "@/components/CampaignsClient";
import { api } from "@/lib/api";
import { cn, usd } from "@/lib/utils";
import type { CampaignProfileDetail } from "@/lib/types";

const TABS = [
  { id: "oversikt", label: "Översikt" },
  { id: "overlay", label: "Overlay" },
  { id: "facecam", label: "Facecam" },
  { id: "profil", label: "Profil (YAML)" },
] as const;
type Tab = (typeof TABS)[number]["id"];

const num = new Intl.NumberFormat("sv-SE");

function Overview({ profile: p }: { profile: CampaignProfileDetail }) {
  const rows: [string, React.ReactNode][] = [
    ["Kanal", `twitch.tv/${p.channel}`],
    ["Kampanj", p.campaign],
    ["Språk", p.language],
    ["Klipplängd", p.clip_min_s != null && p.clip_max_s != null ? `${p.clip_min_s}–${p.clip_max_s} s` : "okänd"],
    ["Text på skärmen", p.on_screen_text.text ? `${p.on_screen_text.text} (${p.on_screen_text.position === "top" ? "överst" : "nederst"})` : "ingen"],
    ["Overlay", p.overlay.exists ? p.overlay.file : p.overlay.required ? "krävs, saknas" : "krävs inte"],
    ["Betalning", p.payment.cpm_usd != null ? `${usd.format(p.payment.cpm_usd)} per 1 000 visningar` : "okänd"],
    ["Minsta visningar", p.payment.min_views != null ? num.format(p.payment.min_views) : "inga"],
    ["Tak per klipp", p.payment.max_payout_per_clip_usd != null ? usd.format(p.payment.max_payout_per_clip_usd) : "inget"],
    ["Inställningsfil", p.clipsmith_config ?? "saknas"],
  ];
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] content-start gap-x-6 gap-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="space-y-5 text-sm">
        <section>
          <h3 className="mb-1.5 text-[13px] text-muted-foreground">Caption-mall</h3>
          <p className="rounded-[10px] border border-line bg-panel px-3 py-2.5 break-words">{p.caption.template}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {p.caption.hashtags.map((tag) => (
              <span key={tag} className="rounded-full border border-line px-2 py-0.5 text-xs text-muted-foreground">{tag}</span>
            ))}
          </div>
          {p.caption.must_include.length > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">Måste innehålla: {p.caption.must_include.join(", ")}</p>
          )}
        </section>
        <section>
          <h3 className="mb-1.5 text-[13px] text-muted-foreground">Format</h3>
          {p.formats.length === 0 ? (
            <p className="text-muted-foreground">Inga format. Claude väljer fritt.</p>
          ) : (
            <ul className="space-y-1.5">
              {p.formats.map((f) => (
                <li key={f.id}>
                  <span className="font-medium">{f.name}</span>
                  {f.description && <span className="text-muted-foreground">: {f.description}</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

export function CampaignDetailClient({ id, tab: initialTab }: { id: string; tab?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const [profile, setProfile] = useState<CampaignProfileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tab: Tab = TABS.some((t) => t.id === initialTab) ? (initialTab as Tab) : "oversikt";

  useEffect(() => {
    api.campaign.profile
      .get(id)
      .then(setProfile)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta kampanjen"));
  }, [id]);

  if (!profile) {
    return (
      <main className="w-full max-w-[1180px] space-y-3 px-6 py-9 md:px-11">
        {error ? (
          <>
            <p role="alert" className="text-bad">{error}</p>
            <Link href="/campaigns" className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
              Till alla kampanjer
            </Link>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Hämtar kampanjen…</p>
        )}
      </main>
    );
  }

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <Link href="/campaigns" className="text-sm text-muted-foreground hover:text-foreground">
        Kampanjer
      </Link>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-bold tracking-tight">{profile.streamer}</h1>
        <ReadyBadge profile={profile} />
      </div>
      <p className="mt-1.5 text-muted-foreground">{profile.campaign}</p>

      {profile.problems.length > 0 && (
        <ul className="mt-4 max-w-[760px] space-y-1.5 text-sm">
          {profile.problems.map((problem) => (
            <li key={problem} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              {problem}
            </li>
          ))}
        </ul>
      )}

      <div role="tablist" aria-label="Kampanjens delar" className="mt-7 mb-6 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            // Fliken står i adressen, så att länkar kan öppna rätt flik.
            onClick={() => router.replace(`${pathname}?tab=${t.id}`, { scroll: false })}
            className={cn(
              "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground",
              "aria-selected:border-brand aria-selected:text-foreground"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "oversikt" && <Overview profile={profile} />}
      {tab === "overlay" && <CampaignOverlay profile={profile} onUpdate={setProfile} />}
      {tab === "facecam" && <CampaignFacecam id={profile.name} />}
      {tab === "profil" && <CampaignYaml id={profile.name} onUpdate={setProfile} />}
    </main>
  );
}
