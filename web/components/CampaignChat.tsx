"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUp, Loader2, Play, Plus, Scissors, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { clock, cn, duration, usd } from "@/lib/utils";
import type { CampaignChat as Chat, CampaignProfileDetail, ChatHit, ChatMessage, ChatSummary, MaterialItem } from "@/lib/types";

const MAX = 1000;
const when = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short" });
const GENERAL_IDEAS = ["Stora reaktioner", "De roligaste ögonblicken", "Ögonblick med mest hype i chatten"];

/** Förslag till en tom chatt: kampanjens format först, sedan allmänna förslag. */
export function chatIdeas(formats: { name: string }[]): string[] {
  const own = formats.map((f) => f.name.trim()).filter(Boolean).slice(0, 3);
  return [...own, ...GENERAL_IDEAS].slice(0, 4);
}

/** Var chattens hinder rättas. Texten kommer från servern (campaign/chat.py, blocker). */
function blockerFix(problem: string, profile: CampaignProfileDetail): { href: string; label: string } | null {
  if (problem.startsWith("Claude-nyckeln")) return { href: "/settings#anthropic", label: "Öppna Inställningar" };
  if (problem.startsWith("Inget material")) {
    return profile.channel
      ? { href: `/campaigns/${profile.name}?tab=material`, label: "Visa kanalens streams" }
      : { href: `/uploads?kampanj=${profile.name}`, label: "Lägg till en egen video" };
  }
  return null;
}

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function Bubble({ msg, last, busy, titleOf, onConfirm }: {
  msg: ChatMessage;
  last: boolean;
  busy: boolean;
  titleOf: (vodId: string) => string;
  onConfirm: () => void;
}) {
  if (msg.role === "user") {
    return (
      <p className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-brand/20 px-3.5 py-2.5 text-sm whitespace-pre-wrap break-words">
        {msg.text}
      </p>
    );
  }
  return (
    <div className="max-w-[92%] space-y-2 rounded-2xl rounded-bl-md border border-line bg-panel px-3.5 py-2.5 text-sm">
      <p className="whitespace-pre-wrap break-words">{msg.text}</p>
      {msg.confirm && (
        <div data-confirm className="space-y-2 rounded-xl border border-warn/40 bg-background p-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
            <dt className="text-muted-foreground">Kostnad</dt>
            <dd className="font-medium tabular-nums">cirka {usd.format(msg.confirm.cost_usd)}</dd>
            <dt className="text-muted-foreground">Material</dt>
            <dd>
              <ul>
                {msg.confirm.sources.map((s) => (
                  <li key={s} className="break-words">{titleOf(s)}</li>
                ))}
              </ul>
            </dd>
            <dt className="text-muted-foreground">Transkript</dt>
            <dd className="tabular-nums">{duration(msg.confirm.minutes * 60)}</dd>
          </dl>
          {last && (
            <Button size="sm" onClick={onConfirm} disabled={busy}>
              <Search aria-hidden />
              Kör sökningen
            </Button>
          )}
        </div>
      )}
      {((msg.sources?.length ?? 0) > 0 || msg.cost_usd != null) && (
        <p className="text-xs text-muted-foreground">
          {(msg.sources?.length ?? 0) > 0 && `Läste: ${msg.sources!.map(titleOf).join(", ")}. `}
          {msg.cost_usd != null && `Kostade ${msg.cost_usd < 0.01 ? "under 1 cent" : usd.format(msg.cost_usd)}.`}
        </p>
      )}
    </div>
  );
}

function Hit({ hit, item, picked, playing, clipHref, onPick, onPlay }: {
  hit: ChatHit;
  clipHref: string;
  item: MaterialItem | undefined;
  picked: boolean;
  playing: boolean;
  onPick: (on: boolean) => void;
  onPlay: () => void;
}) {
  const done = hit.status === "gjord";
  return (
    <li data-hit={hit.id} className={cn("flex gap-3 border-b border-line py-3 last:border-b-0", playing && "bg-brand/5")}>
      {/* Bara nya träffar går att välja: valda görs redan till klipp. */}
      {hit.status !== "ny" ? (
        <span className="w-4 shrink-0" />
      ) : (
        <input
          type="checkbox"
          checked={picked}
          onChange={(e) => onPick(e.target.checked)}
          aria-label={`Välj ${hit.title}`}
          className="mt-1 size-4 shrink-0 accent-[var(--brand,#7c5cff)]"
        />
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start justify-between gap-3">
          <p className="font-semibold leading-snug break-words">{hit.title}</p>
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
              hit.grade >= 8 ? "bg-good/15 text-good" : hit.grade >= 6 ? "bg-warn/15 text-warn" : "bg-line text-muted-foreground"
            )}
          >
            {hit.grade}/10
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          <span className="break-words">{hit.source_title || hit.vod_id}</span>, vid <span className="tabular-nums">{clock(hit.start_s)}</span>,{" "}
          <span className="tabular-nums">{duration(hit.end_s - hit.start_s)}</span>
        </p>
        <p className="text-[13px] text-muted-foreground">{hit.reason}</p>
        {/* Servern skickar hela texten i träffen, upp till 1 000 tecken. */}
        {hit.quote && (
          <p data-quote title={hit.quote} className="line-clamp-3 border-l-2 border-line pl-2 text-[13px] italic break-words">
            ”{hit.quote}”
          </p>
        )}
        {hit.problem && <p className="text-[13px] text-warn">{hit.problem}</p>}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-0.5 text-xs">
          {done && hit.clip_id != null && (
            <Link href={`${clipHref}#klipp-${hit.clip_id}`} className="text-good underline-offset-4 hover:underline">
              Klipp {hit.clip_id}
            </Link>
          )}
          {hit.status === "vald" && <span className="text-warn">Görs nu</span>}
          {item?.has_video ? (
            <button onClick={onPlay} aria-label={`Spela ${hit.title}`} className="inline-flex items-center gap-1 text-brand hover:underline">
              <Play className="size-3" aria-hidden />
              {playing ? "Spelas" : "Spela"}
            </button>
          ) : (
            <span className="text-muted-foreground">
              Ingen video här{item?.kind === "twitch" && !done ? ", delen hämtas från Twitch när klippet görs" : ""}.
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

/** Fliken Skapa klipp: en chatt med Claude som letar i kampanjens transkript, och träffarna som kan bli klipp. */
export function CampaignChat({ profile, material, chatId, onOpen }: {
  profile: CampaignProfileDetail;
  material: MaterialItem[] | null;
  chatId?: number;
  onOpen: (chatId?: number) => void;
}) {
  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [chat, setChat] = useState<Chat | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [playing, setPlaying] = useState<number | null>(null);
  const [making, setMaking] = useState(false);
  const [runs, setRuns] = useState<number[]>([]);
  const log = useRef<HTMLDivElement>(null);

  const loadChats = useCallback(() => {
    api.campaign.chats.list(profile.name).then(setChats).catch(() => setChats([]));
  }, [profile.name]);
  useEffect(loadChats, [loadChats]);

  useEffect(() => {
    setPicked([]);
    setPlaying(null);
    setRuns([]);
    setError(null);
    if (!chatId) {
      setChat(null);
      return;
    }
    let live = true;
    api.campaign.chats
      .get(chatId)
      .then((c) => live && setChat(c))
      .catch((err) => live && setError(message(err, "Kunde inte hämta chatten")));
    return () => {
      live = false;
    };
  }, [chatId]);

  // Senaste meddelandet syns när chatten rullar (på bred skärm).
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [chat, pending, busy]);

  const byVod = new Map(material?.map((m) => [m.vod_id, m]));
  const titleOf = (vodId: string) => byVod.get(vodId)?.title || vodId;
  const scope = chat?.default_scope ?? profile.search_default_scope ?? "latest";
  const withTranscript = material?.filter((m) => m.has_transcript).length;
  const where =
    scope === "latest"
      ? "senaste streamen"
      : `allt material${withTranscript != null ? ` (${withTranscript} ${withTranscript === 1 ? "video" : "videor"})` : ""}`;

  const text = draft.trim();
  const tooLong = draft.length > MAX;
  // Utan nyckel eller transkript svarar servern 400 innan Claude anropas. Rutan är låst tills dess.
  const blocked = profile.chat_problem ?? null;
  const fix = blocked ? blockerFix(blocked, profile) : null;
  const canSend = !!text && !tooLong && !busy && !blocked;
  const ideas = chatIdeas(profile.formats);

  async function send(body: { message: string } | { confirm: true }) {
    const typed = draft;
    setBusy(true);
    setError(null);
    if ("message" in body) {
      setPending(body.message);
      setDraft("");
    }
    try {
      const next = chat
        ? await api.campaign.chats.send(chat.id, body)
        : await api.campaign.chats.create(profile.name, "message" in body ? body.message : "");
      setChat(next);
      if (next.id !== chatId) onOpen(next.id);
      loadChats();
    } catch (err) {
      setError(message(err, "Claude svarade inte"));
      if ("message" in body) setDraft(typed);
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  async function makeClips() {
    if (!chat || picked.length === 0) return;
    setMaking(true);
    setError(null);
    try {
      const { run_id, run_ids } = await api.campaign.chats.makeClips(chat.id, picked);
      setRuns(run_ids?.length ? run_ids : [run_id]);
      setPicked([]);
      api.campaign.chats.get(chat.id).then(setChat).catch(() => {});
    } catch (err) {
      setError(message(err, "Kunde inte göra klippen"));
    } finally {
      setMaking(false);
    }
  }

  const hits = chat?.hits ?? [];
  const playable = hits.filter((h) => byVod.get(h.vod_id)?.has_video);
  const active = playable.find((h) => h.id === playing) ?? playable[0];
  const messages = chat?.messages ?? [];

  return (
    <div className="grid gap-5 lg:grid-cols-[190px_minmax(0,1fr)] xl:grid-cols-[190px_minmax(0,1fr)_400px]">
      <nav aria-label="Chattar" className="flex min-w-0 gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
        <Button variant="outline" className="shrink-0 justify-start" onClick={() => onOpen(undefined)} disabled={busy}>
          <Plus aria-hidden />
          Ny chatt
        </Button>
        {chats?.map((c) => (
          <button
            key={c.id}
            onClick={() => onOpen(c.id)}
            disabled={busy}
            aria-current={c.id === chatId ? "true" : undefined}
            className={cn(
              "w-[200px] shrink-0 rounded-lg px-3 py-2 text-left text-sm lg:w-auto",
              c.id === chatId ? "bg-panel text-foreground" : "text-muted-foreground hover:bg-panel/60 hover:text-foreground"
            )}
          >
            <span className="block truncate">{c.title}</span>
            <span className="block text-[11px] text-muted-foreground">
              {when.format(new Date(c.updated))}
              {c.hits > 0 && `, ${c.hits} träffar`}
            </span>
          </button>
        ))}
      </nav>

      {/* Fast höjd på bred skärm, så att rutan står kvar längst ner och samtalet rullar. */}
      <section aria-label="Chatten" className="flex min-w-0 flex-col rounded-2xl border border-line bg-panel/40 lg:h-[640px] lg:self-start">
        <div ref={log} className="flex min-h-[280px] flex-1 flex-col gap-3 p-4 lg:min-h-0 lg:overflow-y-auto">
          {messages.length === 0 && !pending && (
            <div className="my-auto space-y-3 py-6 text-center">
              <p className="text-lg font-semibold">Vad letar du efter?</p>
              <p className="mx-auto max-w-[44ch] text-sm text-muted-foreground">
                Claude letar i kampanjens transkript och svarar med träffar. Fortsätt prata för att ändra dem.
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {ideas.map((idea) => (
                  <button key={idea} onClick={() => setDraft(idea)} disabled={!!blocked} className="rounded-full disabled:opacity-50 border border-line px-3 py-1 text-xs text-muted-foreground hover:border-muted-foreground hover:text-foreground">
                    {idea}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) => (
            <Bubble key={i} msg={msg} last={i === messages.length - 1} busy={busy || !!blocked} titleOf={titleOf} onConfirm={() => send({ confirm: true })} />
          ))}
          {pending && <Bubble msg={{ role: "user", text: pending, created: "" }} last={false} busy titleOf={titleOf} onConfirm={() => {}} />}
          {busy && (
            <div role="status" className="flex items-start gap-2 text-sm text-muted-foreground">
              <Loader2 className="mt-0.5 size-4 animate-spin" aria-hidden />
              <div>
                <p className="text-foreground">Claude letar…</p>
                <p className="text-xs">En sökning kan ta en minut.</p>
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-line p-3">
          {blocked && (
            <p role="status" className="mb-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-sm text-warn">
              {blocked}
              {fix && (
                <>
                  {" "}
                  <Link href={fix.href} className="font-medium underline underline-offset-4">
                    {fix.label}
                  </Link>
                </>
              )}
            </p>
          )}
          <p className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Search className="size-3.5" aria-hidden />
            <span>Letar i: {where}</span>
          </p>
          <div className="flex items-end gap-2 rounded-xl border border-line bg-background p-1.5 focus-within:border-brand">
            <textarea
              aria-label="Vad vill du ha klipp på?"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={!!blocked}
              onKeyDown={(e) => {
                if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
                // På en telefon ger Enter en ny rad. Där skickar knappen.
                if (window.matchMedia("(pointer: coarse)").matches) return;
                e.preventDefault();
                if (canSend) send({ message: text });
              }}
              rows={2}
              placeholder={chat ? "Skriv till exempel ”fler sådana” eller ”kortare”" : "Till exempel: rage-ögonblick i senaste streamen"}
              className="max-h-40 min-h-11 flex-1 resize-y bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
            />
            <Button size="icon" aria-label="Skicka" disabled={!canSend} onClick={() => send({ message: text })}>
              <ArrowUp aria-hidden />
            </Button>
          </div>
          <p className={cn("mt-1.5 text-[11px]", tooLong ? "text-bad" : "text-muted-foreground")}>
            {tooLong ? (
              `Högst 1 000 tecken. Nu ${draft.length}.`
            ) : (
              <>
                <span className="hidden pointer-fine:inline">Enter skickar, Shift+Enter ger ny rad. </span>
                Säg var Claude ska leta om du vill något annat.
              </>
            )}
          </p>
          {error && (
            <p role="alert" className="mt-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
              {error}
            </p>
          )}
        </div>
      </section>

      <section aria-label="Träffar" className="min-w-0 space-y-3 lg:col-start-2 xl:col-start-auto">
        <h2 className="flex items-baseline justify-between gap-3 font-semibold">
          Träffar <span className="text-sm font-normal text-muted-foreground tabular-nums">{hits.length}</span>
        </h2>
        {hits.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted-foreground">
            Träffarna visas här när Claude har letat.
          </p>
        ) : (
          <>
            {active && (
              <figure className="space-y-1.5">
                <video
                  key={`${active.id}-${active.start_s}-${active.end_s}`}
                  src={`${api.campaign.materialVideoUrl(active.vod_id)}#t=${active.start_s},${active.end_s}`}
                  controls
                  autoPlay={playing === active.id}
                  playsInline
                  preload="metadata"
                  aria-label={`Träffen ${active.title}`}
                  className="aspect-video w-full rounded-xl bg-black"
                />
                <figcaption className="truncate text-xs text-muted-foreground">
                  {active.title}, {clock(active.start_s)}–{clock(active.end_s)}
                </figcaption>
              </figure>
            )}
            <ul className="rounded-2xl border border-line px-3">
              {hits.map((hit) => (
                <Hit
                  key={hit.id}
                  hit={hit}
                  item={byVod.get(hit.vod_id)}
                  picked={picked.includes(hit.id)}
                  clipHref={`/campaigns/${profile.name}?tab=klipp`}
                  playing={active?.id === hit.id}
                  onPick={(on) => setPicked((prev) => (on ? [...prev, hit.id] : prev.filter((x) => x !== hit.id)))}
                  onPlay={() => setPlaying(hit.id)}
                />
              ))}
            </ul>
            <Button className="h-10 w-full" disabled={picked.length === 0 || making} onClick={makeClips}>
              {making ? <Loader2 className="animate-spin" aria-hidden /> : <Scissors aria-hidden />}
              Gör klipp av valda ({picked.length})
            </Button>
            {runs.length > 0 && (
              <p role="status" className="text-sm text-muted-foreground">
                Klippen görs nu och hamnar under Klipp när de är klara.{" "}
                {runs.length === 1 ? (
                  <Link href={`/runs/${runs[0]}`} className="text-brand underline-offset-4 hover:underline">
                    Följ projektet
                  </Link>
                ) : (
                  <>
                    Ett projekt per video:{" "}
                    {runs.map((r, i) => (
                      <span key={r}>
                        {i > 0 && ", "}
                        <Link href={`/runs/${r}`} className="text-brand underline-offset-4 hover:underline">
                          Projekt #{r}
                        </Link>
                      </span>
                    ))}
                  </>
                )}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
