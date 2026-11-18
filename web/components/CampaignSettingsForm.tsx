"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Chips,
  EXAMPLE_TITLE,
  Field,
  FormatList,
  PLATFORM_CHOICES,
  Section,
  Toggle,
  contentRewardsProblem,
  field,
  httpsProblem,
  numberText,
  parseNumber,
  previewCaption,
  type FormatRow,
} from "@/components/CampaignFields";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  AccountPlatform,
  CampaignProfileDetail,
  CampaignSettingsBody,
  ConnectedAccounts,
  HookStyle,
  Platform,
  TextPosition,
} from "@/lib/types";

const ACCOUNT_PLATFORMS: { id: AccountPlatform; label: string; kind: string }[] = [
  { id: "tiktok", label: "TikTok", kind: "konton" },
  { id: "youtube_shorts", label: "YouTube Shorts", kind: "kanaler" },
];

/** Formulärets värden som text, så att Robin kan skriva fritt. Tal tolkas först när han sparar. */
function initial(p: CampaignProfileDetail) {
  return {
    streamer: p.streamer,
    campaign: p.campaign,
    language: p.language,
    contentRewards: p.links.content_rewards ?? "",
    analytics: p.links.analytics_form ?? "",
    template: p.caption.template,
    mustInclude: p.caption.must_include,
    hashtags: p.caption.hashtags,
    onScreenText: p.on_screen_text.text,
    textPosition: p.on_screen_text.position,
    hookStyle: p.on_screen_text.hook_style ?? ("sans" as HookStyle),
    allowedMin: numberText(p.allowed_min_s),
    allowedMax: numberText(p.allowed_max_s),
    clipMin: numberText(p.clip_min_s),
    clipMax: numberText(p.clip_max_s),
    cpm: numberText(p.payment.cpm_usd),
    minViews: numberText(p.payment.min_views),
    maxPayout: numberText(p.payment.max_payout_per_clip_usd),
    formats: p.formats.map((f) => ({ id: f.id, name: f.name, description: f.description })) as FormatRow[],
    platforms: (p.platforms ?? ["tiktok", "youtube_shorts", "instagram_reels"]) as Platform[],
    accounts: {
      tiktok: (p.accounts?.tiktok ?? []).map((a) => a.id),
      youtube_shorts: (p.accounts?.youtube_shorts ?? []).map((a) => a.id),
    } as Record<AccountPlatform, string[]>,
  };
}

type Values = ReturnType<typeof initial>;

/** Ställ in, Översikt: kampanjens fält som ett formulär. Det formuläret inte visar står kvar i filen. */
export function CampaignSettingsForm({ profile, accounts, accountsError, onSaved, onDirty }: {
  profile: CampaignProfileDetail;
  /** De kopplade kontona. Kampanjsidan läser dem en gång, för formuläret och checklistan. */
  accounts: ConnectedAccounts | null;
  accountsError: string | null;
  onSaved: (profile: CampaignProfileDetail) => void;
  onDirty?: (dirty: boolean) => void;
}) {
  const start = useMemo(() => initial(profile), [profile]);
  const [v, setValues] = useState<Values>(start);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  // Felen visas vid fältet när Robin har lämnat det, och alla när han trycker Spara.
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const leave = (key: string) => () => setTouched((prev) => new Set(prev).add(key));
  const set = <K extends keyof Values>(key: K, value: Values[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  // Ny profil från servern (efter Spara eller YAML): börja om från den.
  useEffect(() => setValues(start), [start]);

  const dirty = JSON.stringify(v) !== JSON.stringify(start);
  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  const n = {
    allowedMin: parseNumber(v.allowedMin),
    allowedMax: parseNumber(v.allowedMax),
    clipMin: parseNumber(v.clipMin),
    clipMax: parseNumber(v.clipMax),
    cpm: parseNumber(v.cpm),
    minViews: parseNumber(v.minViews),
    maxPayout: parseNumber(v.maxPayout),
  };
  const whole = (x: number | null) => x != null && Number.isInteger(x);
  const problems: Record<string, string | null> = {
    streamer: v.streamer.trim() ? null : "Skriv streamerns eller varumärkets namn.",
    contentRewards: contentRewardsProblem(v.contentRewards),
    analytics: httpsProblem(v.analytics),
    template: v.template.includes("{title}") ? null : "Mallen måste innehålla {title}, där klippets rubrik hamnar.",
    allowed:
      !whole(n.allowedMin) || !whole(n.allowedMax)
        ? "Skriv hela sekunder, till exempel 7 och 60."
        : n.allowedMin! < 1 || n.allowedMin! >= n.allowedMax!
          ? "Kortast måste vara minst 1 och mindre än längst."
          : null,
    clip:
      v.clipMin === "" && v.clipMax === ""
        ? null
        : !whole(n.clipMin) || !whole(n.clipMax)
          ? "Skriv hela sekunder, till exempel 15 och 45."
          : n.clipMin! < 1 || n.clipMin! >= n.clipMax!
            ? "Kortast måste vara minst 1 och mindre än längst."
            : whole(n.allowedMin) && whole(n.allowedMax) && (n.clipMin! < n.allowedMin! || n.clipMax! > n.allowedMax!)
              ? `Appen ska klippa inom det kampanjen tillåter: ${v.allowedMin}–${v.allowedMax} s.`
              : null,
    cpm: n.cpm != null && Number.isNaN(n.cpm) ? "Skriv ett tal, till exempel 1,5." : null,
    minViews: n.minViews != null && !whole(n.minViews) ? "Skriv ett helt tal, till exempel 2000." : null,
    maxPayout: n.maxPayout != null && Number.isNaN(n.maxPayout) ? "Skriv ett tal, till exempel 150." : null,
    formats: v.formats.some((f) => !f.name.trim()) ? "Ge varje format ett namn, eller ta bort raden." : null,
    platforms: v.platforms.length ? null : "Välj minst en plattform.",
  };
  const shown = (key: string) => (submitted || touched.has(key) ? problems[key] : null);
  const bad = Object.values(problems).filter(Boolean);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (bad.length) {
      requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-settings] [role=alert]")?.scrollIntoView({ block: "center" }));
      return;
    }
    setBusy(true);
    setError(null);
    const body: CampaignSettingsBody = {
      streamer: v.streamer.trim(),
      campaign: v.campaign.trim(),
      language: v.language.trim(),
      links: { content_rewards: v.contentRewards.trim() || null, analytics_form: v.analytics.trim() || null },
      caption_template: v.template,
      must_include: v.mustInclude,
      hashtags: v.hashtags,
      on_screen_text: v.onScreenText.trim(),
      text_position: v.textPosition,
      hook_style: v.hookStyle,
      allowed_min_s: n.allowedMin!,
      allowed_max_s: n.allowedMax!,
      ...(v.clipMin !== "" ? { clip_min_s: n.clipMin!, clip_max_s: n.clipMax! } : {}),
      cpm_usd: n.cpm,
      min_views: n.minViews,
      max_payout_per_clip_usd: n.maxPayout,
      formats: v.formats.map((f) => ({ ...(f.id ? { id: f.id } : {}), name: f.name.trim(), description: f.description.trim() })),
      platforms: v.platforms,
      accounts: v.accounts,
    };
    try {
      onSaved(await api.campaign.saveSettings(profile.name, body));
      setSaved(true);
      setSubmitted(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara kampanjen");
    } finally {
      setBusy(false);
    }
  }

  function togglePlatform(id: Platform, on: boolean) {
    // Behåll ordningen i filen, så att Spara inte skriver om raden i onödan.
    set("platforms", on ? [...v.platforms, id] : v.platforms.filter((x) => x !== id));
  }

  function toggleAccount(platform: AccountPlatform, id: string, on: boolean) {
    const list = v.accounts[platform];
    set("accounts", { ...v.accounts, [platform]: on ? [...list, id] : list.filter((x) => x !== id) });
  }

  return (
    <form onSubmit={save} noValidate data-settings className="min-w-0">
      <Section title="Kampanjen">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Streamerns eller varumärkets namn" error={shown("streamer")}>
            <input value={v.streamer} onChange={(e) => set("streamer", e.target.value)} onBlur={leave("streamer")} aria-invalid={!!shown("streamer") || undefined} className={field} />
          </Field>
          <Field label="Kampanjens namn" hint="Som det står på kampanjsidan.">
            <input value={v.campaign} onChange={(e) => set("campaign", e.target.value)} className={field} />
          </Field>
          <Field label="Språk i videorna" hint="Språkkod, till exempel en eller sv." className="sm:max-w-40">
            <input value={v.language} onChange={(e) => set("language", e.target.value)} className={field} />
          </Field>
        </div>
      </Section>

      <Section title="Länkar" id="lankar">
        <Field
          label="Länk till kampanjen i Content Rewards"
          hint="Där du skickar in klippen. Exempelklipp och Hjälp att skicka in använder den."
          error={shown("contentRewards")}
        >
          <input
            value={v.contentRewards}
            onChange={(e) => set("contentRewards", e.target.value)}
            onBlur={leave("contentRewards")} aria-invalid={!!shown("contentRewards") || undefined}
            inputMode="url"
            placeholder="https://contentrewards.com/c/campaigns/…"
            className={field}
          />
        </Field>
        <Field label="Analytics-formuläret" hint="Formuläret för klipp över minsta visningar." error={shown("analytics")}>
          <input
            value={v.analytics}
            onChange={(e) => set("analytics", e.target.value)}
            onBlur={leave("analytics")} aria-invalid={!!shown("analytics") || undefined}
            inputMode="url"
            placeholder="https://forms.gle/…"
            className={field}
          />
        </Field>
      </Section>

      <Section title="Caption" id="caption">
        <Field label="Mall" hint="{title} byts mot klippets rubrik." error={shown("template")}>
          <input value={v.template} onChange={(e) => set("template", e.target.value)} onBlur={leave("template")} aria-invalid={!!shown("template") || undefined} className={field} />
        </Field>
        <Chips label="Måste innehålla" values={v.mustInclude} onChange={(x) => set("mustInclude", x)} placeholder="Skriv och tryck Enter" />
        <Chips label="Hashtags" values={v.hashtags} onChange={(x) => set("hashtags", x)} prefix="#" placeholder="#hashtag" />
        {profile.caption.tag_line && (
          <p className="text-xs text-muted-foreground">
            Egen rad efter texten: {profile.caption.tag_line}
            {profile.caption.max_extra_hashtags != null && `, och högst ${profile.caption.max_extra_hashtags} hashtags till`}. Ändra den
            under Avancerat (YAML).
          </p>
        )}
        <div>
          <span className="block text-[13px] text-muted-foreground">Så blir en caption med rubriken &quot;{EXAMPLE_TITLE}&quot;</span>
          <p className="mt-1 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words">
            {previewCaption(v.template, v.mustInclude, v.hashtags)}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px_180px]">
          <Field label="Text på skärmen" hint="Tomt betyder ingen text.">
            <input value={v.onScreenText} onChange={(e) => set("onScreenText", e.target.value)} className={field} />
          </Field>
          <Field label="Var texten står">
            <select value={v.textPosition} onChange={(e) => set("textPosition", e.target.value as TextPosition)} className={field}>
              <option value="top">Överst</option>
              <option value="bottom">Nederst</option>
            </select>
          </Field>
          <Field label="Rubrikens stil">
            <select value={v.hookStyle} onChange={(e) => set("hookStyle", e.target.value as HookStyle)} className={field}>
              <option value="sans">Tjock (sans-serif)</option>
              <option value="serif">Serif i versaler</option>
            </select>
          </Field>
        </div>
      </Section>

      <Section title="Klipplängd" id="langd">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-[13px] text-muted-foreground">Kampanjen tillåter (s)</p>
            <div className="flex items-center gap-2">
              <input aria-label="Kampanjen tillåter, kortast" value={v.allowedMin} onChange={(e) => set("allowedMin", e.target.value)} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
              <span className="text-muted-foreground">till</span>
              <input aria-label="Kampanjen tillåter, längst" value={v.allowedMax} onChange={(e) => set("allowedMax", e.target.value)} onBlur={leave("allowed")} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
            </div>
            {shown("allowed") ? (
              <p role="alert" className="text-xs text-bad">{problems.allowed}</p>
            ) : (
              <p className="text-xs text-muted-foreground">Regelkontrollen underkänner klipp utanför.</p>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-[13px] text-muted-foreground">Appen klipper (s)</p>
            <div className="flex items-center gap-2">
              <input aria-label="Appen klipper, kortast" value={v.clipMin} onChange={(e) => set("clipMin", e.target.value)} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
              <span className="text-muted-foreground">till</span>
              <input aria-label="Appen klipper, längst" value={v.clipMax} onChange={(e) => set("clipMax", e.target.value)} onBlur={leave("clip")} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
            </div>
            {shown("clip") ? (
              <p role="alert" className="text-xs text-bad">{problems.clip}</p>
            ) : (
              <p className="text-xs text-muted-foreground">Längden som appen letar efter. Ska ligga inom kampanjens gräns.</p>
            )}
          </div>
        </div>
      </Section>

      <Section title="Betalning" id="betalning">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="US$ per 1 000 visningar" error={shown("cpm")}>
            <input value={v.cpm} onChange={(e) => set("cpm", e.target.value)} onBlur={leave("cpm")} aria-invalid={!!shown("cpm") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
          </Field>
          <Field label="Minsta visningar" error={shown("minViews")}>
            <input value={v.minViews} onChange={(e) => set("minViews", e.target.value)} onBlur={leave("minViews")} aria-invalid={!!shown("minViews") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
          </Field>
          <Field label="Tak per klipp (US$)" hint="Tomt betyder inget tak." error={shown("maxPayout")}>
            <input value={v.maxPayout} onChange={(e) => set("maxPayout", e.target.value)} onBlur={leave("maxPayout")} aria-invalid={!!shown("maxPayout") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
          </Field>
        </div>
      </Section>

      <Section title="Format" id="format">
        <p className="text-sm text-muted-foreground">Claude väljer bara klipp som passar något av formaten. Skriv på engelska, som kampanjsidan.</p>
        <FormatList formats={v.formats} onChange={(x) => set("formats", x)} />
        {shown("formats") && <p role="alert" className="text-xs text-bad">{problems.formats}</p>}
      </Section>

      <Section title="Plattformar och konton" id="konton">
        <div className="flex flex-wrap gap-x-6 gap-y-2" role="group" aria-label="Plattformar där kampanjen postas">
          {PLATFORM_CHOICES.map((p) => (
            <Toggle key={p.id} checked={v.platforms.includes(p.id)} onChange={(on) => togglePlatform(p.id, on)} label={p.label} />
          ))}
        </div>
        {shown("platforms") && <p role="alert" className="text-xs text-bad">{problems.platforms}</p>}
        {accountsError && <p role="alert" className="text-sm text-bad">{accountsError}</p>}
        {ACCOUNT_PLATFORMS.filter((ap) => v.platforms.includes(ap.id)).map((ap) => {
          const connected = accounts?.[ap.id] ?? [];
          const chosen = v.accounts[ap.id];
          // Konton i profilen som inte är kopplade i appen. De står kvar tills Robin tar bort dem.
          const unknown = chosen.filter((id) => !connected.some((a) => a.id === id));
          return (
            <fieldset key={ap.id} className="space-y-2 rounded-xl border border-line px-4 py-3" data-accounts={ap.id}>
              <legend className="px-1 text-[13px] text-muted-foreground">Vilka {ap.kind} på {ap.label} som postar</legend>
              {!accounts && !accountsError && <p className="text-sm text-muted-foreground">Läser kontona…</p>}
              {accounts && connected.length === 0 && unknown.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Inga {ap.kind} är kopplade.{" "}
                  <Link href="/settings#konton" className="text-foreground underline underline-offset-4">
                    Koppla ett under Inställningar
                  </Link>
                  .
                </p>
              )}
              {connected.map((a) => (
                <Toggle
                  key={a.id}
                  checked={chosen.includes(a.id)}
                  onChange={(on) => toggleAccount(ap.id, a.id, on)}
                  label={a.name}
                  hint={a.connected ? undefined : "Inloggningen har gått ut. Logga in igen under Inställningar."}
                />
              ))}
              {unknown.map((id, i) => (
                <Toggle
                  key={id}
                  checked
                  onChange={() => toggleAccount(ap.id, id, false)}
                  label={`Ett konto som inte är kopplat${unknown.length > 1 ? ` (${i + 1})` : ""}`}
                  hint="Kampanjen nämner det, men appen känner inte till det. Koppla det under Inställningar, eller ta bort bocken."
                />
              ))}
              {accounts && (connected.length > 0 || unknown.length > 0) && (
                <p className="text-xs text-muted-foreground">
                  {chosen.length === 0
                    ? `Inget valt: alla kopplade ${ap.kind} får posta kampanjen.`
                    : `Bara ${chosen.length === 1 ? "det valda" : `de ${chosen.length} valda`} får posta kampanjen. Appen nekar andra.`}
                </p>
              )}
            </fieldset>
          );
        })}
      </Section>

      <div className="sticky bottom-0 z-10 -mx-1 space-y-2 bg-background/95 px-1 py-4 backdrop-blur">
        {submitted && bad.length > 0 && <p className="text-sm text-warn">Rätta fälten med rött innan du sparar.</p>}
        {error && (
          <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        {saved && !dirty && (
          <p role="status" className="flex items-center gap-2 text-sm text-good">
            <CheckCircle2 className="size-4" aria-hidden />
            Kampanjen är sparad. Det formuläret inte visar står kvar i filen.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" className="h-9" disabled={!dirty || busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Spara
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="h-9"
            disabled={!dirty || busy}
            onClick={() => {
              setValues(start);
              setSubmitted(false);
              setTouched(new Set());
              setError(null);
            }}
          >
            Ångra ändringar
          </Button>
        </div>
      </div>
    </form>
  );
}
