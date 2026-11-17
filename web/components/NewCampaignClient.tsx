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
  numberText,
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
  const [contentRewards, setContentRewards] = useState("");
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
    problems.channel = "Skriv kanalens namn som i länken twitch.tv/<kanal>: 2–40 små bokstäver, siffror och _, och inte _ först.";
  }
  if (source === "local" && !ID.test(name)) {
    problems.name = "Skriv 2–40 små bokstäver a–z, siffror och _. Inte _ först.";
  }
  if (!streamer.trim()) problems.streamer = "Skriv streamerns eller varumärkets namn.";
  const whole = (n: number | null) => n != null && Number.isInteger(n);
  if (!whole(numbers.minS)) problems.minS = "Skriv ett helt tal, till exempel 15.";
  if (!whole(numbers.maxS)) problems.maxS = "Skriv ett helt tal, till exempel 45.";
  if (whole(numbers.minS) && whole(numbers.maxS) && (numbers.minS! < 1 || numbers.minS! >= numbers.maxS!)) {
    problems.maxS = "Kortast måste vara minst 1 och mindre än längst.";
  }
  if (!whole(numbers.minViews)) problems.minViews = "Skriv ett helt tal, till exempel 2000.";
  if (!template.includes("{title}")) problems.template = "Mallen måste innehålla {title}, där klippets rubrik hamnar.";
  const decimal = "Skriv ett tal som är 0 eller större, till exempel 1,5.";
  for (const key of ["cpm", "maxPayout", "engagement"] as const) {
    const n = numbers[key];
    if (n == null || Number.isNaN(n)) problems[key] = decimal;
  }
  const crProblem = contentRewardsProblem(contentRewards);
  if (crProblem) problems.contentRewards = crProblem;
  const formProblem = httpsProblem(analytics);
  if (formProblem) problems.analytics = formProblem;
  if (platforms.length === 0) problems.platforms = "Välj minst en plattform.";
  if (formats.some((f) => !f.name.trim())) problems.formats = "Ge varje format ett namn, eller ta bort raden.";
  const texts: string[] = [streamer, campaign, language, onScreenText, template, ...mustInclude, ...hashtags,
    ...formats.flatMap((f) => [f.name, f.description])];
  // Fälten är en rad; en radbrytning kan komma in via adressen (?streamer=) och servern skriver texten i YAML-filen.
  if (texts.some((v) => /[\r\n]/.test(v))) problems.lines = "Ta bort radbrytningen. Varje text ska stå på en rad.";

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
      const text = err instanceof Error ? err.message : "Det gick inte att skapa kampanjen";
      const id = source === "twitch" ? channel : name;
      setError({ text, exists: (err as { status?: number }).status === 409 ? id : undefined });
      setBusy(false);
    }
  }

  return (
    <main className="w-full max-w-[1000px] px-6 py-9 md:px-11">
      <Link href="/campaigns" className="text-sm text-muted-foreground hover:text-foreground">
        Kampanjer
      </Link>
      <h1 className="mt-1 text-3xl font-bold tracking-tight">Ny kampanj</h1>
      <p className="mt-1.5 text-muted-foreground">
        Fyll i det kampanjsidan kräver. Allt utom källa och namn har förval. Allt går att ändra senare under Ställ in.
      </p>
      {start.scoutRef && (
        <p className="mt-3 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-muted-foreground">
          Ifyllt från Bevakning. Kontrollera reglerna mot kampanjsidan.
        </p>
      )}

      <form onSubmit={submit} noValidate>
        <Section step={1} title="Källa">
          <div role="radiogroup" aria-label="Var klippen kommer från" className="grid gap-2 sm:grid-cols-2">
            {([
              ["twitch", "En Twitch-kanal", "Appen hämtar streamerns VOD:er och klipper dem."],
              ["local", "Bara egna videor", "Du laddar upp filerna själv, till exempel trailers. Ingen Twitch."],
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
              <Field label="Twitch-kanal" hint={channel ? `twitch.tv/${channel}` : "Det som står efter twitch.tv/"} error={shown("channel")}>
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
              <Field label="Kampanjens id" hint="Blir filens namn. Små bokstäver a–z, siffror och _." error={shown("name")}>
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
            <Field label={source === "twitch" ? "Streamerns namn" : "Streamerns eller varumärkets namn"} error={shown("streamer")}>
              <input
                value={streamer}
                onChange={(e) => setStreamerName(e.target.value)}
                onBlur={leave("streamer")}
                aria-invalid={!!shown("streamer") || undefined}
                placeholder={source === "twitch" ? "SkiiMaskTimmy" : "Blizzard Entertainment"}
                className={field}
              />
            </Field>
            <Field label="Kampanjens namn" hint="Som det står på kampanjsidan. Tomt blir streamerns namn.">
              <input value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="$2,500 Streamer Campaign" className={field} />
            </Field>
            <Field label="Språk i videorna" hint="Språkkod, till exempel en eller sv.">
              <input value={language} onChange={(e) => setLanguage(e.target.value)} className={field} />
            </Field>
          </div>
        </Section>

        <Section step={2} title="Länkar">
          <Field
            label="Länk till kampanjen i Content Rewards"
            hint="Där du skickar in klippen. Exempelklipp och Hjälp att skicka in använder den."
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
          <Field label="Analytics-formuläret" hint="Formuläret för klipp över minsta visningar. Tomt går bra." error={shown("analytics")}>
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

        <Section step={3} title="Klipplängd">
          <div className="flex flex-wrap items-start gap-3">
            <Field label="Kortast (s)" className="w-28" error={shown("minS")}>
              <input value={minS} onChange={(e) => setMinS(e.target.value)} onBlur={leave("minS")} aria-invalid={!!shown("minS") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Längst (s)" className="w-28" error={shown("maxS")}>
              <input value={maxS} onChange={(e) => setMaxS(e.target.value)} onBlur={leave("maxS")} aria-invalid={!!shown("maxS") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <Section step={4} title="Bilden">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_160px]">
            <Field label="Text på skärmen" hint="Tomt betyder ingen text.">
              <input value={onScreenText} onChange={(e) => setOnScreenText(e.target.value)} className={field} />
            </Field>
            <Field label="Var texten står">
              <select value={textPosition} onChange={(e) => setTextPosition(e.target.value as TextPosition)} className={field}>
                <option value="top">Överst</option>
                <option value="bottom">Nederst</option>
              </select>
            </Field>
          </div>
          <Toggle checked={overlayRequired} onChange={setOverlayRequired} label="Kampanjen kräver en overlay-bild" hint="Utan bilden blir klippen underkända." />
          {overlayRequired && (
            <Field label="Overlay-bildens läge" className="max-w-xs">
              <select value={overlayMode} onChange={(e) => setOverlayMode(e.target.value as OverlayMode)} className={field}>
                <option value="full">Hela klippet (1080 x 1920)</option>
                <option value="top">Överst (1080 bred)</option>
                <option value="bottom">Nederst (1080 bred)</option>
              </select>
            </Field>
          )}
          <Toggle checked={facecamRequired} onChange={setFacecamRequired} label="Streamerns kamera ska synas i varje klipp" hint="Bildgranskningen underkänner klipp utan kamera." />
        </Section>

        <Section step={5} title="Caption">
          <Field label="Mall" hint="{title} byts mot klippets rubrik." error={shown("template")}>
            <input value={template} onChange={(e) => setTemplate(e.target.value)} onBlur={leave("template")} aria-invalid={!!shown("template") || undefined} className={field} />
          </Field>
          <Chips label="Måste innehålla" values={mustInclude} onChange={setMustInclude} placeholder="Skriv och tryck Enter" />
          <Chips label="Hashtags" values={hashtags} onChange={setHashtags} prefix="#" placeholder="#hashtag" />
          <div>
            <span className="block text-[13px] text-muted-foreground">Så blir en caption med rubriken &quot;{EXAMPLE_TITLE}&quot;</span>
            <p data-testid="caption-preview" className="mt-1 rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words">
              {previewCaption(template, mustInclude, hashtags)}
            </p>
          </div>
        </Section>

        <Section step={6} title="Format">
          <p className="text-sm text-muted-foreground">Claude väljer bara klipp som passar något av formaten. Skriv på engelska, som kampanjsidan.</p>
          <FormatList formats={formats} onChange={setFormats} />
          {shown("formats") && <p role="alert" className="text-xs text-bad">{problems.formats}</p>}
        </Section>

        <Section step={7} title="Betalning">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="US$ per 1 000 visningar" error={shown("cpm")}>
              <input value={cpm} onChange={(e) => setCpm(e.target.value)} onBlur={leave("cpm")} aria-invalid={!!shown("cpm") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minsta visningar" error={shown("minViews")}>
              <input value={minViews} onChange={(e) => setMinViews(e.target.value)} onBlur={leave("minViews")} aria-invalid={!!shown("minViews") || undefined} inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Tak per klipp (US$)" error={shown("maxPayout")}>
              <input value={maxPayout} onChange={(e) => setMaxPayout(e.target.value)} onBlur={leave("maxPayout")} aria-invalid={!!shown("maxPayout") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minsta engagemang (%)" error={shown("engagement")}>
              <input value={engagement} onChange={(e) => setEngagement(e.target.value)} onBlur={leave("engagement")} aria-invalid={!!shown("engagement") || undefined} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <Section step={8} title="Plattformar">
          <div className="flex flex-wrap gap-x-6 gap-y-2" role="group" aria-label="Plattformar där kampanjen postas">
            {PLATFORM_CHOICES.map((p) => (
              <Toggle
                key={p.id}
                checked={platforms.includes(p.id)}
                onChange={(on) => setPlatforms(on ? [...platforms, p.id] : platforms.filter((x) => x !== p.id))}
                label={p.label}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">Vilka konton som postar väljer du under Ställ in när kampanjen är skapad.</p>
          {shown("platforms") && <p role="alert" className="text-xs text-bad">{problems.platforms}</p>}
        </Section>

        <div className="space-y-3 py-7">
          {submitted && all.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" aria-label="Det som saknas">
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
                    Öppna kampanjen
                  </Link>
                </>
              )}
            </p>
          )}
          <Button type="submit" className="h-10 px-5" disabled={busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            {busy ? "Skapar kampanjen…" : "Skapa kampanj"}
          </Button>
        </div>
      </form>
    </main>
  );
}

/** Tal från adressen, med komma som i resten av appen. */
export const fromQuery = (value: string | undefined) => (value && /^\d+(\.\d+)?$/.test(value) ? numberText(Number(value)) : undefined);
