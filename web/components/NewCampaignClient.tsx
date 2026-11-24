"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
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
  parseNumber,
  previewCaption,
  type FormatRow,
} from "@/components/CampaignFields";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfileCreate, CampaignSource, OverlayMode, Platform, TextPosition } from "@/lib/types";

export { previewCaption } from "@/components/CampaignFields";

// Fält vars standardvärde bygger på källan och kanalen. De följer dem tills de ändras för hand.
const fromChannel = (source: CampaignSource, c: string) =>
  source === "local"
    ? { onScreenText: "", template: "{title}", mustInclude: [] as string[], hashtags: [] as string[] }
    : {
        onScreenText: c ? `twitch.tv/${c}` : "",
        template: c ? `{title} 👉 twitch.tv/${c}` : "{title}",
        mustInclude: c ? [`twitch.tv/${c}`] : [],
        hashtags: c ? [`#${c}`, "#twitchclips", "#gaming"] : ["#twitchclips", "#gaming"],
      };

/** Kampanjens id av ett namn: små bokstäver, siffror och _. Samma regel som servern (profile_edit.ID). */
export const idFrom = (name: string) =>
  name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);

const ID = /^[a-z0-9][a-z0-9_]{1,39}$/;

/** Ifyllt från adressen, till exempel från Hem (?channel=) eller Bevakning (?campaign=&cpm=). */
export interface NewCampaignStart {
  channel: string;
  streamer: string;
  campaign?: string;
  source?: CampaignSource;
  cpm?: string;
  minViews?: string;
  maxPayout?: string;
  platforms?: Platform[];
  scoutRef?: string;
  /** Länken till kampanjen i Content Rewards, från Bevakning. */
  link?: string;
}

type Key =
  | "channel" | "name" | "streamer" | "minS" | "maxS" | "minViews" | "template" | "cpm" | "maxPayout" | "engagement"
  | "contentRewards" | "analytics" | "platforms" | "formats" | "lines";

export function NewCampaignClient(start: NewCampaignStart) {
  const router = useRouter();
  const startSource: CampaignSource = start.source === "local" ? "local" : "twitch";
  const startChannel = start.channel.trim().toLowerCase();
  const first = fromChannel(startSource, startChannel);
  const [source, setSourceValue] = useState<CampaignSource>(startSource);
  const [channel, setChannelValue] = useState(startChannel);
  const [streamer, setStreamer] = useState(start.streamer);
  const [name, setName] = useState(startSource === "local" ? idFrom(start.streamer || start.campaign || "") : "");
  const [nameTouched, setNameTouched] = useState(false);
  const [campaign, setCampaign] = useState(start.campaign ?? "");
  const [language, setLanguage] = useState("en");
  const [contentRewards, setContentRewards] = useState(start.link ?? "");
  const [analytics, setAnalytics] = useState("");
  const [minS, setMinS] = useState("15");
  const [maxS, setMaxS] = useState("45");
  const [onScreenText, setOnScreenText] = useState(first.onScreenText);
  const [textPosition, setTextPosition] = useState<TextPosition>("top");
  const [overlayRequired, setOverlayRequired] = useState(startSource === "twitch");
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("full");
  const [facecamRequired, setFacecamRequired] = useState(startSource === "twitch");
  const [template, setTemplate] = useState(first.template);
  const [mustInclude, setMustInclude] = useState(first.mustInclude);
  const [hashtags, setHashtags] = useState(first.hashtags);
  const [formats, setFormats] = useState<FormatRow[]>([
    { name: "Funny moments and fails", description: "Bad deaths, rage and fails. The reaction is the clip." },
    { name: "Clutch plays", description: "Multi kills and last-second wins. Open on the payoff." },
    { name: "Genuine reactions", description: "Jump scares, disbelief and hype, with the facecam large." },
  ]);
  const [cpm, setCpm] = useState(start.cpm ?? "1");
  const [minViews, setMinViews] = useState(start.minViews ?? "2000");
  const [maxPayout, setMaxPayout] = useState(start.maxPayout ?? "150");
  const [engagement, setEngagement] = useState("0,8");
  const [platforms, setPlatforms] = useState<Platform[]>(
    start.platforms?.length ? start.platforms : ["tiktok", "youtube_shorts", "instagram_reels"]
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; exists?: string } | null>(null);
  // Felen visas vid fältet när Robin har lämnat det, och alla när han trycker Skapa kampanj.
  const [touched, setTouched] = useState<Set<Key>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const leave = (key: Key) => () => setTouched((prev) => new Set(prev).add(key));

  /** Byt de fält som fortfarande har standardvärdet för den gamla källan och kanalen. */
  function follow(nextSource: CampaignSource, nextChannel: string) {
    const before = fromChannel(source, channel);
    const after = fromChannel(nextSource, nextChannel);
    if (onScreenText === before.onScreenText) setOnScreenText(after.onScreenText);
    if (template === before.template) setTemplate(after.template);
    if (JSON.stringify(mustInclude) === JSON.stringify(before.mustInclude)) setMustInclude(after.mustInclude);
    if (JSON.stringify(hashtags) === JSON.stringify(before.hashtags)) setHashtags(after.hashtags);
  }

  function setChannel(value: string) {
    const next = value.trim().toLowerCase();
    follow(source, next);
    setChannelValue(next);
  }

  function setSource(next: CampaignSource) {
    if (next === source) return;
    follow(next, channel);
    setSourceValue(next);
    // Egna videor har ingen kamera och oftast ingen overlay. Robin kan slå på dem igen.
    setOverlayRequired(next === "twitch");
    setFacecamRequired(next === "twitch");
    if (next === "local" && !nameTouched) setName(idFrom(streamer || campaign));
  }

  function setStreamerName(value: string) {
    setStreamer(value);
    if (source === "local" && !nameTouched) setName(idFrom(value));
  }

  const numbers = {
    minS: parseNumber(minS),
    maxS: parseNumber(maxS),
    cpm: parseNumber(cpm),
    minViews: parseNumber(minViews),
    maxPayout: parseNumber(maxPayout),
    engagement: parseNumber(engagement),
  };
  const problems: Partial<Record<Key, string>> = {};
  // Samma regel som servern (campaign/profile_edit.py ID): kanalen eller id:t blir kampanjens filnamn.
  if (source === "twitch" && !ID.test(channel)) {
    problems.channel = "Enter the channel name as in the link twitch.tv/<channel>: 2–40 lowercase letters, digits and _, and not _ first.";
  }
  if (source === "local" && !ID.test(name)) {
    problems.name = "Enter 2–40 lowercase letters a–z, digits and _. Not _ first.";
  }
  if (!streamer.trim()) problems.streamer = "Enter the streamer's or brand's name.";
  const whole = (n: number | null) => n != null && Number.isInteger(n);
  if (!whole(numbers.minS)) problems.minS = "Enter a whole number, for example 15.";
  if (!whole(numbers.maxS)) problems.maxS = "Enter a whole number, for example 45.";
  if (whole(numbers.minS) && whole(numbers.maxS) && (numbers.minS! < 1 || numbers.minS! >= numbers.maxS!)) {
    problems.maxS = "Shortest must be at least 1 and less than longest.";
  }
  if (!whole(numbers.minViews)) problems.minViews = "Enter a whole number, for example 2000.";
  if (!template.includes("{title}")) problems.template = "The template must contain {title}, where the clip's headline goes.";
  const decimal = "Enter a number that is 0 or more, for example 1.5.";
  for (const key of ["cpm", "maxPayout", "engagement"] as const) {
    const n = numbers[key];
    if (n == null || Number.isNaN(n)) problems[key] = decimal;
  }
  const crProblem = contentRewardsProblem(contentRewards);
  if (crProblem) problems.contentRewards = crProblem;
  const formProblem = httpsProblem(analytics);
  if (formProblem) problems.analytics = formProblem;
  if (platforms.length === 0) problems.platforms = "Choose at least one platform.";
  if (formats.some((f) => !f.name.trim())) problems.formats = "Give each format a name, or remove the row.";
  const texts: string[] = [streamer, campaign, language, onScreenText, template, ...mustInclude, ...hashtags,
    ...formats.flatMap((f) => [f.name, f.description])];
  // Fälten är en rad; en radbrytning kan komma in via adressen (?streamer=) och servern skriver texten i YAML-filen.
  if (texts.some((v) => /[\r\n]/.test(v))) problems.lines = "Remove the line break. Each text must be on one line.";

  const shown = (key: Key) => (submitted || touched.has(key) ? problems[key] ?? null : null);
  const all = Object.values(problems);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (all.length) {
      // Rulla till det första felet.
      requestAnimationFrame(() => document.querySelector<HTMLElement>("form [role=alert]")?.scrollIntoView({ block: "center" }));
      return;
    }
    setBusy(true);
    setError(null);
    const body: CampaignProfileCreate = {
      source,
      channel: source === "twitch" ? channel : "",
      name: source === "local" ? name : undefined,
      streamer: streamer.trim(),
      campaign: campaign.trim() || undefined,
      language: language.trim() || undefined,
      clip_min_s: numbers.minS!,
      clip_max_s: numbers.maxS!,
      on_screen_text: onScreenText.trim(),
      text_position: textPosition,
      overlay_required: overlayRequired,
      overlay_mode: overlayMode,
      facecam_required: facecamRequired,
      must_include: mustInclude,
      hashtags,
      caption_template: template,
      formats: formats.map((f) => ({ name: f.name.trim(), description: f.description.trim() })),
      cpm_usd: numbers.cpm!,
      min_views: numbers.minViews!,
      max_payout_per_clip_usd: numbers.maxPayout!,
      engagement_min_percent: numbers.engagement!,
      content_rewards_url: contentRewards.trim() || undefined,
      analytics_form_url: analytics.trim() || undefined,
      platforms,
      scout_ref: start.scoutRef || undefined,
    };
    try {
      const created = await api.campaign.createProfile(body);
      router.push(`/campaigns/${created.name}?skapad=1${created.warning ? "&varning=twitch" : ""}`);
    } catch (err) {
      const text = err instanceof Error ? err.message : "Could not create the campaign";
      const id = source === "twitch" ? channel : name;
      setError({ text, exists: (err as { status?: number }).status === 409 ? id : undefined });
      setBusy(false);
    }
  }

  return (
    <main className="w-full max-w-[1000px] px-6 py-9 md:px-11">
      <Link href="/campaigns" className="text-sm text-muted-foreground hover:text-foreground">
        Campaigns
      </Link>
      <h1 className="mt-1 text-3xl font-bold tracking-tight">New campaign</h1>
      <p className="mt-1.5 text-muted-foreground">
        Fill in what the campaign page requires. Everything except source and name has defaults. Everything can be changed later under Set up.
      </p>
      {start.scoutRef && (
        <p className="mt-3 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
          Filled in from Scout. Check the rules against the campaign page.
        </p>
      )}

      <form onSubmit={submit} noValidate>
        <Section step={1} title="Source">
          <div role="radiogroup" aria-label="Where the clips come from" className="grid gap-2 sm:grid-cols-2">
            {([
              ["twitch", "A Twitch channel", "The app downloads the streamer's VODs and clips them."],
              ["local", "Uploads only", "You upload the files yourself, for example trailers. No Twitch."],
            ] as const).map(([id, label, hint]) => (
              <label
                key={id}
                className={cn(
                  "flex cursor-pointer gap-3 rounded-xl border px-3 py-2.5 text-sm",
                  source === id ? "border-brand bg-brand/10" : "border-line hover:border-[#3a3a44]"
                )}
              >
                <input type="radio" name="source" checked={source === id} onChange={() => setSource(id)} className="mt-0.5 accent-[#7c5cff]" />
                <span>
                  {label}
                  <span className="block text-xs text-muted-foreground">{hint}</span>
                </span>
              </label>
            ))}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {source === "twitch" ? (
              <Field label="Twitch channel" hint={channel ? `twitch.tv/${channel}` : "What comes after twitch.tv/"} error={shown("channel")}>
                <input
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  onBlur={leave("channel")}
                  aria-invalid={!!shown("channel") || undefined}
                  placeholder="skiimasktimmy"
                  className={field}
                  autoFocus={!startChannel}
                />
              </Field>
            ) : (
              <Field label="Campaign ID" hint="Becomes the file name. Lowercase letters a–z, digits and _." error={shown("name")}>
                <input
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value.trim().toLowerCase());
                    setNameTouched(true);
                  }}
                  onBlur={leave("name")}
                  aria-invalid={!!shown("name") || undefined}
                  placeholder="blizzard_nordics"
                  className={field}
                />
              </Field>
            )}
            <Field label={source === "twitch" ? "Streamer's name" : "Streamer's or brand's name"} error={shown("streamer")}>
              <input
                value={streamer}
                onChange={(e) => setStreamerName(e.target.value)}
                onBlur={leave("streamer")}
                aria-invalid={!!shown("streamer") || undefined}
                placeholder={source === "twitch" ? "SkiiMaskTimmy" : "Blizzard Entertainment"}
                className={field}
              />
            </Field>
            <Field label="Campaign name" hint="As it says on the campaign page. Empty becomes the streamer's name.">
              <input value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="$2,500 Streamer Campaign" className={field} />
            </Field>
            <Field label="Language in the videos" hint="Language code, for example en or sv.">
              <input value={language} onChange={(e) => setLanguage(e.target.value)} className={field} />
            </Field>
          </div>
        </Section>

        <Section step={2} title="Links">
          <Field
            label="Link to the campaign in Content Rewards"
            hint="Where you submit the clips. Example clips and Submit help use it."
            error={shown("contentRewards")}
          >
            <input
              value={contentRewards}
              onChange={(e) => setContentRewards(e.target.value)}
              onBlur={leave("contentRewards")}
              aria-invalid={!!shown("contentRewards") || undefined}
              inputMode="url"
              placeholder="https://contentrewards.com/c/campaigns/…"
              className={field}
            />
          </Field>
          <Field label="Analytics form" hint="The form for clips above the minimum views. Empty is fine." error={shown("analytics")}>
            <input
              value={analytics}
              onChange={(e) => setAnalytics(e.target.value)}
              onBlur={leave("analytics")}
              aria-invalid={!!shown("analytics") || undefined}
              inputMode="url"
              placeholder="https://forms.gle/…"
              className={field}
            />
          </Field>
        </Section>

        <Section step={3} title="Clip length">
          <div className="flex flex-wrap items-start gap-3">
            <Field label="Shortest (s)" className="w-28" error={shown("minS")}>
              <input value={minS} onChange={(e) => setMinS(e.target.value)} onBlur={leave("minS")} aria-invalid={!!shown("minS") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Longest (s)" className="w-28" error={shown("maxS")}>
              <input value={maxS} onChange={(e) => setMaxS(e.target.value)} onBlur={leave("maxS")} aria-invalid={!!shown("maxS") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <Section step={4} title="The picture">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px]">
            <Field label="On-screen text" hint="Empty means no text.">
              <input value={onScreenText} onChange={(e) => setOnScreenText(e.target.value)} className={field} />
            </Field>
            <Field label="Where the text goes">
              <select value={textPosition} onChange={(e) => setTextPosition(e.target.value as TextPosition)} className={field}>
                <option value="top">Top</option>
                <option value="bottom">Bottom</option>
              </select>
            </Field>
          </div>
          <Toggle checked={overlayRequired} onChange={setOverlayRequired} label="The campaign requires an overlay image" hint="Without the image the clips fail the check." />
          {overlayRequired && (
            <Field label="Overlay image mode" className="max-w-xs">
              <select value={overlayMode} onChange={(e) => setOverlayMode(e.target.value as OverlayMode)} className={field}>
                <option value="full">Whole clip (1080 x 1920)</option>
                <option value="top">Top (1080 wide)</option>
                <option value="bottom">Bottom (1080 wide)</option>
              </select>
            </Field>
          )}
          <Toggle checked={facecamRequired} onChange={setFacecamRequired} label="The streamer's camera must show in every clip" hint="The image review fails clips without a camera." />
        </Section>

        <Section step={5} title="Caption">
          <Field label="Template" hint="{title} is replaced with the clip's headline." error={shown("template")}>
            <input value={template} onChange={(e) => setTemplate(e.target.value)} onBlur={leave("template")} aria-invalid={!!shown("template") || undefined} className={field} />
          </Field>
          <Chips label="Must include" values={mustInclude} onChange={setMustInclude} placeholder="Type and press Enter" />
          <Chips label="Hashtags" values={hashtags} onChange={setHashtags} prefix="#" placeholder="#hashtag" />
          <div>
            <span className="block text-[13px] text-muted-foreground">A caption with the headline &quot;{EXAMPLE_TITLE}&quot; looks like this</span>
            <p data-testid="caption-preview" className="mt-1 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words">
              {previewCaption(template, mustInclude, hashtags)}
            </p>
          </div>
        </Section>

        <Section step={6} title="Format">
          <p className="text-sm text-muted-foreground">Claude only chooses clips that fit one of the formats. Write in English, like the campaign page.</p>
          <FormatList formats={formats} onChange={setFormats} />
          {shown("formats") && <p role="alert" className="text-xs text-bad">{problems.formats}</p>}
        </Section>

        <Section step={7} title="Payment">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="US$ per 1,000 views" error={shown("cpm")}>
              <input value={cpm} onChange={(e) => setCpm(e.target.value)} onBlur={leave("cpm")} aria-invalid={!!shown("cpm") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minimum views" error={shown("minViews")}>
              <input value={minViews} onChange={(e) => setMinViews(e.target.value)} onBlur={leave("minViews")} aria-invalid={!!shown("minViews") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Cap per clip (US$)" error={shown("maxPayout")}>
              <input value={maxPayout} onChange={(e) => setMaxPayout(e.target.value)} onBlur={leave("maxPayout")} aria-invalid={!!shown("maxPayout") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minimum engagement (%)" error={shown("engagement")}>
              <input value={engagement} onChange={(e) => setEngagement(e.target.value)} onBlur={leave("engagement")} aria-invalid={!!shown("engagement") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <Section step={8} title="Platforms">
          <div className="flex flex-wrap gap-x-6 gap-y-2" role="group" aria-label="Platforms where the campaign is posted">
            {PLATFORM_CHOICES.map((p) => (
              <Toggle
                key={p.id}
                checked={platforms.includes(p.id)}
                onChange={(on) => setPlatforms(on ? [...platforms, p.id] : platforms.filter((x) => x !== p.id))}
                label={p.label}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">You choose which accounts post under Set up once the campaign is created.</p>
          {shown("platforms") && <p role="alert" className="text-xs text-bad">{problems.platforms}</p>}
        </Section>

        <div className="space-y-3 py-7">
          {submitted && all.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" aria-label="What is missing">
              {[...new Set(all)].map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
              {error.text}
              {error.exists && (
                <>
                  {" "}
                  <Link href={`/campaigns/${error.exists}`} className="font-medium text-foreground underline underline-offset-4">
                    Open the campaign
                  </Link>
                </>
              )}
            </p>
          )}
          <Button type="submit" className="h-10 px-5" disabled={busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            {busy ? "Creating the campaign…" : "Create campaign"}
          </Button>
        </div>
      </form>
    </main>
  );
}

