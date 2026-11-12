"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfileCreate, OverlayMode, TextPosition } from "@/lib/types";

const EXAMPLE_TITLE = "Monster Kill Then Total Chaos";

// Fält vars standardvärde bygger på kanalen. De följer kanalen tills de ändras för hand.
const fromChannel = (c: string) => ({
  onScreenText: c ? `twitch.tv/${c}` : "",
  template: c ? `{title} 👉 twitch.tv/${c}` : "{title}",
  mustInclude: c ? [`twitch.tv/${c}`] : [],
  hashtags: c ? [`#${c}`, "#twitchclips", "#gaming"] : ["#twitchclips", "#gaming"],
});

/** Samma regel som servern (campaign/caption.py): mall, det som måste finnas, sedan hashtags. */
export function previewCaption(template: string, mustInclude: string[], hashtags: string[]): string {
  let text = template.replaceAll("{title}", EXAMPLE_TITLE);
  for (const must of mustInclude) if (!text.toLowerCase().includes(must.toLowerCase())) text = `${text} ${must}`;
  const seen = new Set<string>();
  const tags = hashtags.filter((t) => !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase()));
  return `${text} ${tags.join(" ")}`.trim();
}

const field =
  "h-9 w-full rounded-lg border border-line bg-background px-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-brand";

function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-1", className)}>
      <label className="block space-y-1">
        <span className="block text-[13px] text-muted-foreground">{label}</span>
        {children}
      </label>
      {/* Utanför etiketten, så att fältets namn bara är etiketten. */}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Section({ step, title, children }: { step: number; title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-4 border-b border-line py-7 md:grid-cols-[200px_minmax(0,1fr)]">
      <h2 className="flex items-baseline gap-2.5 font-semibold">
        <span className="grid size-6 shrink-0 place-items-center rounded-full bg-line text-xs text-muted-foreground tabular-nums">
          {step}
        </span>
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 size-4 accent-[#7c5cff]" />
      <span>
        {label}
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
}

function Chips({ label, values, onChange, prefix = "", placeholder }: {
  label: string;
  values: string[];
  onChange: (v: string[]) => void;
  prefix?: string;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const words = draft.split(/[\s,]+/).filter(Boolean).map((w) => (prefix && !w.startsWith(prefix) ? prefix + w : w));
    const next = [...values];
    for (const w of words) if (!next.some((v) => v.toLowerCase() === w.toLowerCase())) next.push(w);
    onChange(next);
    setDraft("");
  }
  return (
    <div className="space-y-1">
      <span className="block text-[13px] text-muted-foreground">{label}</span>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-line bg-background px-2 py-1.5 focus-within:border-brand">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-full bg-line py-0.5 pr-1 pl-2.5 text-xs">
            {v}
            <button type="button" aria-label={`Ta bort ${v}`} onClick={() => onChange(values.filter((x) => x !== v))} className="rounded-full p-0.5 text-muted-foreground hover:text-foreground">
              <X className="size-3" aria-hidden />
            </button>
          </span>
        ))}
        <input
          aria-label={label}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === ",") && draft.trim()) {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={() => draft.trim() && add()}
          placeholder={placeholder}
          className="min-w-32 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
    </div>
  );
}

export function NewCampaignClient({ channel: initialChannel, streamer: initialStreamer }: { channel: string; streamer: string }) {
  const router = useRouter();
  const start = initialChannel.trim().toLowerCase();
  const [channel, setChannelValue] = useState(start);
  const [streamer, setStreamer] = useState(initialStreamer);
  const [campaign, setCampaign] = useState("");
  const [language, setLanguage] = useState("en");
  const [minS, setMinS] = useState("15");
  const [maxS, setMaxS] = useState("45");
  const [onScreenText, setOnScreenText] = useState(fromChannel(start).onScreenText);
  const [textPosition, setTextPosition] = useState<TextPosition>("top");
  const [overlayRequired, setOverlayRequired] = useState(true);
  const [overlayMode, setOverlayMode] = useState<OverlayMode>("full");
  const [facecamRequired, setFacecamRequired] = useState(true);
  const [template, setTemplate] = useState(fromChannel(start).template);
  const [mustInclude, setMustInclude] = useState(fromChannel(start).mustInclude);
  const [hashtags, setHashtags] = useState(fromChannel(start).hashtags);
  const [formats, setFormats] = useState([
    { name: "Funny moments and fails", description: "Bad deaths, rage and fails. The reaction is the clip." },
    { name: "Clutch plays", description: "Multi kills and last-second wins. Open on the payoff." },
    { name: "Genuine reactions", description: "Jump scares, disbelief and hype, with the facecam large." },
  ]);
  const [cpm, setCpm] = useState("1");
  const [minViews, setMinViews] = useState("2000");
  const [maxPayout, setMaxPayout] = useState("150");
  const [engagement, setEngagement] = useState("0.8");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setChannel(value: string) {
    const next = value.trim().toLowerCase();
    const before = fromChannel(channel);
    const after = fromChannel(next);
    if (onScreenText === before.onScreenText) setOnScreenText(after.onScreenText);
    if (template === before.template) setTemplate(after.template);
    if (JSON.stringify(mustInclude) === JSON.stringify(before.mustInclude)) setMustInclude(after.mustInclude);
    if (JSON.stringify(hashtags) === JSON.stringify(before.hashtags)) setHashtags(after.hashtags);
    setChannelValue(next);
  }

  const numbers = { minS: Number(minS), maxS: Number(maxS), cpm: Number(cpm), minViews: Number(minViews), maxPayout: Number(maxPayout), engagement: Number(engagement) };
  const problems: string[] = [];
  // Samma regel som servern (campaign/profile_edit.py ID): kanalen blir kampanjens filnamn.
  if (!/^[a-z0-9][a-z0-9_]{1,39}$/.test(channel)) {
    problems.push("Skriv kanalens namn som i länken twitch.tv/<kanal>: 2–40 små bokstäver, siffror och _, och inte _ först.");
  }
  if (!streamer.trim()) problems.push("Skriv streamerns namn.");
  const wholes = [["Kortast (s)", minS, "15"], ["Längst (s)", maxS, "45"], ["Minsta visningar", minViews, "2000"]];
  for (const [label, value, example] of wholes) {
    if (!/^\d+$/.test(value.trim())) problems.push(`${label}: skriv ett helt tal utan decimaler, till exempel ${example}.`);
  }
  if (/^\d+$/.test(minS.trim()) && /^\d+$/.test(maxS.trim()) && (numbers.minS < 1 || numbers.minS >= numbers.maxS)) {
    problems.push("Klipplängden: kortast måste vara minst 1 och mindre än längst.");
  }
  const texts: [string, string][] = [
    ["Streamerns namn", streamer],
    ["Kampanjens namn", campaign],
    ["Språk", language],
    ["Text på skärmen", onScreenText],
    ["Caption-mallen", template],
    ...mustInclude.map((v): [string, string] => ["Måste innehålla", v]),
    ...hashtags.map((v): [string, string] => ["Hashtags", v]),
    ...formats.flatMap((f, i): [string, string][] => [[`Format ${i + 1}`, f.name], [`Format ${i + 1}`, f.description]]),
  ];
  // Fälten är en rad; en radbrytning kan komma in via adressen (?streamer=) och servern skriver texten i YAML-filen.
  for (const label of new Set(texts.filter(([, v]) => /[\r\n]/.test(v)).map(([l]) => l))) {
    problems.push(`${label}: ta bort radbrytningen. Texten ska stå på en rad.`);
  }
  if (!template.includes("{title}")) problems.push("Caption-mallen måste innehålla {title}, där klippets rubrik hamnar.");
  if ([numbers.cpm, numbers.minViews, numbers.maxPayout, numbers.engagement].some((n) => !Number.isFinite(n) || n < 0)) {
    problems.push("Betalningen ska vara tal som är 0 eller större.");
  }
  if (formats.some((f) => !f.name.trim())) problems.push("Ge varje format ett namn, eller ta bort raden.");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) return;
    setBusy(true);
    setError(null);
    const body: CampaignProfileCreate = {
      channel,
      streamer: streamer.trim(),
      campaign: campaign.trim() || undefined,
      language: language.trim() || undefined,
      clip_min_s: numbers.minS,
      clip_max_s: numbers.maxS,
      on_screen_text: onScreenText.trim(),
      text_position: textPosition,
      overlay_required: overlayRequired,
      overlay_mode: overlayMode,
      facecam_required: facecamRequired,
      must_include: mustInclude,
      hashtags,
      caption_template: template,
      formats: formats.map((f) => ({ name: f.name.trim(), description: f.description.trim() })),
      cpm_usd: numbers.cpm,
      min_views: numbers.minViews,
      max_payout_per_clip_usd: numbers.maxPayout,
      engagement_min_percent: numbers.engagement,
    };
    try {
      const created = await api.campaign.profile.create(body);
      router.push(`/campaigns/${created.name}?tab=overlay`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att skapa kampanjen");
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
        Fyll i det kampanjsidan kräver. Allt utom kanal och namn har förval. Overlay-bilden och facecam ställer du in i nästa steg.
      </p>

      <form onSubmit={submit} noValidate>
        <Section step={1} title="Streamer">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Twitch-kanal" hint={channel ? `twitch.tv/${channel}` : "Det som står efter twitch.tv/"}>
              <input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="skiimasktimmy" className={field} autoFocus={!start} />
            </Field>
            <Field label="Streamerns namn">
              <input value={streamer} onChange={(e) => setStreamer(e.target.value)} placeholder="SkiiMaskTimmy" className={field} />
            </Field>
            <Field label="Kampanjens namn" hint="Som det står på kampanjsidan. Tomt blir streamerns namn.">
              <input value={campaign} onChange={(e) => setCampaign(e.target.value)} placeholder="$2,500 Streamer Campaign" className={field} />
            </Field>
            <Field label="Språk i streamen" hint="Språkkod, till exempel en eller sv.">
              <input value={language} onChange={(e) => setLanguage(e.target.value)} className={field} />
            </Field>
          </div>
        </Section>

        <Section step={2} title="Klipplängd">
          <div className="flex items-end gap-3">
            <Field label="Kortast (s)" className="w-28">
              <input value={minS} onChange={(e) => setMinS(e.target.value)} type="number" min="1" step="1" inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Längst (s)" className="w-28">
              <input value={maxS} onChange={(e) => setMaxS(e.target.value)} type="number" min="1" step="1" inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <Section step={3} title="Bilden">
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

        <Section step={4} title="Caption">
          <Field label="Mall" hint="{title} byts mot klippets rubrik.">
            <input value={template} onChange={(e) => setTemplate(e.target.value)} className={field} />
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

        <Section step={5} title="Format">
          <p className="text-sm text-muted-foreground">Claude väljer bara klipp som passar något av formaten. Skriv på engelska, som kampanjsidan.</p>
          <ul className="space-y-2">
            {formats.map((f, i) => (
              <li key={i} className="grid items-start gap-2 sm:grid-cols-[220px_minmax(0,1fr)_auto]">
                <input
                  aria-label={`Format ${i + 1}, namn`}
                  value={f.name}
                  onChange={(e) => setFormats(formats.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                  placeholder="Namn"
                  className={field}
                />
                <input
                  aria-label={`Format ${i + 1}, beskrivning`}
                  value={f.description}
                  onChange={(e) => setFormats(formats.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))}
                  placeholder="Vad som gör ett bra klipp i formatet"
                  className={field}
                />
                <Button type="button" variant="ghost" className="h-9 text-muted-foreground" aria-label={`Ta bort format ${i + 1}`} onClick={() => setFormats(formats.filter((_, j) => j !== i))}>
                  <Trash2 aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
          <Button type="button" variant="outline" className="h-9" onClick={() => setFormats([...formats, { name: "", description: "" }])}>
            <Plus aria-hidden />
            Lägg till format
          </Button>
        </Section>

        <Section step={6} title="Betalning">
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="US$ per 1 000 visningar">
              <input value={cpm} onChange={(e) => setCpm(e.target.value)} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minsta visningar">
              <input value={minViews} onChange={(e) => setMinViews(e.target.value)} type="number" min="0" step="1" inputMode="numeric" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Tak per klipp (US$)">
              <input value={maxPayout} onChange={(e) => setMaxPayout(e.target.value)} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
            <Field label="Minsta engagemang (%)">
              <input value={engagement} onChange={(e) => setEngagement(e.target.value)} inputMode="decimal" className={cn(field, "tabular-nums")} />
            </Field>
          </div>
        </Section>

        <div className="space-y-3 py-7">
          {problems.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" aria-label="Det som saknas">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
              {error}
            </p>
          )}
          <Button type="submit" className="h-10 px-5" disabled={busy || problems.length > 0}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            {busy ? "Skapar kampanjen…" : "Skapa kampanj"}
          </Button>
        </div>
      </form>
    </main>
  );
}
