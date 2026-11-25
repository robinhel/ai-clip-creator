"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ExternalLink, Eye, EyeOff, Loader2, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScoutCountryCard } from "@/components/ScoutCountryCard";
import { TikTokCard } from "@/components/TikTokCard";
import { YouTubeCard } from "@/components/YouTubeCard";
import { api } from "@/lib/api";
import { DEMO, DEMO_OFF } from "@/lib/demo";
import { ENGINES, LOCAL_MODELS, transcriptionNote } from "@/lib/transcription";
import { cn } from "@/lib/utils";
import type {
  ConnectedAccount,
  ConnectedAccounts,
  KeyGroup,
  KeyTest,
  LocalWhisperModel,
  SetupCheck,
  TranscribeEngine,
  TranscriptionSetting,
} from "@/lib/types";

function KeyField({ name, label, set, value, onChange, disabled }: {
  name: string;
  label: string;
  set: boolean;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const [show, setShow] = useState(false);
  const id = `key-${name}`;
  return (
    <div data-key={name} className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="text-[13px] text-muted-foreground">
          {label}
        </label>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
            set ? "border-[#174a2a] text-good" : "border-[#4d1d1d] text-bad"
          )}
        >
          {set ? <CheckCircle2 className="size-3" aria-hidden /> : <XCircle className="size-3" aria-hidden />}
          {set ? "Saved" : "Missing"}
        </span>
      </div>
      <div className="flex items-center rounded-lg border border-line bg-background focus-within:border-brand">
        <input
          id={id}
          type={show ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          placeholder={set ? "Paste to replace" : "Paste here"}
          className="h-9 min-w-0 flex-1 bg-transparent px-2.5 font-mono text-sm outline-none placeholder:font-sans placeholder:text-muted-foreground"
        />
        <button
          type="button"
          aria-label={`${show ? "Hide" : "Show"} ${label}`}
          aria-pressed={show}
          onClick={() => setShow(!show)}
          className="grid h-9 w-9 place-items-center text-muted-foreground hover:text-foreground"
        >
          {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
        </button>
      </div>
    </div>
  );
}

function TestResult({ test }: { test: KeyTest }) {
  return test.ok ? (
    <p role="status" className="flex items-center gap-2 text-sm text-good">
      <CheckCircle2 className="size-4 shrink-0" aria-hidden />
      The keys work.
    </p>
  ) : (
    <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      {test.message}
    </p>
  );
}

const TWITCH_STEPS = [
  "Create an app with the redirect address http://localhost and the type Confidential.",
  "Copy the Client ID.",
  "Press New Secret and copy it.",
];

/** Ett kort per grupp. Värdena finns bara i fälten tills de skickas, och fälten töms efter sparandet. */
function GroupCard({ group, onSaved, children }: { group: KeyGroup; onSaved: (groups: KeyGroup[]) => void; children?: React.ReactNode }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<KeyTest | null>(null);
  const filled = Object.fromEntries(
    Object.entries(values).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()])
  );
  const twitch = group.id === "twitch";
  const allSet = group.keys.every((k) => k.set);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.campaign.keys.save(filled);
      setValues({});
      if (result.test) setTest(result.test);
      onSaved(result.groups);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the keys");
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setError(null);
    try {
      setTest(await api.campaign.keys.testTwitch());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not test the keys");
    } finally {
      setTesting(false);
    }
  }

  return (
    // id: Kontroll och kampanjsidan länkar hit, till exempel /settings#anthropic.
    <section id={group.id} aria-label={group.label} className="scroll-mt-24 rounded-2xl border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">{group.label}</h2>
      {twitch && (
        <div className="mt-2 text-sm text-muted-foreground">
          <p>
            Create the keys at{" "}
            <a
              href="https://dev.twitch.tv/console/apps"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-foreground underline underline-offset-4"
            >
              dev.twitch.tv/console/apps
              <ExternalLink className="size-3.5" aria-hidden />
            </a>
            .
          </p>
          <ol className="mt-1.5 list-decimal space-y-0.5 pl-5">
            {TWITCH_STEPS.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
      <div className="mt-4 space-y-3">
        {group.keys.map((key) => (
          <KeyField
            key={key.name}
            name={key.name}
            label={key.label}
            set={key.set}
            value={values[key.name] ?? ""}
            onChange={(v) => setValues((prev) => ({ ...prev, [key.name]: v }))}
            disabled={DEMO || busy}
          />
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="h-9" disabled={DEMO || busy || Object.keys(filled).length === 0} onClick={save} title={DEMO ? DEMO_OFF : undefined}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          {busy ? "Saving…" : "Save"}
        </Button>
        {twitch && allSet && (
          <Button variant="outline" className="h-9" disabled={DEMO || busy || testing} onClick={runTest} title={DEMO ? DEMO_OFF : undefined}>
            {testing && <Loader2 className="animate-spin" aria-hidden />}
            Test the keys
          </Button>
        )}
      </div>
      <div className="mt-3 space-y-2 empty:hidden">
        {error && (
          <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        {twitch && test && <TestResult test={test} />}
      </div>
      {children}
    </section>
  );
}

/** Samma kontroller som python -m campaign check-setup, i serverns ordning. */
function SetupCard({ checks, error, loading, onRun }: {
  checks: SetupCheck[] | null;
  error: string | null;
  loading: boolean;
  onRun: () => void;
}) {
  const failing = checks?.filter((c) => !c.ok).length ?? 0;
  return (
    <section aria-label="Check" className="mt-4 rounded-2xl border border-line bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Check</h2>
          <p className={cn("text-sm", !checks ? "text-muted-foreground" : failing ? "text-bad" : "text-good")}>
            {!checks
              ? loading
                ? "Checking…"
                : "The check could not run."
              : failing === 0
                ? "Everything is ready. You can get clips."
                : `${failing} ${failing === 1 ? "thing needs" : "things need"} fixing.`}
          </p>
        </div>
        <Button variant="outline" className="h-9" disabled={loading} onClick={onRun}>
          {loading ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
          Run the check again
        </Button>
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {checks && (
        <ul className="mt-4 grid gap-x-6 gap-y-3 md:grid-cols-2">
          {checks.map((c) => (
            <li key={c.id} data-check={c.id} data-ok={c.ok} className="flex gap-2.5">
              {c.ok ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-label="Works" />
              ) : (
                <XCircle className="mt-0.5 size-4 shrink-0 text-bad" aria-label="Needs fixing" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  <CheckLabel check={c} />
                </p>
                <p className={cn("text-[13px] break-words", c.ok ? "text-muted-foreground" : "text-[#fca5a5]")}>{c.detail}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Raden länkar till stället där den rättas: en kampanj eller ett kort längre ner. */
function CheckLabel({ check }: { check: SetupCheck }) {
  if (!check.href) return <>{check.label}</>;
  return (
    <Link href={check.href} className="underline underline-offset-4 hover:text-brand">
      {check.label}
    </Link>
  );
}

const ACCOUNT_GROUPS = [
  { id: "tiktok", label: "TikTok", empty: "No TikTok accounts are connected." },
  { id: "youtube_shorts", label: "YouTube", empty: "No YouTube channels are connected.", anchor: "youtube" },
] as const;

/** Alla kopplade konton på ett ställe: vilka som måste logga in igen och vilka kampanjer som har valt dem. */
function AccountsCard({ refresh }: { refresh: string }) {
  const [accounts, setAccounts] = useState<ConnectedAccounts | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.campaign
      .accounts()
      .then(setAccounts)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the accounts"));
  }, [refresh]);

  const expired = accounts ? [...accounts.tiktok, ...accounts.youtube_shorts].filter((a) => !a.connected).length : 0;
  return (
    <section id="konton" aria-label="Accounts" className="mt-7 scroll-mt-24 rounded-2xl border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">Accounts</h2>
      <p className="mt-0.5 max-w-[640px] text-sm text-muted-foreground">
        The accounts the app posts from. You choose which accounts a campaign may use under the campaign's Set up.
      </p>
      {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
      {!accounts && !error && <p className="mt-3 text-sm text-muted-foreground">Loading the accounts…</p>}
      {expired > 0 && (
        <p className="mt-3 text-sm text-warn">
          {expired === 1 ? "One account" : `${expired} accounts`} must log in again.
        </p>
      )}
      {accounts && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {ACCOUNT_GROUPS.map((g) => (
            <AccountGroup key={g.id} group={g} list={accounts[g.id]} />
          ))}
        </div>
      )}
    </section>
  );
}

function AccountGroup({ group, list }: { group: (typeof ACCOUNT_GROUPS)[number]; list: ConnectedAccount[] }) {
  const anchor = "anchor" in group ? group.anchor : group.id;
  return (
    <div data-accounts={group.id}>
      <h3 className="text-[13px] text-muted-foreground">{group.label}</h3>
      {list.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">
          {group.empty}{" "}
          <a href={`#${anchor}`} className="font-medium text-foreground underline underline-offset-4">
            Connect one further down
          </a>
        </p>
      ) : (
        <ul className="mt-1 divide-y divide-line">
          {list.map((a) => (
            <AccountRow key={a.id} account={a} anchor={anchor} />
          ))}
        </ul>
      )}
    </div>
  );
}

function AccountRow({ account: a, anchor }: { account: ConnectedAccount; anchor: string }) {
  return (
    <li data-account={a.id} className="py-2 text-sm">
      <p className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="font-medium">{a.name}</span>
        {a.connected ? (
          <span className="text-xs text-good">Logged in</span>
        ) : (
          <a href={`#${anchor}`} className="text-xs font-medium text-bad underline underline-offset-4">
            Log in again
          </a>
        )}
      </p>
      <p className="text-xs text-muted-foreground">
        Used by:{" "}
        {a.used_by.length === 0
          ? "all campaigns that have not chosen their own accounts"
          : a.used_by.map((c, i) => (
              <span key={c.name}>
                {i > 0 && ", "}
                <Link href={`/campaigns/${c.name}?tab=installningar&del=oversikt#konton`} className="underline underline-offset-4 hover:text-foreground">
                  {c.streamer || c.name}
                </Link>
              </span>
            ))}
      </p>
    </li>
  );
}

/** Vem som transkriberar: Berget eller en lokal Whisper-modell. Sparas direkt; vid fel står valet kvar som innan.
 * I demoläget sparas valet bara i minnet och inget laddas ner. */
function TranscriptionCard({ onSaved }: { onSaved: () => void }) {
  const [setting, setSetting] = useState<TranscriptionSetting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaign.transcription
      .get()
      .then(setSetting)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the setting"));
  }, []);

  async function save(change: { engine?: TranscribeEngine; model?: LocalWhisperModel }) {
    setBusy(true);
    setError(null);
    try {
      setSetting(await api.campaign.transcription.save(change));
      onSaved(); // Kontroll visar valet
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the setting");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="transcription" aria-label="Transcription" className="mt-4 scroll-mt-24 rounded-2xl border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">Transcription</h2>
      <p className="mt-0.5 max-w-[640px] text-sm text-muted-foreground">
        Who turns the speech into text. Berget runs in the cloud and needs a key. Local runs a Whisper model on this computer for free.
      </p>
      {!setting && !error && <p className="mt-3 text-sm text-muted-foreground">Loading the setting…</p>}
      {setting && (
        <>
          <div role="radiogroup" aria-label="Transcription" className="mt-4 inline-flex rounded-lg border border-line bg-background p-1">
            {ENGINES.map((e) => {
              const on = setting.engine === e.id;
              return (
                <button
                  key={e.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  data-engine={e.id}
                  disabled={busy}
                  onClick={() => !on && save({ engine: e.id })}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-left text-sm transition-colors disabled:opacity-60",
                    on ? "bg-brand text-white" : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <span className="font-medium">{e.label}</span> <span className={on ? "text-white/80" : ""}>({e.hint})</span>
                </button>
              );
            })}
          </div>
          {setting.engine === "local" && (
            <div className="mt-4 max-w-[640px] space-y-2">
              <label htmlFor="local-model" className="text-[13px] text-muted-foreground">
                Local model
              </label>
              <select
                id="local-model"
                value={setting.model}
                disabled={busy}
                onChange={(e) => save({ model: e.target.value as LocalWhisperModel })}
                className="block h-9 rounded-lg border border-line bg-background px-2.5 text-sm outline-none focus:border-brand"
              >
                {LOCAL_MODELS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id} (about {m.size_gb} GB)
                  </option>
                ))}
              </select>
              <p className="text-sm text-muted-foreground">
                The model is downloaded the first time it is used. With small, a 4 h VOD takes about 30–90 minutes on an ordinary CPU;
                medium and large-v3 are more accurate but slower.
              </p>
            </div>
          )}
          <p
            data-note
            className={cn(
              "mt-3 max-w-[640px] text-sm",
              setting.engine === "berget" && !setting.berget_key
                ? "text-[#fca5a5]"
                : setting.engine === "local" && !setting.downloaded
                  ? "text-warn"
                  : "text-muted-foreground"
            )}
          >
            {transcriptionNote(setting)}
            {DEMO && " Demo mode: the choice is only kept in this tab, and nothing is downloaded."}
          </p>
        </>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </section>
  );
}

/** Hjälpen att skicka in i Content Rewards. Sparas direkt; vid fel står switchen kvar som innan. */
function SubmitHelperCard() {
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaign.preferences
      .get()
      .then((p) => setOn(p.submit_helper))
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the setting"));
  }, []);

  async function toggle() {
    if (on === null) return;
    setBusy(true);
    setError(null);
    try {
      setOn((await api.campaign.preferences.save({ submit_helper: !on })).submit_helper);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the setting");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Content Rewards" className="mt-4 rounded-2xl border border-line bg-panel p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="submit-helper" className="text-lg font-semibold">
            Submit help in Content Rewards
          </h2>
          <p className="mt-0.5 max-w-[640px] text-sm text-muted-foreground">
            The app adds the link when a clip has been uploaded, copies it and opens the campaign. You press Submit yourself.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={!!on}
          aria-labelledby="submit-helper"
          disabled={on === null || busy}
          onClick={toggle}
          className="mt-1 shrink-0 disabled:opacity-60"
        >
          <span className={cn("flex h-6 w-11 items-center rounded-full border px-0.5 transition-colors", on ? "border-brand bg-brand" : "border-line bg-[#1c1c22]")}>
            <span className={cn("size-4.5 rounded-full bg-white transition-transform", on && "translate-x-5")} />
          </span>
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
    </section>
  );
}

/** tiktokResult och tiktokReason: ?tiktok= och ?orsak= från serverns callback efter inloggningen på TikTok. */
export function SettingsClient({ tiktokResult, tiktokReason }: { tiktokResult?: string; tiktokReason?: string }) {
  const [groups, setGroups] = useState<KeyGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<SetupCheck[] | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupLoading, setSetupLoading] = useState(true);

  function runSetup() {
    setSetupLoading(true);
    setSetupError(null);
    api.campaign
      .setup()
      .then(({ checks }) => setChecks(checks))
      .catch((err) => setSetupError(err instanceof Error ? err.message : "Could not run the check"))
      .finally(() => setSetupLoading(false));
  }

  useEffect(() => {
    api.campaign.keys
      .list()
      .then(({ groups }) => setGroups(groups))
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the keys"));
    runSetup();
  }, []);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
      <p className="mt-1.5 max-w-[640px] text-muted-foreground">
        Paste the keys here. The server saves them in .env and uses them right away. They are never shown again, only whether they are
        saved.
      </p>
      {DEMO && (
        <p role="note" className="mt-4 max-w-[640px] rounded-lg border border-[#4a3b10] bg-[#1d170a] px-3 py-2 text-sm text-warn">
          Demo mode: saving keys and connecting TikTok or YouTube need the app server, so those buttons are turned off.
        </p>
      )}
      <AccountsCard refresh={groups?.map((g) => g.keys.map((k) => k.set).join()).join() ?? ""} />
      <SetupCard checks={checks} error={setupError} loading={setupLoading} onRun={runSetup} />
      <TranscriptionCard onSaved={runSetup} />
      <SubmitHelperCard />
      <ScoutCountryCard />
      {error && (
        <p role="alert" className="mt-6 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {!groups && !error && <p className="mt-6 text-sm text-muted-foreground">Loading the keys…</p>}
      {groups && (
        <div className="mt-4 grid items-start gap-4 lg:grid-cols-2">
          {groups.map((group) => (
            <GroupCard
              key={group.id}
              group={group}
              onSaved={(next) => {
                setGroups(next);
                runSetup(); // nycklarna är en del av kontrollen
              }}
            >
              {group.id === "tiktok" && (
                <TikTokCard
                  result={tiktokResult}
                  reason={tiktokReason}
                  refresh={group.keys.map((k) => k.set).join()} // läs om läget när nycklarna sparas
                />
              )}
              {group.id === "youtube" && <YouTubeCard refresh={group.keys.map((k) => k.set).join()} />}
            </GroupCard>
          ))}
        </div>
      )}
    </main>
  );
}
