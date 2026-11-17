"use client";

import Link from "next/link";
import { ExternalLink, EyeOff } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn, usd } from "@/lib/utils";
import type { CampaignProfile, ScoutCampaign } from "@/lib/types";

const dollars = new Intl.NumberFormat("sv-SE", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const views = new Intl.NumberFormat("sv-SE");

const PLATFORM: Record<string, string> = { tiktok: "TikTok", youtube: "YouTube", instagram: "Instagram" };

/** 70 eller mer är en extra bra kampanj, samma gräns som notisen på Macen. */
export function scoutTone(score: number): string {
  if (score >= 70) return "text-good";
  if (score >= 50) return "text-warn";
  return "text-muted-foreground";
}

// Ny: yngre än 48 h. Äldre servrar utan ålder: serverns is_new.
const isNew = (c: ScoutCampaign) => (c.age_hours != null ? c.age_hours < 48 : c.is_new);

function daysLeft(days: number | null): string {
  if (days == null) return "Okänt";
  if (days < 1) return "Under 1 dag";
  const d = Math.floor(days);
  return d === 1 ? "1 dag" : `${d} dagar`;
}

function Tag({ children, tone }: { children: React.ReactNode; tone: string }) {
  return <span className={cn("rounded-md px-1.5 py-0.5 text-[11px] font-semibold", tone)}>{children}</span>;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm tabular-nums">{children}</dd>
    </div>
  );
}

const PLATFORM_ID: Record<string, string> = { tiktok: "tiktok", youtube: "youtube_shorts", instagram: "instagram_reels" };

/** Ny kampanj ifylld med det Bevakning vet: namn, betalning, plattformar och källa. */
export function newCampaignHref(c: ScoutCampaign): string {
  const query: Record<string, string> = { campaign: c.title, scout: c.ref };
  if (c.brand) query.streamer = c.brand;
  // Nämner kampanjen inte Twitch är materialet oftast egna filer, som trailers.
  if (!c.mentions_twitch) query.source = "local";
  if (c.rate_usd != null) query.cpm = String(c.rate_usd);
  if (c.min_views != null) query.min_views = String(c.min_views);
  if (c.max_payout_usd != null) query.max_payout = String(c.max_payout_usd);
  const platforms = c.platforms.map((p) => PLATFORM_ID[p]).filter(Boolean);
  if (platforms.length) query.platforms = platforms.join(",");
  return `/campaigns/new?${new URLSearchParams(query)}`;
}

const plain = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "");

/** Kampanjen som redan är inlagd: samma ref, eller samma namn som i kampanjens profil. */
export function existingCampaign(c: ScoutCampaign, campaigns: CampaignProfile[] | null): CampaignProfile | undefined {
  const title = plain(c.title);
  return campaigns?.find((p) => {
    if (p.scout_ref) return p.scout_ref === c.ref;
    const name = plain(p.campaign);
    return title.length >= 8 && name.length >= 8 && (name.includes(title) || title.includes(name));
  });
}

/** En kampanj i bevakningen: betyget stort till vänster, fakta i mitten, åtgärder till höger. */
export function ScoutCard({ campaign: c, onDismiss, busy, existing }: {
  campaign: ScoutCampaign;
  onDismiss: () => void;
  busy: boolean;
  /** Kampanjen är redan inlagd under Kampanjer. */
  existing?: CampaignProfile;
}) {
  return (
    <article data-ref={c.ref} className="grid gap-4 rounded-2xl border border-line bg-panel p-5 md:grid-cols-[72px_minmax(0,1fr)_190px]">
      <p className="text-center md:pt-0.5" aria-label={`Betyg ${c.score} av 100`}>
        <span data-score className={cn("block text-4xl leading-none font-extrabold tabular-nums", scoutTone(c.score))}>
          {c.score}
        </span>
        <span className="text-[11px] text-muted-foreground">av 100</span>
      </p>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h2 className="text-lg leading-snug font-semibold break-words">{c.title}</h2>
          {isNew(c) && <Tag tone="bg-brand text-white">Ny</Tag>}
          {c.mentions_twitch && <Tag tone="bg-[#2a1f4d] text-[#c4b5fd]">Twitch</Tag>}
          {existing && <Tag tone="bg-good/15 text-good">Finns redan</Tag>}
        </div>
        {c.brand && <p className="text-[13px] text-muted-foreground">{c.brand}</p>}

        <dl className="mt-3.5 grid grid-cols-2 gap-x-5 gap-y-3 sm:grid-cols-3 xl:grid-cols-5">
          <Fact label="Betalning">
            {c.rate_usd != null ? <span className="whitespace-nowrap">{usd.format(c.rate_usd)} per 1 000</span> : "Okänt"}
            {c.net_rate_usd != null && <span className="block text-xs text-muted-foreground">netto {usd.format(c.net_rate_usd)}</span>}
          </Fact>
          <Fact label="Budget kvar">
            {c.budget_left_usd != null ? dollars.format(c.budget_left_usd) : "Okänt"}
            {c.budget_total_usd != null && (
              <span className="text-muted-foreground"> kvar av {dollars.format(c.budget_total_usd)}</span>
            )}
          </Fact>
          <Fact label="Minst för betalning">
            {c.min_views != null ? `${views.format(c.min_views)} visningar` : "Ingen gräns"}
          </Fact>
          <Fact label="Budgeten räcker">{daysLeft(c.drain_days)}</Fact>
          <Fact label="Plattformar">{c.platforms.map((p) => PLATFORM[p] ?? p).join(", ") || "Okänt"}</Fact>
        </dl>

        <details className="group mt-4">
          <summary className="cursor-pointer text-sm text-muted-foreground select-none hover:text-foreground">Varför</summary>
          <ul className="mt-3 space-y-2.5">
            {c.parts.map((p) => (
              <li key={p.key} data-part={p.key} className="grid gap-x-4 gap-y-1 sm:grid-cols-[250px_minmax(0,1fr)]">
                <div>
                  <p className="flex justify-between gap-2 text-sm">
                    {p.label}
                    <span className="text-muted-foreground tabular-nums">
                      {p.points} av {p.max}
                    </span>
                  </p>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-line">
                    <div className="h-full rounded-full bg-brand" style={{ width: `${p.max > 0 ? (p.points / p.max) * 100 : 0}%` }} />
                  </div>
                </div>
                <p className="text-[13px] text-muted-foreground">{p.fact}</p>
              </li>
            ))}
          </ul>
        </details>
      </div>

      <div className="flex flex-col gap-1.5">
        {c.join_url && (
          <a href={c.join_url} target="_blank" rel="noreferrer" className={buttonVariants({ className: "h-9 justify-start" })}>
            <ExternalLink aria-hidden />
            Gå med
          </a>
        )}
        {c.page_url && (
          <a href={c.page_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", className: "h-9 justify-start" })}>
            <ExternalLink aria-hidden />
            Öppna kampanjen
          </a>
        )}
        <Button variant="ghost" className="h-9 justify-start" disabled={busy} onClick={onDismiss}>
          <EyeOff aria-hidden />
          Dölj
        </Button>
        {existing ? (
          <Link href={`/campaigns/${existing.name}`} className="px-2.5 pt-1 text-[13px] text-brand underline-offset-4 hover:underline">
            Öppna {existing.streamer || existing.name}
          </Link>
        ) : (
          <Link href={newCampaignHref(c)} className="px-2.5 pt-1 text-[13px] text-brand underline-offset-4 hover:underline">
            Lägg in kampanjen
          </Link>
        )}
      </div>
    </article>
  );
}

/** Litet kort för startsidan. */
export function ScoutMiniCard({ campaign: c }: { campaign: ScoutCampaign }) {
  return (
    <Link
      href="/campaigns?flik=nya"
      data-ref={c.ref}
      className="flex gap-3.5 rounded-2xl border border-line bg-panel p-4 transition-colors hover:border-[#3a3a44] focus-visible:outline-2 focus-visible:outline-brand"
    >
      <span className={cn("text-3xl leading-none font-extrabold tabular-nums", scoutTone(c.score))} aria-label={`Betyg ${c.score} av 100`}>
        {c.score}
      </span>
      <span className="min-w-0 space-y-0.5">
        <span className="flex items-center gap-1.5">
          <span className="line-clamp-2 text-sm leading-snug font-medium break-words">{c.title}</span>
          {isNew(c) && <Tag tone="shrink-0 bg-brand text-white">Ny</Tag>}
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {[c.brand, c.rate_usd != null && `${usd.format(c.rate_usd)} per 1 000`].filter(Boolean).join(", ")}
        </span>
      </span>
    </Link>
  );
}
