"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Clapperboard, FolderOpen, Home, Megaphone, Send, Settings } from "lucide-react";
import { api } from "@/lib/api";
import { cn, SUMMARY_CHANGED, usd } from "@/lib/utils";
import type { CampaignSummary } from "@/lib/types";

const LINKS = [
  { href: "/", label: "Hem", icon: Home },
  { href: "/projects", label: "Projekt", icon: FolderOpen },
  { href: "/queue", label: "Att posta", icon: Send },
  { href: "/log", label: "Statistik", icon: BarChart3 },
  { href: "/campaigns", label: "Kampanjer", icon: Megaphone },
  { href: "/settings", label: "Inställningar", icon: Settings },
];

const gb = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/projects") return pathname.startsWith("/projects") || pathname.startsWith("/runs");
  return pathname.startsWith(href);
}

function Meter({ label, pct, tone, detail }: {
  label: string; pct: number; tone: "good" | "warn" | "bad"; detail?: string;
}) {
  return (
    <div className="px-2.5 text-xs text-muted-foreground">
      {label}
      {detail && <div className="mt-0.5 text-[11px] tabular-nums opacity-80">{detail}</div>}
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

  // Läs om vid varje sidbyte och när ett klipp ändras, så att antal, budget och disk följer med.
  useEffect(() => {
    const load = () => api.campaign.summary().then(setSummary).catch(() => setSummary(null));
    load();
    window.addEventListener(SUMMARY_CHANGED, load);
    return () => window.removeEventListener(SUMMARY_CHANGED, load);
  }, [pathname]);

  const toPost = summary?.counts.godkand ?? 0;
  const budget = summary?.budget;
  // Berget sparas med syftet transkribering; allt annat är Claude.
  const bergetUsd = budget?.by_purpose?.transkribering?.usd ?? 0;
  const budgetPct = budget && budget.month_cap_usd > 0 ? (budget.month_usd / budget.month_cap_usd) * 100 : 0;
  const disk = summary?.disk;
  // Stapeln visar ledigt utrymme: nästan tom betyder lite ledigt.
  const diskFreePct = disk && disk.total_gb > 0 ? (disk.free_gb / disk.total_gb) * 100 : 0;

  return (
    // Kolumnen går hela sidans höjd och ritar kanten; menyn i den står fast i fönstret.
    <div className="contents md:block md:border-r md:border-line">
      <aside className="sticky top-0 z-30 flex h-auto flex-col gap-1 border-b border-line bg-background px-3.5 py-4 md:h-screen md:border-b-0 md:py-5">
        <Link href="/" className="flex items-center gap-2 px-2 pb-4 text-lg font-bold tracking-tight">
          <Clapperboard className="size-5 text-brand" aria-hidden />
          Klipp
        </Link>
        <nav aria-label="Huvudmeny" className="flex flex-wrap gap-1 md:flex-col">
          {LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={isActive(pathname, href) ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground",
                "aria-[current=page]:bg-panel aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
              )}
            >
              <Icon className="size-4" aria-hidden />
              <span className="flex-1">{label}</span>
              {href === "/queue" && toPost > 0 && (
                <span className="rounded-full bg-brand px-1.5 text-xs font-medium text-white tabular-nums">
                  {toPost}
                </span>
              )}
            </Link>
          ))}
        </nav>

        {summary && (
          <div className="mt-auto hidden space-y-4 border-t border-line pt-4 md:block">
            {budget && (
              <Meter
                label={`Kostnad denna månad: ${usd.format(budget.month_usd)} av ${usd.format(budget.month_cap_usd)}`}
                detail={`Berget ${usd.format(bergetUsd)}, Claude ${usd.format(budget.month_usd - bergetUsd)}`}
                pct={budgetPct}
                tone={budgetPct >= 90 ? "bad" : budgetPct >= 70 ? "warn" : "good"}
              />
            )}
            {disk?.connected === false ? (
              <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-2.5 py-2 text-xs text-[#fca5a5]">
                {disk.problem || "SSD:n är inte ansluten."}
              </p>
            ) : disk && (
              <Meter
                label={`Disk ledigt: ${gb.format(disk.free_gb)} GB`}
                pct={diskFreePct}
                tone={disk.free_gb < 20 ? "bad" : disk.free_gb < 50 ? "warn" : "good"}
              />
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
