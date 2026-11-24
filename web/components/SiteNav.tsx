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

const gb = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

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

/** Menyns data: antal per status, budget och disk, och minuter kvar för Skicka in. */
function useNavSummary(pathname: string) {
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

  return { summary, minutesLeft: deadline != null ? Math.ceil((deadline - now) / 60_000) : 0 };
}

type Tone = "good" | "warn" | "bad";

const toneFor = (value: number, bad: (v: number) => boolean, warn: (v: number) => boolean): Tone =>
  bad(value) ? "bad" : warn(value) ? "warn" : "good";

/** Budgeten som text, stapel och färg. Berget sparas med syftet transkribering; allt annat är Claude. */
function budgetMeter(budget: CampaignSummary["budget"]) {
  if (!budget) return null;
  const bergetUsd = budget.by_purpose?.transkribering?.usd ?? 0;
  const pct = budget.month_cap_usd > 0 ? (budget.month_usd / budget.month_cap_usd) * 100 : 0;
  return {
    spent: `${usd.format(budget.month_usd)} av ${usd.format(budget.month_cap_usd)}`,
    split: `Berget ${usd.format(bergetUsd)}, Claude ${usd.format(budget.month_usd - bergetUsd)}`,
    pct,
    tone: toneFor(pct, (p) => p >= 90, (p) => p >= 70),
  };
}

/** Disken som text, stapel och färg. Stapeln visar ledigt utrymme: nästan tom betyder lite ledigt. */
function diskMeter(disk: CampaignSummary["disk"]) {
  if (!disk) return null;
  return {
    off: disk.connected === false,
    problem: disk.problem || "SSD:n är inte ansluten.",
    label: `Disk ledigt: ${gb.format(disk.free_gb)} GB`,
    pct: disk.total_gb > 0 ? (disk.free_gb / disk.total_gb) * 100 : 0,
    tone: toneFor(disk.free_gb, (g) => g < 20, (g) => g < 50),
  };
}

// Bred skärm: längst ner i kolumnen, med hela texten. Smal skärm: en rad under menyn.
const METER_LAYOUT = {
  wide: { prefix: "Kostnad denna månad: ", split: true, budget: undefined, disk: undefined, alert: "rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-2.5 py-2" },
  compact: { prefix: "Kostnad: ", split: false, budget: "flex-1", disk: "shrink-0", alert: "flex-1" },
};

/** Budget och disk. Smal skärm: raden under menyn står inte fast. */
function Meters({ summary, compact }: { summary: CampaignSummary; compact?: boolean }) {
  const budget = budgetMeter(summary.budget);
  const disk = diskMeter(summary.disk);
  const layout = METER_LAYOUT[compact ? "compact" : "wide"];
  return (
    <>
      {budget && (
        <Meter
          className={layout.budget}
          label={layout.prefix + budget.spent}
          detail={layout.split ? budget.split : undefined}
          pct={budget.pct}
          tone={budget.tone}
        />
      )}
      {disk?.off && (
        <p role="alert" className={cn("text-xs text-[#fca5a5]", layout.alert)}>
          {disk.problem}
        </p>
      )}
      {disk && !disk.off && <Meter className={layout.disk} label={disk.label} pct={disk.pct} tone={disk.tone} />}
    </>
  );
}

function CountBadge({ n, tone }: { n: number; tone: string }) {
  if (n <= 0) return null;
  return <span className={cn("rounded-full px-1.5 text-xs font-medium tabular-nums", tone)}>{n}</span>;
}

function SubmitClock({ minutesLeft }: { minutesLeft: number }) {
  if (minutesLeft <= 0) return null;
  return (
    <span
      data-submit-clock
      title="Minuter kvar innan Content Rewards slutar ta emot den första väntande länken"
      className="inline-flex items-center gap-0.5 text-[11px] text-violet-200 tabular-nums"
    >
      <Clock className="size-3" aria-hidden />
      {minutesLeft} min
    </span>
  );
}

/** Siffrorna per steg i Klippen. Smal skärm: i punkten. Bred skärm: ett steg per rad, med länk till fliken. */
function StepCounts({ count, minutesLeft, list }: { count: (step: ClipStep) => number; minutesLeft: number; list?: boolean }) {
  if (!list) {
    return (
      <span className="flex items-center gap-1 md:hidden" aria-hidden>
        {STEPS.map(({ step, tone }) => (
          <CountBadge key={step} n={count(step)} tone={tone} />
        ))}
        <SubmitClock minutesLeft={minutesLeft} />
      </span>
    );
  }
  return (
    <ul aria-label="Klippen per steg" className="mt-0.5 mb-1 hidden space-y-0.5 pl-8 md:block">
      {STEPS.map(({ step, label, tone }) => (
        <li key={step}>
          <Link
            href={`/queue?tab=${step}`}
            className="flex items-center gap-2 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-panel hover:text-foreground"
          >
            <span className="flex-1">{label}</span>
            {step === "skicka-in" && <SubmitClock minutesLeft={minutesLeft} />}
            <CountBadge n={count(step)} tone={tone} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function SiteNav() {
  const pathname = usePathname();
  const { summary, minutesLeft } = useNavSummary(pathname);
  const count = (step: ClipStep) => (summary ? stepCount(summary.counts, step) : 0);

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
                  {href === "/queue" && <StepCounts count={count} minutesLeft={minutesLeft} />}
                </Link>
                {href === "/queue" && <StepCounts count={count} minutesLeft={minutesLeft} list />}
              </div>
            ))}
          </nav>
        </div>

        {summary && (
          <div className="mt-auto hidden space-y-4 border-t border-line px-2.5 pt-4 md:block">
            <Meters summary={summary} />
          </div>
        )}
      </aside>

      {/* Smal skärm: budget och disk på en rad under menyn. Den står inte fast, så den rullar bort. */}
      {summary && (
        <div data-meters-mobile className="flex gap-4 border-b border-line px-4 py-2 md:hidden">
          <Meters summary={summary} compact />
        </div>
      )}
    </div>
  );
}
