"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Clapperboard, Clock, Film, Home, Megaphone, Settings } from "lucide-react";
import { api } from "@/lib/api";
import { cn, SUMMARY_CHANGED, usd } from "@/lib/utils";
import { stepCount, submitDeadline, type ClipStep } from "@/lib/steps";
import type { CampaignSummary } from "@/lib/types";

// Fem punkter. Projekt och Egna videor nås från Hem, Bevakning från Kampanjer; sidorna finns kvar.
const LINKS = [
  { href: "/", label: "Hem", icon: Home, also: ["/projects", "/runs", "/uploads"] },
  { href: "/queue", label: "Klippen", icon: Film, also: [] },
  { href: "/log", label: "Statistik", icon: BarChart3, also: [] },
  { href: "/campaigns", label: "Kampanjer", icon: Megaphone, also: ["/scout"] },
  { href: "/settings", label: "Inställningar", icon: Settings, also: [] },
];

// Stegen i Klippen, med samma färger som statusen i klippraden.
const STEPS: { step: ClipStep; label: string; tone: string }[] = [
  { step: "granska", label: "Att granska", tone: "bg-amber-500/20 text-amber-200" },
  { step: "posta", label: "Att posta", tone: "bg-sky-500/20 text-sky-200" },
  { step: "skicka-in", label: "Skicka in", tone: "bg-violet-500/25 text-violet-200" },
];

const gb = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

export function isActive(pathname: string, href: string, also: string[] = []): boolean {
  if (href === "/") return pathname === "/" || also.some((p) => pathname.startsWith(p));
  return [href, ...also].some((p) => pathname.startsWith(p));
}

function Meter({ label, pct, tone, detail, className }: {
  label: string; pct: number; tone: "good" | "warn" | "bad"; detail?: string; className?: string;
}) {
  return (
    <div className={cn("min-w-0 text-xs text-muted-foreground", className)}>
      {/* Smal skärm: en rad med … Bred skärm: hela texten, radbruten. */}
      <span className="block truncate md:overflow-visible md:whitespace-normal">{label}</span>
      {detail && <span className="mt-0.5 block text-[11px] tabular-nums opacity-80">{detail}</span>}
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
        <div
          className={cn("h-full rounded-full", { good: "bg-good", warn: "bg-warn", bad: "bg-bad" }[tone])}
          style={{ width: `${Math.max(2, Math.min(pct, 100))}%` }}
        />
      </div>
    </div>
  );
}

export function SiteNav() {
  const pathname = usePathname();
  const [summary, setSummary] = useState<CampaignSummary | null>(null);
  // När den första väntande länken måste vara inskickad i Content Rewards, i ms. null: inget väntar.
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Läs om vid varje sidbyte och när ett klipp ändras, så att antal, klocka, budget och disk följer med.
  useEffect(() => {
    const load = () =>
      api.campaign
        .summary()
        .then((s) => {
          setSummary(s);
          setNow(Date.now());
          if (!s.counts.postad) return setDeadline(null);
          return api.campaign.clips.list({ status: "postad" }).then((clips) => setDeadline(submitDeadline(clips)));
        })
        .catch(() => setSummary(null));
    load();
    window.addEventListener(SUMMARY_CHANGED, load);
    return () => window.removeEventListener(SUMMARY_CHANGED, load);
  }, [pathname]);

  // Klockan räknar ner medan något väntar.
  useEffect(() => {
    if (deadline == null) return;
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [deadline]);

  const count = (step: ClipStep) => (summary ? stepCount(summary.counts, step) : 0);
  const minutesLeft = deadline != null ? Math.ceil((deadline - now) / 60_000) : 0;
  const budget = summary?.budget;
  // Berget sparas med syftet transkribering; allt annat är Claude.
  const bergetUsd = budget?.by_purpose?.transkribering?.usd ?? 0;
  const budgetPct = budget && budget.month_cap_usd > 0 ? (budget.month_usd / budget.month_cap_usd) * 100 : 0;
  const budgetTone = budgetPct >= 90 ? "bad" : budgetPct >= 70 ? "warn" : "good";
  const disk = summary?.disk;
  // Stapeln visar ledigt utrymme: nästan tom betyder lite ledigt.
  const diskFreePct = disk && disk.total_gb > 0 ? (disk.free_gb / disk.total_gb) * 100 : 0;
  const diskTone = disk && disk.free_gb < 20 ? "bad" : disk && disk.free_gb < 50 ? "warn" : "good";
  const diskOff = disk?.connected === false;

  const clock = minutesLeft > 0 && (
    <span
      data-submit-clock
      title="Minuter kvar innan Content Rewards slutar ta emot den första väntande länken"
      className="inline-flex items-center gap-0.5 text-[11px] text-violet-200 tabular-nums"
    >
      <Clock className="size-3" aria-hidden />
      {minutesLeft} min
    </span>
  );

  return (
    // Kolumnen går hela sidans höjd och ritar kanten; menyn i den står fast i fönstret.
    <div className="contents md:block md:border-r md:border-line">
      {/* Smal skärm: en rad som rullar i sidled och står fast överst. Bred skärm: kolumnen till vänster. */}
      <aside className="sticky top-0 z-30 border-b border-line bg-background md:flex md:h-screen md:flex-col md:gap-1 md:border-b-0 md:px-3.5 md:py-5">
        <div className="flex items-center gap-1 overflow-x-auto px-2 py-1.5 [scrollbar-width:none] md:block md:overflow-visible md:p-0">
          <Link
            href="/"
            aria-label="Klipp, till Hem"
            className="flex shrink-0 items-center gap-2 px-1.5 text-lg font-bold tracking-tight md:px-2 md:pb-4"
          >
            <Clapperboard className="size-5 text-brand" aria-hidden />
            <span className="hidden md:inline">Klipp</span>
          </Link>
          <nav aria-label="Huvudmeny" className="flex gap-0.5 md:flex-col md:gap-1">
            {LINKS.map(({ href, label, icon: Icon, also }) => (
              <div key={href} className="shrink-0">
                <Link
                  href={href}
                  aria-current={isActive(pathname, href, also) ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
                    "aria-[current=page]:bg-panel aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
                  )}
                >
                  <Icon className="size-4 shrink-0" aria-hidden />
                  <span className="md:flex-1">{label}</span>
                  {/* Smal skärm: siffrorna per steg direkt i punkten. */}
                  {href === "/queue" && (
                    <span className="flex items-center gap-1 md:hidden" aria-hidden>
                      {STEPS.map(({ step, tone }) =>
                        count(step) > 0 ? (
                          <span key={step} className={cn("rounded-full px-1.5 text-xs font-medium tabular-nums", tone)}>
                            {count(step)}
                          </span>
                        ) : null
                      )}
                      {clock}
                    </span>
                  )}
                </Link>
                {/* Bred skärm: ett steg per rad under Klippen, med länk till fliken. */}
                {href === "/queue" && (
                  <ul aria-label="Klippen per steg" className="mt-0.5 mb-1 hidden space-y-0.5 pl-8 md:block">
                    {STEPS.map(({ step, label: stepLabel, tone }) => (
                      <li key={step}>
                        <Link
                          href={`/queue?tab=${step}`}
                          className="flex items-center gap-2 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-panel hover:text-foreground"
                        >
                          <span className="flex-1">{stepLabel}</span>
                          {step === "skicka-in" && clock}
                          {count(step) > 0 && (
                            <span className={cn("rounded-full px-1.5 text-xs font-medium tabular-nums", tone)}>{count(step)}</span>
                          )}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </nav>
        </div>

        {summary && (
          <div className="mt-auto hidden space-y-4 border-t border-line px-2.5 pt-4 md:block">
            {budget && (
              <Meter
                label={`Kostnad denna månad: ${usd.format(budget.month_usd)} av ${usd.format(budget.month_cap_usd)}`}
                detail={`Berget ${usd.format(bergetUsd)}, Claude ${usd.format(budget.month_usd - bergetUsd)}`}
                pct={budgetPct}
                tone={budgetTone}
              />
            )}
            {diskOff ? (
              <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-2.5 py-2 text-xs text-[#fca5a5]">
                {disk?.problem || "SSD:n är inte ansluten."}
              </p>
            ) : disk && (
              <Meter label={`Disk ledigt: ${gb.format(disk.free_gb)} GB`} pct={diskFreePct} tone={diskTone} />
            )}
          </div>
        )}
      </aside>

      {/* Smal skärm: budget och disk på en rad under menyn. Den står inte fast, så den rullar bort. */}
      {summary && (
        <div data-meters-mobile className="flex gap-4 border-b border-line px-4 py-2 md:hidden">
          {budget && (
            <Meter
              className="flex-1"
              label={`Kostnad: ${usd.format(budget.month_usd)} av ${usd.format(budget.month_cap_usd)}`}
              pct={budgetPct}
              tone={budgetTone}
            />
          )}
          {diskOff ? (
            <p role="alert" className="flex-1 text-xs text-[#fca5a5]">
              {disk?.problem || "SSD:n är inte ansluten."}
            </p>
          ) : disk && (
            <Meter className="shrink-0" label={`Disk ledigt: ${gb.format(disk.free_gb)} GB`} pct={diskFreePct} tone={diskTone} />
          )}
        </div>
      )}
    </div>
  );
}
