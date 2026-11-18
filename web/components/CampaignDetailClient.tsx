"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { CampaignChat } from "@/components/CampaignChat";
import { CampaignChecklist } from "@/components/CampaignChecklist";
import { checklist } from "@/lib/checklist";
import { CampaignClips } from "@/components/CampaignClips";
import { CampaignExampleClips } from "@/components/CampaignExampleClips";
import { CampaignFacecam } from "@/components/CampaignFacecam";
import { CampaignOverlay } from "@/components/CampaignOverlay";
import { CampaignMaterial } from "@/components/CampaignMaterial";
import { CampaignRules } from "@/components/CampaignRules";
import { CampaignSettingsForm } from "@/components/CampaignSettingsForm";
import { CampaignYaml } from "@/components/CampaignYaml";
import { LiveWatch } from "@/components/LiveWatch";
import { MissingKeys, ProblemFix, ReadyBadge, keyProblems, lengthText, ownProblems, sourceText } from "@/components/CampaignsClient";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfileDetail, ConnectedAccounts, MaterialItem } from "@/lib/types";

const TABS = [
  { id: "skapa", label: "Skapa klipp" },
  { id: "klipp", label: "Klipp" },
  { id: "exempel", label: "Exempelklipp" },
  { id: "material", label: "Material" },
  { id: "installningar", label: "Ställ in" },
] as const;
type Tab = (typeof TABS)[number]["id"];

/** Underflikarna i Ställ in. id:t står kvar från de gamla flikarna, så att gamla länkar fungerar. */
const PARTS = [
  { id: "oversikt", label: "Översikt" },
  { id: "overlay", label: "Overlay" },
  { id: "facecam", label: "Facecam" },
  { id: "profil", label: "Avancerat (YAML)" },
] as const;
type Part = (typeof PARTS)[number]["id"];
const isPart = (value?: string): value is Part => PARTS.some((p) => p.id === value);
const isTab = (value?: string): value is Tab => TABS.some((t) => t.id === value);

// Problem som checklistan redan visar.
const IN_CHECKLIST = ["Overlay-bilden saknas", "Facecam- och spelrutan"];

const LEAVE = "Du har ändringar som inte är sparade. Vill du lämna dem?";

/** Kampanjen behöver ställas in innan klippen blir godkända: då öppnar sidan Ställ in. */
const needsSetup = (p: CampaignProfileDetail) => !p.links.content_rewards || ownProblems(p).length > 0;

export function CampaignDetailClient({ id, tab: tabParam, part: partParam, chat, created, twitchWarning }: {
  id: string;
  tab?: string;
  part?: string;
  chat?: number;
  /** Kampanjen skapades nyss (?skapad=1). */
  created?: boolean;
  /** Twitch svarade inte när kampanjen skapades (?varning=twitch). */
  twitchWarning?: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [profile, setProfile] = useState<CampaignProfileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Ställ in, Avancerat (YAML) och Facecam säger till när de har osparade ändringar.
  const [dirty, setDirty] = useState(false);
  const tabs = useRef<HTMLDivElement>(null);
  // Materialet används av Skapa klipp, Klipp och Material, så det hämtas en gång.
  const [material, setMaterial] = useState<MaterialItem[] | null>(null);
  const [materialError, setMaterialError] = useState<string | null>(null);
  // Kontona används av checklistan och formuläret i Ställ in.
  const [accounts, setAccounts] = useState<ConnectedAccounts | null>(null);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  useEffect(() => {
    api.campaign
      .material(id)
      .then(setMaterial)
      .catch((err) => setMaterialError(err instanceof Error ? err.message : "Kunde inte hämta materialet"));
    api.campaign
      .accounts()
      .then(setAccounts)
      .catch((err) => setAccountsError(err instanceof Error ? err.message : "Kunde inte läsa kontona"));
  }, [id]);

  // Gamla länkar (?tab=facecam, ?del=exempel) öppnar rätt flik. Utan flik: Ställ in tills kampanjen är inställd.
  const tab: Tab = isTab(tabParam)
    ? tabParam
    : isPart(tabParam)
      ? "installningar"
      : partParam === "exempel"
        ? "exempel"
        : profile && needsSetup(profile)
          ? "installningar"
          : "skapa";
  const part: Part = isPart(tabParam) ? tabParam : isPart(partParam) ? partParam : "oversikt";
  const camera = profile?.facecam?.camera ?? true;

  // På en smal skärm kan vald flik ligga utanför den rullbara raden. Rulla fram den.
  useEffect(() => {
    tabs.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [tab, profile]);

  // Länkar med #ankare (till exempel #lankar från checklistan) rullar fram fältet när delen har ritats.
  useEffect(() => {
    if (!profile || !window.location.hash) return;
    const target = document.getElementById(window.location.hash.slice(1));
    target?.scrollIntoView({ block: "start" });
  }, [profile, tab, part]);

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
      // En länk till ett fält på samma sida lämnar inget.
      if (link.pathname === window.location.pathname && link.search === window.location.search && link.hash) return;
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

  const problems = ownProblems(profile).filter((p) => !IN_CHECKLIST.some((start) => p.startsWith(start)));
  const parts = PARTS.filter((p) => p.id !== "facecam" || camera);

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
      {/* Kampanjens egen sida på Content Rewards, ett klick bort från alla flikar. */}
      <p className="mt-1.5 text-sm">
        {profile.links.content_rewards ? (
          <a
            href={profile.links.content_rewards}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 font-medium text-foreground underline underline-offset-4 hover:text-brand"
          >
            Öppna kampanjen på Content Rewards
            <ExternalLink className="size-3.5 shrink-0" aria-hidden />
          </a>
        ) : (
          <Link
            href={`/campaigns/${profile.name}?tab=installningar&del=oversikt#lankar`}
            className="text-muted-foreground underline underline-offset-4 hover:text-foreground"
          >
            Lägg in länken till kampanjen
          </Link>
        )}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        Källa: {sourceText(profile)}. {lengthText(profile)}
      </p>

      <MissingKeys problems={keyProblems(profile)} className="mt-4 max-w-[760px]" />
      {twitchWarning && profile.channel && (
        <p role="status" className="mt-4 flex max-w-[760px] gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            Twitch svarade inte när kampanjen skapades. Kontrollera att kanalen finns:{" "}
            <a href={`https://www.twitch.tv/${profile.channel}`} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-4">
              twitch.tv/{profile.channel}
            </a>
          </span>
        </p>
      )}
      <CampaignChecklist items={checklist(profile, accounts)} created={created} />

      {problems.length > 0 && (
        <ul className="mt-4 max-w-[760px] space-y-1.5 text-sm">
          {problems.map((problem) => (
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
      <LiveWatch profile={profile.name} />

      <div
        ref={tabs}
        role="tablist"
        aria-label="Kampanjens delar"
        // På smal skärm bryts raden, så att ingen flik göms. Varje flik har då sin egen linje.
        // På bred skärm är linjen en skugga inuti raden.
        className="mt-7 mb-6 flex flex-wrap sm:flex-nowrap sm:gap-1 sm:overflow-x-auto sm:shadow-[inset_0_-1px_0_var(--color-line)]"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            // Fliken står i adressen, så att länkar kan öppna rätt flik.
            onClick={() => openTab(t.id)}
            className={cn(
              "shrink-0 border-b-2 border-line px-3 py-2 text-sm whitespace-nowrap text-muted-foreground hover:text-foreground sm:border-transparent",
              "aria-selected:border-brand aria-selected:text-foreground"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "skapa" && <CampaignChat profile={profile} material={material} chatId={chat} onOpen={openChat} />}
      {tab === "klipp" && <CampaignClips profile={profile} material={material} />}
      {tab === "exempel" && <CampaignExampleClips id={profile.name} link={profile.links.content_rewards} />}
      {tab === "material" && <CampaignMaterial profile={profile} material={material} error={materialError} />}
      {tab === "installningar" && (
        <>
          {/* Två rader på smal skärm, så att ingen del göms. */}
          <div role="tablist" aria-label="Ställ in" className="mb-6 flex flex-wrap gap-1.5 text-sm">
            {parts.map((p) => (
              <button
                key={p.id}
                role="tab"
                aria-selected={part === p.id}
                onClick={() => openTab("installningar", p.id)}
                className="rounded-[9px] border border-line px-3 py-1.5 whitespace-nowrap text-muted-foreground hover:text-foreground aria-selected:border-[#3a3a44] aria-selected:bg-panel aria-selected:text-foreground"
              >
                {p.label}
              </button>
            ))}
          </div>
          {part === "oversikt" && (
            // Reglerna står bredvid formuläret bara när båda får plats. Annars överst, så att formulärets fält är breda.
            <div className="grid gap-6 2xl:grid-cols-[minmax(0,1fr)_340px] 2xl:gap-8">
              <div className="max-w-[760px] 2xl:sticky 2xl:top-20 2xl:order-last 2xl:max-w-none 2xl:self-start">
                <CampaignRules profile={profile} />
              </div>
              <CampaignSettingsForm
                profile={profile}
                accounts={accounts}
                accountsError={accountsError}
                onSaved={setProfile}
                onDirty={setDirty}
              />
            </div>
          )}
          {part === "overlay" && <CampaignOverlay profile={profile} onUpdate={setProfile} />}
          {part === "facecam" &&
            (camera ? (
              <CampaignFacecam id={profile.name} channel={profile.channel} onSaved={load} onDirty={setDirty} />
            ) : (
              <p className="text-sm text-muted-foreground">Kampanjen har ingen kamera. Klippen visar bara bilden från videon.</p>
            ))}
          {part === "profil" && <CampaignYaml id={profile.name} onUpdate={setProfile} onDirty={setDirty} />}
        </>
      )}
    </main>
  );
}
