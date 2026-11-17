"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { CampaignChat } from "@/components/CampaignChat";
import { CampaignClips } from "@/components/CampaignClips";
import { CampaignExampleClips } from "@/components/CampaignExampleClips";
import { CampaignFacecam } from "@/components/CampaignFacecam";
import { CampaignOverlay } from "@/components/CampaignOverlay";
import { CampaignMaterial } from "@/components/CampaignMaterial";
import { CampaignYaml } from "@/components/CampaignYaml";
import { ProblemFix, ReadyBadge } from "@/components/CampaignsClient";
import { api } from "@/lib/api";
import { cn, usd } from "@/lib/utils";
import type { CampaignProfileDetail, MaterialItem } from "@/lib/types";

const TABS = [
  { id: "skapa", label: "Skapa klipp" },
  { id: "klipp", label: "Klipp" },
  { id: "material", label: "Material" },
  { id: "installningar", label: "Inställningar" },
] as const;
type Tab = (typeof TABS)[number]["id"];

/** Underflikarna i Inställningar: kampanjsidans flikar före kampanjen blev ett nav. */
const PARTS = [
  { id: "oversikt", label: "Översikt" },
  { id: "overlay", label: "Overlay" },
  { id: "facecam", label: "Facecam" },
  { id: "profil", label: "Profil (YAML)" },
  { id: "exempel", label: "Exempelklipp" },
] as const;
type Part = (typeof PARTS)[number]["id"];
const isPart = (value?: string): value is Part => PARTS.some((p) => p.id === value);

const num = new Intl.NumberFormat("sv-SE");
const LEAVE = "Du har ändringar som inte är sparade. Vill du lämna dem?";

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

export function CampaignDetailClient({ id, tab: tabParam, part: partParam, chat }: { id: string; tab?: string; part?: string; chat?: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const [profile, setProfile] = useState<CampaignProfileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Profil (YAML) och Facecam säger till när de har osparade ändringar.
  const [dirty, setDirty] = useState(false);
  // Gamla länkar (?tab=facecam) öppnar Inställningar med den underfliken.
  const tab: Tab = TABS.some((t) => t.id === tabParam) ? (tabParam as Tab) : isPart(tabParam) ? "installningar" : "skapa";
  const part: Part = isPart(tabParam) ? tabParam : isPart(partParam) ? partParam : "oversikt";
  const tabs = useRef<HTMLDivElement>(null);
  // Materialet används av Skapa klipp, Klipp och Material, så det hämtas en gång.
  const [material, setMaterial] = useState<MaterialItem[] | null>(null);
  const [materialError, setMaterialError] = useState<string | null>(null);
  useEffect(() => {
    api.campaign
      .material(id)
      .then(setMaterial)
      .catch((err) => setMaterialError(err instanceof Error ? err.message : "Kunde inte hämta materialet"));
  }, [id]);

  // På en smal skärm kan vald flik ligga utanför den rullbara raden. Rulla fram den.
  useEffect(() => {
    tabs.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab, profile]);

  // Fråga innan osparade ändringar försvinner: när fliken stängs eller laddas om och vid klick på en länk.
  useEffect(() => {
    if (!dirty) return;
    function onUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = "";
    }
    function onClick(e: MouseEvent) {
      const link = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || link.target === "_blank") return;
      if (window.confirm(LEAVE)) {
        window.removeEventListener("beforeunload", onUnload); // användaren har redan svarat
      } else {
        // Före Next.js länkhantering, som lyssnar längre ner i trädet.
        e.preventDefault();
        e.stopPropagation();
      }
    }
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty]);

  function openTab(next: Tab, nextPart: Part = "oversikt") {
    if ((next === tab && (next !== "installningar" || nextPart === part)) || (dirty && !window.confirm(LEAVE))) return;
    router.replace(`${pathname}?tab=${next}${next === "installningar" ? `&del=${nextPart}` : ""}`, { scroll: false });
  }

  function openChat(chatId?: number) {
    router.replace(`${pathname}?tab=skapa${chatId ? `&chatt=${chatId}` : ""}`, { scroll: false });
  }

  function load() {
    api.campaign.profile
      .get(id)
      .then(setProfile)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta kampanjen"));
  }
  useEffect(load, [id]);

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
              <span>
                {problem}
                <ProblemFix id={profile.name} problem={problem} />
              </span>
            </li>
          ))}
        </ul>
      )}

      <div
        ref={tabs}
        role="tablist"
        aria-label="Kampanjens delar"
        // Rullbar på smala skärmar, så att flikarna inte gör sidan bredare. Linjen är en skugga inuti,
        // eftersom en ram under en rullbar rad skulle klippa flikens understrykning.
        className="mt-7 mb-6 flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--color-line)]">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            // Fliken står i adressen, så att länkar kan öppna rätt flik.
            onClick={() => openTab(t.id)}
            className={cn(
              "shrink-0 border-b-2 border-transparent px-3 py-2 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground",
              "aria-selected:border-brand aria-selected:text-foreground"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "skapa" && <CampaignChat profile={profile} material={material} chatId={chat} onOpen={openChat} />}
      {tab === "klipp" && <CampaignClips profile={profile} material={material} />}
      {tab === "material" && <CampaignMaterial material={material} error={materialError} />}
      {tab === "installningar" && (
        <>
          <div role="tablist" aria-label="Inställningar" className="mb-6 flex max-w-full overflow-x-auto rounded-[9px] border border-line text-sm sm:w-fit">
            {PARTS.map((p) => (
              <button
                key={p.id}
                role="tab"
                aria-selected={part === p.id}
                onClick={() => openTab("installningar", p.id)}
                className="shrink-0 px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:bg-panel aria-selected:text-foreground"
              >
                {p.label}
              </button>
            ))}
          </div>
          {part === "oversikt" && <Overview profile={profile} />}
          {part === "overlay" && <CampaignOverlay profile={profile} onUpdate={setProfile} />}
          {part === "facecam" && <CampaignFacecam id={profile.name} onSaved={load} onDirty={setDirty} />}
          {part === "profil" && <CampaignYaml id={profile.name} onUpdate={setProfile} onDirty={setDirty} />}
          {part === "exempel" && <CampaignExampleClips id={profile.name} />}
        </>
      )}
    </main>
  );
}
