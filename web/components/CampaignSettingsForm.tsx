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
  previewCaption,
  type FormatRow,
} from "@/components/CampaignFields";
import { api } from "@/lib/api";
import { numberText, parseNumber } from "@/lib/numbers";
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
  { id: "tiktok", label: "TikTok", kind: "accounts" },
  { id: "youtube_shorts", label: "YouTube Shorts", kind: "channels" },
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
    streamer: v.streamer.trim() ? null : "Enter the streamer's or brand's name.",
    contentRewards: contentRewardsProblem(v.contentRewards),
    analytics: httpsProblem(v.analytics),
    template: v.template.includes("{title}") ? null : "The template must contain {title}, where the clip's headline goes.",
    allowed:
      !whole(n.allowedMin) || !whole(n.allowedMax)
        ? "Enter whole seconds, for example 7 and 60."
        : n.allowedMin! < 1 || n.allowedMin! >= n.allowedMax!
          ? "Shortest must be at least 1 and less than longest."
          : null,
    clip:
      v.clipMin === "" && v.clipMax === ""
        ? null
        : !whole(n.clipMin) || !whole(n.clipMax)
          ? "Enter whole seconds, for example 15 and 45."
          : n.clipMin! < 1 || n.clipMin! >= n.clipMax!
            ? "Shortest must be at least 1 and less than longest."
            : whole(n.allowedMin) && whole(n.allowedMax) && (n.clipMin! < n.allowedMin! || n.clipMax! > n.allowedMax!)
              ? `The app must clip within what the campaign allows: ${v.allowedMin}–${v.allowedMax} s.`
              : null,
    cpm: n.cpm != null && Number.isNaN(n.cpm) ? "Enter a number, for example 1.5." : null,
    minViews: n.minViews != null && !whole(n.minViews) ? "Enter a whole number, for example 2000." : null,
    maxPayout: n.maxPayout != null && Number.isNaN(n.maxPayout) ? "Enter a number, for example 150." : null,
    formats: v.formats.some((f) => !f.name.trim()) ? "Give each format a name, or remove the row." : null,
    platforms: v.platforms.length ? null : "Choose at least one platform.",
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
      setError(err instanceof Error ? err.message : "Could not save the campaign");
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
      <Section title="Campaign">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Streamer's or brand's name" error={shown("streamer")}>
            <input value={v.streamer} onChange={(e) => set("streamer", e.target.value)} onBlur={leave("streamer")} aria-invalid={!!shown("streamer") || undefined} className={field} />
          </Field>
          <Field label="Campaign name" hint="As it says on the campaign page.">
            <input value={v.campaign} onChange={(e) => set("campaign", e.target.value)} className={field} />
          </Field>
          <Field label="Language in the videos" hint="Language code, for example en or sv." className="sm:max-w-40">
            <input value={v.language} onChange={(e) => set("language", e.target.value)} className={field} />
          </Field>
        </div>
      </Section>

      <Section title="Links" id="lankar">
        <Field
          label="Link to the campaign in Content Rewards"
          hint="Where you submit the clips. Example clips and Submit help use it."
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
        <Field label="Analytics form" hint="The form for clips above the minimum views." error={shown("analytics")}>
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
        <Field label="Template" hint="{title} is replaced with the clip's headline." error={shown("template")}>
          <input value={v.template} onChange={(e) => set("template", e.target.value)} onBlur={leave("template")} aria-invalid={!!shown("template") || undefined} className={field} />
        </Field>
        <Chips label="Must include" values={v.mustInclude} onChange={(x) => set("mustInclude", x)} placeholder="Type and press Enter" />
        <Chips label="Hashtags" values={v.hashtags} onChange={(x) => set("hashtags", x)} prefix="#" placeholder="#hashtag" />
        {profile.caption.tag_line && (
          <p className="text-xs text-muted-foreground">
            Own line after the text: {profile.caption.tag_line}
            {profile.caption.max_extra_hashtags != null && `, and at most ${profile.caption.max_extra_hashtags} more hashtags`}. Change it
            under Advanced (YAML).
          </p>
        )}
        <div>
          <span className="block text-[13px] text-muted-foreground">A caption with the headline &quot;{EXAMPLE_TITLE}&quot; looks like this</span>
          <p className="mt-1 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words">
            {previewCaption(v.template, v.mustInclude, v.hashtags)}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px_180px]">
          <Field label="On-screen text" hint="Empty means no text.">
            <input value={v.onScreenText} onChange={(e) => set("onScreenText", e.target.value)} className={field} />
          </Field>
          <Field label="Where the text goes">
            <select value={v.textPosition} onChange={(e) => set("textPosition", e.target.value as TextPosition)} className={field}>
              <option value="top">Top</option>
              <option value="bottom">Bottom</option>
            </select>
          </Field>
          <Field label="Headline style">
            <select value={v.hookStyle} onChange={(e) => set("hookStyle", e.target.value as HookStyle)} className={field}>
              <option value="sans">Tjock (sans-serif)</option>
              <option value="serif">Serif in capitals</option>
            </select>
          </Field>
        </div>
      </Section>

      <Section title="Clip length" id="langd">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-[13px] text-muted-foreground">The campaign allows (s)</p>
            <div className="flex items-center gap-2">
              <input aria-label="The campaign allows, shortest" value={v.allowedMin} onChange={(e) => set("allowedMin", e.target.value)} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
              <span className="text-muted-foreground">till</span>
              <input aria-label="The campaign allows, longest" value={v.allowedMax} onChange={(e) => set("allowedMax", e.target.value)} onBlur={leave("allowed")} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
            </div>
            {shown("allowed") ? (
              <p role="alert" className="text-xs text-bad">{problems.allowed}</p>
            ) : (
              <p className="text-xs text-muted-foreground">The rule check fails clips outside it.</p>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-[13px] text-muted-foreground">The app clips (s)</p>
            <div className="flex items-center gap-2">
              <input aria-label="The app clips, shortest" value={v.clipMin} onChange={(e) => set("clipMin", e.target.value)} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
              <span className="text-muted-foreground">till</span>
              <input aria-label="The app clips, longest" value={v.clipMax} onChange={(e) => set("clipMax", e.target.value)} onBlur={leave("clip")} inputMode="numeric" className={cn(field, "w-20 tabular-nums")} />
            </div>
            {shown("clip") ? (
              <p role="alert" className="text-xs text-bad">{problems.clip}</p>
            ) : (
              <p className="text-xs text-muted-foreground">The length the app looks for. Must be within the campaign's limit.</p>
            )}
          </div>
        </div>
      </Section>

      <Section title="Payment" id="betalning">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="US$ per 1,000 views" error={shown("cpm")}>
            <input value={v.cpm} onChange={(e) => set("cpm", e.target.value)} onBlur={leave("cpm")} aria-invalid={!!shown("cpm") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
          </Field>
          <Field label="Minimum views" error={shown("minViews")}>
            <input value={v.minViews} onChange={(e) => set("minViews", e.target.value)} onBlur={leave("minViews")} aria-invalid={!!shown("minViews") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
          </Field>
          <Field label="Cap per clip (US$)" hint="Empty means no cap." error={shown("maxPayout")}>
            <input value={v.maxPayout} onChange={(e) => set("maxPayout", e.target.value)} onBlur={leave("maxPayout")} aria-invalid={!!shown("maxPayout") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
          </Field>
        </div>
      </Section>

      <Section title="Format" id="format">
        <p className="text-sm text-muted-foreground">Claude only chooses clips that fit one of the formats. Write in English, like the campaign page.</p>
        <FormatList formats={v.formats} onChange={(x) => set("formats", x)} />
        {shown("formats") && <p role="alert" className="text-xs text-bad">{problems.formats}</p>}
      </Section>

      <Section title="Platforms and accounts" id="konton">
        <div className="flex flex-wrap gap-x-6 gap-y-2" role="group" aria-label="Platforms where the campaign is posted">
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
              <legend className="px-1 text-[13px] text-muted-foreground">Which {ap.kind} on {ap.label} post</legend>
              {!accounts && !accountsError && <p className="text-sm text-muted-foreground">Loading the accounts…</p>}
              {accounts && connected.length === 0 && unknown.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No {ap.kind} are connected.{" "}
                  <Link href="/settings#konton" className="text-foreground underline underline-offset-4">
                    Connect one under Settings
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
                  hint={a.connected ? undefined : "The login has expired. Log in again under Settings."}
                />
              ))}
              {unknown.map((id, i) => (
                <Toggle
                  key={id}
                  checked
                  onChange={() => toggleAccount(ap.id, id, false)}
                  label={`An account that is not connected${unknown.length > 1 ? ` (${i + 1})` : ""}`}
                  hint="The campaign mentions it, but the app does not know it. Connect it under Settings, or uncheck it."
                />
              ))}
              {accounts && (connected.length > 0 || unknown.length > 0) && (
                <p className="text-xs text-muted-foreground">
                  {chosen.length === 0
                    ? `None chosen: all connected ${ap.kind} may post the campaign.`
                    : `Only ${chosen.length === 1 ? "the chosen one" : `the ${chosen.length} chosen`} may post the campaign. The app refuses others.`}
                </p>
              )}
            </fieldset>
          );
        })}
      </Section>

      <div className="sticky bottom-0 z-10 -mx-1 space-y-2 bg-background/95 px-1 py-4 backdrop-blur">
        {submitted && bad.length > 0 && <p className="text-sm text-warn">Fix the fields in red before you save.</p>}
        {error && (
          <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        {saved && !dirty && (
          <p role="status" className="flex items-center gap-2 text-sm text-good">
            <CheckCircle2 className="size-4" aria-hidden />
            The campaign is saved. What the form does not show stays in the file.
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
            Undo changes
          </Button>
        </div>
      </div>
    </form>
  );
}
