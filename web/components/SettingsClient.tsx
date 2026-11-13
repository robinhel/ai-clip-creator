"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, Eye, EyeOff, Loader2, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TikTokCard } from "@/components/TikTokCard";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { KeyGroup, KeyTest, SetupCheck } from "@/lib/types";

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
          {set ? "Sparad" : "Saknas"}
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
          placeholder={set ? "Klistra in för att byta" : "Klistra in här"}
          className="h-9 min-w-0 flex-1 bg-transparent px-2.5 font-mono text-sm outline-none placeholder:font-sans placeholder:text-muted-foreground"
        />
        <button
          type="button"
          aria-label={`${show ? "Dölj" : "Visa"} ${label}`}
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
      Nycklarna fungerar.
    </p>
  ) : (
    <p role="alert" className="flex items-start gap-2 text-sm text-[#fca5a5]">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      {test.message}
    </p>
  );
}

const TWITCH_STEPS = [
  "Skapa en app med redirect-adressen http://localhost och typen Confidential.",
  "Kopiera Client ID.",
  "Tryck på New Secret och kopiera den.",
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
      setError(err instanceof Error ? err.message : "Det gick inte att spara nycklarna");
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
      setError(err instanceof Error ? err.message : "Det gick inte att prova nycklarna");
    } finally {
      setTesting(false);
    }
  }

  return (
    <section aria-label={group.label} className="rounded-2xl border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">{group.label}</h2>
      {twitch && (
        <div className="mt-2 text-sm text-muted-foreground">
          <p>
            Skapa nycklarna på{" "}
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
            disabled={busy}
          />
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="h-9" disabled={busy || Object.keys(filled).length === 0} onClick={save}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          {busy ? "Sparar…" : "Spara"}
        </Button>
        {twitch && allSet && (
          <Button variant="outline" className="h-9" disabled={busy || testing} onClick={runTest}>
            {testing && <Loader2 className="animate-spin" aria-hidden />}
            Prova nycklarna
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
    <section aria-label="Kontroll" className="mt-7 rounded-2xl border border-line bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Kontroll</h2>
          <p className={cn("text-sm", !checks ? "text-muted-foreground" : failing ? "text-bad" : "text-good")}>
            {!checks
              ? loading
                ? "Kontrollerar…"
                : "Kontrollen kunde inte köras."
              : failing === 0
                ? "Allt är klart för en körning."
                : `${failing} ${failing === 1 ? "sak behöver" : "saker behöver"} rättas.`}
          </p>
        </div>
        <Button variant="outline" className="h-9" disabled={loading} onClick={onRun}>
          {loading ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
          Kör kontrollen igen
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
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-label="Fungerar" />
              ) : (
                <XCircle className="mt-0.5 size-4 shrink-0 text-bad" aria-label="Behöver rättas" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium">{c.label}</p>
                <p className={cn("text-[13px] break-words", c.ok ? "text-muted-foreground" : "text-[#fca5a5]")}>{c.detail}</p>
              </div>
            </li>
          ))}
        </ul>
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
      .catch((err) => setSetupError(err instanceof Error ? err.message : "Kunde inte köra kontrollen"))
      .finally(() => setSetupLoading(false));
  }

  useEffect(() => {
    api.campaign.keys
      .list()
      .then(({ groups }) => setGroups(groups))
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte läsa nycklarna"));
    runSetup();
  }, []);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Inställningar</h1>
      <p className="mt-1.5 max-w-[640px] text-muted-foreground">
        Klistra in nycklarna här. Servern sparar dem i .env och använder dem direkt. De visas aldrig igen, bara om de är
        sparade.
      </p>
      <SetupCard checks={checks} error={setupError} loading={setupLoading} onRun={runSetup} />
      {error && (
        <p role="alert" className="mt-6 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {!groups && !error && <p className="mt-6 text-sm text-muted-foreground">Läser nycklarna…</p>}
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
            </GroupCard>
          ))}
        </div>
      )}
    </main>
  );
}
