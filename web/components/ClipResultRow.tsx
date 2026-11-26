"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CheckCircle2, Download, ExternalLink, Loader2, MoreHorizontal, Pencil, Scissors, Send, ThumbsDown, ThumbsUp, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { CampaignStatusBadge, PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import { ClipEditor } from "@/components/ClipEditor";
import { ClipPosts } from "@/components/ClipPosts";
import { CopyButton } from "@/components/CopyButton";
import { CampaignRules, type Posting, PublishButton } from "@/components/PublishDialog";
import { api } from "@/lib/api";
import { HEADLINE_MAX, textEdit } from "@/lib/headline";
import { formatForKey } from "@/lib/review";
import { VersionButton } from "@/components/VersionDialog";
import { englishProblem } from "@/lib/oldProblems";
import { fixHint, rejectedFix } from "@/lib/steps";
import { clock, cn, SUMMARY_CHANGED } from "@/lib/utils";
import type { CampaignClip, CampaignClipPatch, CampaignFormat, ClipEditInfo, ClipSubscores, ClipTranscript, Platform } from "@/lib/types";

// Längsta caption på TikTok och Instagram. Rubriken får vara högst 120 tecken (redigeraren).
const CAPTION_MAX = 2200;
const TITLE_MAX = 120;

// Meddelandet efter en redigering sparas här, per klipp och version, så att raden visar det
// bara för den versionen.
const editNotice = new Map<number, { updated: string; text: string }>();
const EDITABLE: CampaignClip["status"][] = ["renderad", "underkand", "godkand"];

// Klippet får laddas ner och captionen kopieras först när Robin har sagt ja.
const CAN_POST: CampaignClip["status"][] = ["godkand", "postad", "inskickad", "analytics_inskickad", "betald"];
const POSTED: CampaignClip["status"][] = ["postad", "inskickad", "analytics_inskickad", "betald"];
// Hook först: de första sekunderna avgör om någon tittar vidare.
const SUBSCORES: [keyof ClipSubscores, string][] = [
  ["hook", "Hook"],
  ["engagement", "Engagement"],
  ["value", "Value"],
  ["shareability", "Shareability"],
];

/** Färg efter betyg, som i skissen: grönt från 80, gult från 60. */
export function scoreTone(score: number | null): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-good";
  if (score >= 60) return "text-warn";
  return "text-bad";
}

/** Tangenterna gäller inte när man skriver i ett fält, håller en modifierare eller har en dialog öppen. */
export function ignoreKey(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  return (
    e.metaKey || e.ctrlKey || e.altKey || e.repeat ||
    !!el?.closest("input, textarea, select, [contenteditable=true]") ||
    !!document.querySelector('[role="dialog"]')
  );
}

/** Vad bildgranskningen såg. Bara beskrivningar, inga fel, så de ligger fällda. */
function ReviewNotes({ review }: { review: CampaignClip["review"] }) {
  const notes = Array.isArray(review?.notes) ? review.notes.filter((n): n is string => typeof n === "string" && !!n) : [];
  if (notes.length === 0) return null;
  return (
    <details className="rounded-lg border border-line px-3 py-2 text-[13px] text-muted-foreground">
      <summary className="cursor-pointer select-none hover:text-foreground">Image review ({notes.length})</summary>
      <ul className="mt-1 list-disc space-y-0.5 pl-6">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </details>
  );
}

function NoticeList({ tone, title, items }: { tone: "error" | "warning"; title: string; items: string[] }) {
  if (items.length === 0) return null;
  const Icon = tone === "error" ? XCircle : AlertTriangle;
  // Varningar stoppar inte klippet: dämpade, och fällda när de är många.
  if (tone === "warning") {
    const list = (
      <ul className="mt-1 list-disc space-y-0.5 pl-6">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    );
    return (
      <div className="rounded-lg border border-[#3a3018] px-3 py-2 text-[13px] text-warn/80">
        {items.length > 2 ? (
          <details>
            <summary className="flex cursor-pointer items-center gap-1.5 select-none hover:text-warn [&::-webkit-details-marker]:hidden">
              <Icon className="size-3.5" aria-hidden />
              {items.length} warnings, show
            </summary>
            {list}
          </details>
        ) : (
          <>
            <p className="flex items-center gap-1.5">
              <Icon className="size-3.5" aria-hidden />
              {items.length === 1 ? "Warning" : "Warnings"}
            </p>
            {list}
          </>
        )}
      </div>
    );
  }
  return (
    <div
      className={cn(
        "space-y-1 rounded-lg border p-3 text-sm",
        "border-[#4d1d1d] bg-[#1c0f10] text-[#fca5a5]"
      )}
    >
      <p className="flex items-center gap-1.5 font-medium">
        <Icon className="size-4" aria-hidden />
        {title}
      </p>
      <ul className="list-disc space-y-0.5 pl-6">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function Transcript({ clipId }: { clipId: number }) {
  const [transcript, setTranscript] = useState<ClipTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load(open: boolean) {
    if (!open || transcript) return;
    api.campaign.clips
      .transcript(clipId)
      .then(setTranscript)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the transcript"));
  }

  return (
    <details onToggle={(e) => load(e.currentTarget.open)} className="group">
      <summary className="cursor-pointer text-[13px] text-muted-foreground select-none hover:text-foreground">
        What is said
      </summary>
      <div className="mt-1.5 max-h-48 overflow-y-auto text-sm leading-relaxed text-muted-foreground">
        {error ? (
          <p className="text-bad">{error}</p>
        ) : !transcript ? (
          <p>Loading the transcript…</p>
        ) : transcript.text ? (
          <p className="whitespace-pre-line">{transcript.text}</p>
        ) : (
          <p>The transcript is missing for this clip.</p>
        )}
      </div>
    </details>
  );
}

interface Props {
  clip: CampaignClip;
  /** Klippets nummer i listan, som i OpusClip. Utan nummer visas inget. */
  number?: number;
  formats: CampaignFormat[];
  /** Länk till projektet, för klipp som visas utanför projektsidan. */
  runId?: number;
  /** Var klippet kommer ifrån, med länk. Visas i stället för länken Open the project. */
  source?: { label: string; href: string };
  /** Raden som tangenterna Y, N, E och C gäller. */
  active?: boolean;
  onUpdate: (clip: CampaignClip) => void;
  /** Läs om listan, till exempel när en ny version har blivit ett eget klipp. */
  onReload?: () => void;
  /** Extra val för ett godkänt klipp, till exempel Ta bort i Klippen. */
  approvedAction?: ReactNode;
  /** Extra val i menyn Mer för ett godkänt klipp, till exempel Postad utan länk i Klippen. */
  moreAction?: ReactNode;
  /** Shift+Y: godkänn och öppna Publicera för klippet. */
  onPublish?: (clip: CampaignClip) => void;
  /** Bara i Klippen: där postas klippet. Andra sidor visar en länk dit. */
  posting?: Posting;
}

/** Ett klipp per rad: betyg, video, Claudes motivering, caption, regelkontroll och knappar. */
/** Claudes delpoäng under motiveringen. */
function Subscores({ subscores }: { subscores: ClipSubscores }) {
  return (
    <dl
      aria-label="Claude's subscores"
      title="The score is the sum of the subscores, adjusted against the other clips in the same video."
      className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4"
    >
      {SUBSCORES.map(([key, label]) => {
        const points = subscores[key];
        const hook = key === "hook";
        return (
          <div key={key} className="min-w-0">
            <dt className={cn("text-[12px]", hook ? "font-medium text-foreground" : "text-muted-foreground")}>{label}</dt>
            <dd className="text-[13px] font-semibold tabular-nums">
              {points}
              <span className="font-normal text-muted-foreground">/25</span>
            </dd>
            <div aria-hidden className="mt-1 h-1 overflow-hidden rounded-full bg-line">
              <div className={cn("h-full rounded-full", hook ? "bg-brand" : "bg-muted-foreground/60")} style={{ width: `${(points / 25) * 100}%` }} />
            </div>
          </div>
        );
      })}
    </dl>
  );
}

const SOURCE_LINK = "text-[13px] text-muted-foreground underline underline-offset-4 hover:text-foreground";

/** Utanför projektsidan, till exempel i Klippen, går det att öppna projektet eller videon klippet kommer från. */
function SourceLink({ source, runId }: { source?: { label: string; href: string }; runId?: number }) {
  if (source) {
    return (
      <Link href={source.href} className={SOURCE_LINK}>
        From: {source.label}
      </Link>
    );
  }
  if (!runId) return null;
  return (
    <Link href={`/runs/${runId}`} className={SOURCE_LINK}>
      Open the project
    </Link>
  );
}

/** Äldre servrar har en länk per klipp i stället för en lista med inlägg. canEdit: Change link, bara i Klippen. */
function LegacyLink({ clip, busy, canEdit, onSave }: {
  clip: CampaignClip;
  busy: boolean;
  canEdit: boolean;
  onSave: (body: CampaignClipPatch) => Promise<boolean>;
}) {
  const [platform, setPlatform] = useState<Platform | "">(clip.platform ?? "");
  const [url, setUrl] = useState(clip.url ?? "");
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <div className="space-y-2 rounded-[10px] border border-line bg-panel p-3">
        <NativeSelect aria-label="Platform" value={platform} onChange={(e) => setPlatform(e.target.value as Platform | "")} disabled={busy}>
          <option value="" disabled>
            Platform…
          </option>
          {Object.entries(PLATFORM_LABEL).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </NativeSelect>
        <Input aria-label="New link to the posted clip" type="url" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={busy || !platform || !url.trim()}
            onClick={async () => {
              if (await onSave({ platform: platform as Platform, url: url.trim() })) setEditing(false);
            }}
          >
            Save the link
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }
  if (!POSTED.includes(clip.status) || !clip.url) return null;
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-sm">
      <span className="text-muted-foreground">{clip.platform ? PLATFORM_LABEL[clip.platform] : "Posted"}:</span>
      <a href={clip.url} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 break-all underline underline-offset-4">
        {clip.url}
        <ExternalLink className="size-3.5 shrink-0" aria-hidden />
      </a>
      {clip.status === "postad" && canEdit && (
        <button
          type="button"
          className="text-muted-foreground underline underline-offset-4 hover:text-foreground"
          onClick={() => {
            setPlatform(clip.platform ?? "");
            setUrl(clip.url ?? "");
            setEditing(true);
          }}
        >
          Change link
        </button>
      )}
    </p>
  );
}

/** Ja och Nej för ett klipp att granska. block: varför Ja inte går än. */
function ReviewButtons({ clip, busy, block, onApprove, onReject }: {
  clip: CampaignClip;
  busy: boolean;
  block?: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  if (clip.status !== "renderad" && clip.status !== "underkand") return null;
  const waiting = clip.status === "renderad";
  return (
    <>
      <div className="flex gap-2">
        <Button
          className="h-9 flex-1"
          variant="outline"
          disabled={busy || !waiting || !!block}
          title={waiting ? block : "Fix the problems first"}
          onClick={onApprove}
        >
          <ThumbsUp aria-hidden />
          Yes
        </Button>
        <Button className="h-9 flex-1 text-muted-foreground" variant="outline" disabled={busy} onClick={onReject}>
          <ThumbsDown aria-hidden />
          No
        </Button>
      </div>
      {waiting && block && <p className="text-xs text-muted-foreground">{block}.</p>}
    </>
  );
}

/** Utanför Klippen visar ett godkänt eller postat klipp bara en länk dit, och Ångra. Postningen finns på ett ställe. */
function ElsewhereActions({ clip, busy, onUndo }: { clip: CampaignClip; busy: boolean; onUndo: () => void }) {
  if (clip.status === "postad") {
    return (
      <Link href={`/queue?tab=skicka-in#klipp-${clip.id}`} className={buttonVariants({ variant: "outline", className: "h-9" })}>
        Submit in Clips
        <ArrowRight aria-hidden />
      </Link>
    );
  }
  if (clip.status !== "godkand") return null;
  return (
    <div role="status" className="space-y-2 rounded-[10px] border border-[#174a2a] bg-[#0d1a12] p-3 text-sm">
      <p className="flex items-start gap-2 text-good">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
        Approved. The clip is in Clips, under Ready to post.
      </p>
      <Link href={`/queue?tab=posta#klipp-${clip.id}`} className={buttonVariants({ className: "h-9 w-full" })}>
        Post in Clips
        <ArrowRight aria-hidden />
      </Link>
      <Button className="w-full" size="sm" variant="ghost" disabled={busy} onClick={onUndo}>
        Undo approval
      </Button>
    </div>
  );
}

/** Det som sällan behövs ligger i en meny, så att raden inte har en vägg av knappar. */
function MoreMenu({ clip, busy, onUndo, onReload, children }: {
  clip: CampaignClip;
  busy: boolean;
  onUndo: () => void;
  onReload?: () => void;
  children?: ReactNode;
}) {
  return (
    <details className="group rounded-lg border border-line">
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 px-3 text-sm text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
        <MoreHorizontal className="size-4" aria-hidden />
        More
      </summary>
      <div className="space-y-2 border-t border-line p-2">
        <VersionButton clip={clip} onCreated={onReload} />
        <Button className="w-full" size="sm" variant="ghost" disabled={busy} onClick={onUndo}>
          Undo approval
        </Button>
        {children}
      </div>
    </details>
  );
}

/** Vad Robin gör med ett underkänt klipp. Fel i captionen och längden rättas här; en egen video har ingen projektsida. */
function RejectedFix({ clip, runId }: { clip: CampaignClip; runId?: number }) {
  const fix = rejectedFix(clip);
  if (!fix) return null;
  const hint = fixHint(fix);
  if (hint) return <p className="text-sm text-muted-foreground">{hint}</p>;
  return (
    <p className="text-sm text-muted-foreground">
      Fix the problems and make the clips campaign-ready again
      {runId ? (
        <>
          {" "}on the{" "}
          <Link href={`/runs/${runId}`} className="underline underline-offset-4 hover:text-foreground">
            project page
          </Link>
        </>
      ) : null}
      .
    </p>
  );
}

/** Ett textfält i Ändra rubrik, med räknare mot högsta antal tecken. */
function TextField({ id, label, value, max, onChange, disabled, placeholder, hint }: {
  id: string;
  label: string;
  value: string;
  max: number;
  onChange: (value: string) => void;
  disabled: boolean;
  placeholder?: string;
  hint: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-[13px] text-muted-foreground">
          {label}
        </label>
        <span className={cn("text-xs tabular-nums text-muted-foreground", value.length > max && "font-medium text-bad")}>
          {value.length} / {max}
        </span>
      </div>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} disabled={disabled} className="bg-panel" />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export function ClipResultRow({ clip, number, formats, runId, source, active = false, onUpdate, onReload, approvedAction, moreAction, onPublish, posting }: Props) {
  const [caption, setCaption] = useState(clip.caption ?? "");
  const [title, setTitle] = useState(clip.title ?? "");
  const [headline, setHeadline] = useState(clip.headline ?? "");
  const [editing, setEditing] = useState(false);
  const video = useRef<HTMLVideoElement>(null);

  // Raden står kvar när servern har sparat, så att videon och öppna fönster inte nollställs.
  // Fälten följer det sparade, men inte medan Robin skriver i dem.
  useEffect(() => {
    if (editing) return;
    setCaption(clip.caption ?? "");
    setTitle(clip.title ?? "");
    setHeadline(clip.headline ?? "");
  }, [clip.updated, clip.caption, clip.title, clip.headline, editing]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const captionDirty = caption !== (clip.caption ?? "");
  const textBody = textEdit(title, clip.title ?? "", headline, clip.headline);
  const textDirty = Object.keys(textBody).length > 0;
  const canEditCaption = ["renderad", "underkand", "godkand"].includes(clip.status);
  const needsFormat = formats.length > 0 && !clip.format;
  const formatName = formats.find((f) => f.id === clip.format)?.name ?? clip.format;
  const hasProblems = clip.problems.length > 0;
  const approved = CAN_POST.includes(clip.status);
  // Servern har en lista med inlägg per plattform. Äldre servrar har bara en länk per klipp.
  const hasPosts = (clip.posts?.length ?? 0) > 0;
  // Utanför Klippen visar ett godkänt klipp bara Ångra och en länk dit: postningen finns på ett ställe.
  const elsewhere = !posting && clip.status === "godkand";
  const [editInfo, setEditInfo] = useState<ClipEditInfo | null>(null);
  const [opening, setOpening] = useState(false);
  const [keyHint, setKeyHint] = useState<string | null>(null);
  const notice = editNotice.get(clip.id)?.updated === clip.updated ? editNotice.get(clip.id)!.text : null;

  // Läget hämtas först vid klick: en GET per rad gör ffprobe på servern för varje klipp.
  async function openEditor() {
    setOpening(true);
    setError(null);
    try {
      setEditInfo(await api.campaign.clips.edit(clip.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open the editor");
    } finally {
      setOpening(false);
    }
  }

  function handleEdited(updated: CampaignClip, trimmed: boolean, wordsChanged = false) {
    const parts = [
      trimmed && "The clip is trimmed.",
      updated.title !== clip.title && "The title is changed.",
      (updated.headline ?? "") !== (clip.headline ?? "") && "The on-screen text is changed.",
      wordsChanged && "The subtitles are fixed.",
    ];
    const what = parts.filter(Boolean).join(" ");
    const again = clip.status === "godkand" ? " It was approved, so it needs a new yes." : "";
    const result = updated.status === "underkand" ? " The rule check found problems, see below." : "";
    editNotice.set(updated.id, { updated: updated.updated, text: `${what}${again}${result}` });
    setEditInfo(null);
    setEditing(false);
    onUpdate(updated);
  }
  const length = clip.start_s != null && clip.end_s != null ? clip.end_s - clip.start_s : null;

  /** true när ändringen sparades. */
  async function patch(body: CampaignClipPatch): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.campaign.clips.patch(clip.id, body));
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
      return false;
    } finally {
      setBusy(false);
    }
  }

  /** Titel och text i bilden sparas som i Redigera: captionen byggs om från mallen, och en inbränd rubrik ritas om.
   * En egen ändring i captionen sparas efteråt. */
  async function saveText() {
    if (!textDirty) {
      if (await patch({ caption })) setEditing(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let updated = await api.campaign.clips.saveEdit(clip.id, textBody);
      if (captionDirty && caption !== updated.caption) updated = await api.campaign.clips.patch(clip.id, { caption });
      window.dispatchEvent(new Event(SUMMARY_CHANGED));
      handleEdited(updated, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  const approve = () => patch(captionDirty ? { status: "godkand", caption } : { status: "godkand" });
  const approveBlock = hasProblems
    ? "The clip has problems that must be fixed first"
    : needsFormat
      ? "Choose a format first"
      : undefined;
  const postBlock = hasProblems
    ? "The clip has problems that must be fixed first."
    : captionDirty || textDirty
      ? "Save or undo the text first."
      : null;

  // Snabbgranskning med tangenterna. Samma skydd som knapparna: är knappen avstängd händer inget, och raden säger varför.
  const canChooseFormat = canEditCaption && formats.length > 0 && !elsewhere;
  const keyAction = useRef<(key: string, shift: boolean) => void>(() => {});
  keyAction.current = (key, shift) => {
    if (key === " ") {
      const v = video.current;
      if (v) void (v.paused ? v.play().catch(() => {}) : v.pause());
      return;
    }
    if (busy || opening) return;
    const format = formatForKey(key, formats);
    if (format) {
      if (!canChooseFormat) return setKeyHint("The format cannot be changed in this status.");
      setKeyHint(null);
      if (format !== clip.format) patch({ format });
    } else if (key === "y") {
      if (clip.status !== "renderad") return setKeyHint(clip.status === "underkand" ? "Yes: fix the problems first." : "Yes only works for clips waiting for review.");
      if (approveBlock) return setKeyHint(`Yes: ${approveBlock.toLowerCase()}.`);
      setKeyHint(null);
      approve().then((ok) => ok && shift && onPublish?.(clip));
    } else if (key === "n") {
      if (clip.status !== "renderad" && clip.status !== "underkand") return setKeyHint("No only works for clips that are waiting or failed the check.");
      setKeyHint(null);
      patch({ status: "nekad" });
    } else if (key === "e") {
      if (!EDITABLE.includes(clip.status)) return setKeyHint("The clip cannot be edited in this status.");
      setKeyHint(null);
      openEditor();
    } else if (key === "c") {
      if (!approved) return setKeyHint("The caption can be copied only when the clip is approved.");
      if (captionDirty) return setKeyHint("Save or undo the caption first.");
      navigator.clipboard.writeText(clip.caption ?? "").then(
        () => setKeyHint("The caption is copied."),
        () => setKeyHint("The browser did not let the app copy.")
      );
    }
  };
  useEffect(() => {
    if (!active) return setKeyHint(null);
    function onKey(e: KeyboardEvent) {
      const key = e.key.toLowerCase();
      if (!/^([ynec1-9 ])$/.test(key) || ignoreKey(e)) return;
      // Mellanslag på en knapp, länk eller video gör det som webbläsaren brukar göra.
      if (key === " " && (e.target as HTMLElement | null)?.closest("button, a, summary, video")) return;
      e.preventDefault();
      keyAction.current(key, e.shiftKey);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  return (
    <article
      id={`klipp-${clip.id}`}
      data-clip-id={clip.id}
      data-active={active || undefined}
      className="relative scroll-mt-16 before:absolute before:inset-y-6 before:-left-4 before:w-[3px] before:rounded-full before:bg-brand before:opacity-0 data-active:before:opacity-100 grid grid-cols-[56px_minmax(0,1fr)] gap-x-4 gap-y-4 border-b border-line py-6 md:scroll-mt-4 lg:grid-cols-[56px_230px_minmax(0,1fr)_200px] lg:gap-x-5"
    >
      <div className="row-span-2 pt-0.5 text-center" aria-label={clip.score == null ? "No score" : `Score ${clip.score} of 100`}>
        <p className={cn("text-[32px] leading-none font-extrabold tabular-nums", scoreTone(clip.score))}>
          {clip.score ?? "–"}
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">{clip.score == null ? "no score" : "score"}</p>
      </div>

      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 lg:col-span-3">
        <h3 className="text-[19px] leading-snug font-semibold break-words">
          {number != null && <span className="mr-2 font-normal text-muted-foreground tabular-nums">#{number}</span>}
          {clip.title || <em className="text-muted-foreground">untitled</em>}
        </h3>
        {/* Klippets id, som trådarna säger "klipp 44" om. #1 ovan är ordningen i projektet. Två klipp kan ha samma titel. */}
        <span className="text-xs text-muted-foreground tabular-nums">Clip {clip.id}</span>
        <CampaignStatusBadge status={clip.status} />
        {clip.version_of != null && <Badge variant="outline">Version av klipp {clip.version_of}</Badge>}
        {clip.format && <Badge variant="outline">{formatName}</Badge>}
        <SourceLink source={source} runId={runId} />
      </header>

      <div className="relative col-start-2 aspect-[9/16] w-full max-w-[230px] overflow-hidden rounded-xl bg-black lg:col-start-auto">
        <video
          ref={video}
          // #t=0.1 visar en bildruta i stället för svart innan uppspelning.
          src={`${api.campaign.videoUrl(clip)}#t=0.1`}
          poster={api.campaign.posterUrl(clip)}
          controls
          playsInline
          preload="metadata"
          className="h-full w-full object-contain"
          aria-label={`Clip: ${clip.title}`}
        />
        {length != null && (
          <span className="pointer-events-none absolute top-2 right-2 rounded-md bg-black/70 px-1.5 text-xs tabular-nums">
            {clock(length)}
          </span>
        )}
      </div>

      <div className="col-start-2 flex min-w-0 flex-col gap-3 lg:col-start-auto">
        {notice && (
          <p role="status" className="flex items-start gap-2 rounded-[10px] border border-[#174a2a] bg-[#0d1a12] px-3 py-2.5 text-[13px] text-good">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            {notice}
          </p>
        )}
        {clip.reason && (
          <p className="rounded-[10px] border border-[#2c2440] bg-[#16131f] px-3 py-2.5 text-[13px] text-[#cfc8ea]">
            {/* Utan betyg har Claude inte valt klippet; motiveringen säger varför. */}
            {clip.score != null && <span className="font-medium text-[#e4defb]">Claude: </span>}
            {clip.reason}
          </p>
        )}
        {clip.subscores && <Subscores subscores={clip.subscores} />}

        {canChooseFormat && (
          <div className="space-y-1.5">
            <label htmlFor={`format-${clip.id}`} className="text-[13px] text-muted-foreground">
              Format
            </label>
            <NativeSelect
              id={`format-${clip.id}`}
              value={clip.format ?? ""}
              disabled={busy}
              onChange={(e) => patch({ format: e.target.value })}
            >
              <option value="" disabled>
                Choose format…
              </option>
              {formats.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                  {f.description ? ` – ${f.description}` : ""}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}

        {editing && (
          <>
            <TextField
              id={`bildtext-${clip.id}`}
              label="On-screen text"
              value={headline}
              max={HEADLINE_MAX}
              onChange={setHeadline}
              disabled={busy}
              placeholder="Same as the title"
              hint="If the campaign does not require its own text, this text shows at the top of the frame for the whole clip. Empty field: the title shows there."
            />
            <TextField
              id={`rubrik-${clip.id}`}
              label="Title"
              value={title}
              max={TITLE_MAX}
              onChange={setTitle}
              disabled={busy}
              hint="The first line of the caption. A new title rebuilds the caption from the campaign's template, unless you wrote it yourself."
            />
          </>
        )}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor={`caption-${clip.id}`} className="text-[13px] text-muted-foreground">
              Caption
            </label>
            {editing && (
              <span
                className={cn(
                  "text-xs tabular-nums text-muted-foreground",
                  caption.length > CAPTION_MAX && "font-medium text-bad"
                )}
              >
                {caption.length} / {CAPTION_MAX}
              </span>
            )}
          </div>
          {editing ? (
            <Textarea
              id={`caption-${clip.id}`}
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              disabled={busy}
              className="min-h-24 bg-panel"
            />
          ) : (
            <p id={`caption-${clip.id}`} className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words whitespace-pre-line">
              {clip.caption || <span className="text-muted-foreground">No caption yet.</span>}
            </p>
          )}
          {editing && clip.status === "godkand" && (
            <p className="text-xs text-muted-foreground">If you change the text, the clip must be approved again. Scheduled uploads are cancelled.</p>
          )}
          {editing && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy || (!captionDirty && !textDirty) || !title.trim() || title.length > TITLE_MAX || (textBody.headline !== undefined && headline.length > HEADLINE_MAX)}
                onClick={saveText}
              >
                {busy && <Loader2 className="animate-spin" aria-hidden />}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setCaption(clip.caption ?? "");
                  setTitle(clip.title ?? "");
                  setHeadline(clip.headline ?? "");
                  setEditing(false);
                }}
              >
                Undo
              </Button>
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-1.5 text-xs">
          {clip.signals.map((signal) => (
            <span key={signal} className="rounded-full border border-line bg-panel px-2 py-0.5 text-muted-foreground">
              {signal}
            </span>
          ))}
          {!hasProblems && (
            <span className="inline-flex items-center gap-1 rounded-full border border-[#174a2a] px-2 py-0.5 text-good">
              <CheckCircle2 className="size-3" aria-hidden />
              The rule check is green
            </span>
          )}
        </div>
        <NoticeList tone="error" title="The rule check found problems" items={clip.problems.map(englishProblem)} />
        <NoticeList tone="warning" title="Warnings" items={clip.warnings.map(englishProblem)} />
        <ReviewNotes review={clip.review} />
        {posting?.campaign && approved && <CampaignRules detail={posting.campaign} />}

        <RejectedFix clip={clip} runId={runId} />

        {hasPosts && <ClipPosts clip={clip} onUpdate={onUpdate} readOnly={!posting} />}
        {!hasPosts && <LegacyLink clip={clip} busy={busy} canEdit={!!posting} onSave={patch} />}

        <Transcript clipId={clip.id} />
      </div>

      <div className="col-start-2 flex flex-col gap-2 lg:col-start-auto">
        <ReviewButtons clip={clip} busy={busy} block={approveBlock} onApprove={approve} onReject={() => patch({ status: "nekad" })} />

        {!posting && <ElsewhereActions clip={clip} busy={busy} onUndo={() => patch({ status: "renderad" })} />}

        {posting && approved && (clip.status === "godkand" || hasPosts) && (
          <PublishButton clip={clip} posting={posting} block={postBlock} onUpdate={onUpdate} />
        )}
        {EDITABLE.includes(clip.status) && !elsewhere && (
          <>
            <Button className="h-9 justify-start" variant="outline" disabled={busy || opening} onClick={openEditor}>
              {opening ? <Loader2 className="animate-spin" aria-hidden /> : <Scissors aria-hidden />}
              Edit
            </Button>
            {editInfo && (
              <ClipEditor clip={clip} info={editInfo} open onOpenChange={(o) => !o && setEditInfo(null)} onSaved={handleEdited} />
            )}
          </>
        )}
        {canEditCaption && !editing && !elsewhere && (
          <Button className="h-9 justify-start" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
            <Pencil aria-hidden />
            Edit headline and caption
          </Button>
        )}
        {approved && posting && (
          <>
            {/* Kopiera alltid den sparade captionen, den som regelkontrollen har godkänt. */}
            <CopyButton
              text={clip.caption ?? ""}
              label={captionDirty ? "Save first" : "Copy caption"}
              disabled={captionDirty}
              className="h-9 justify-start"
            />
            <a
              href={api.campaign.downloadUrl(clip)}
              download
              className={buttonVariants({ variant: "outline", className: "h-9 justify-start" })}
            >
              <Download aria-hidden />
              Download
            </a>
          </>
        )}

        {/* Det som sällan behövs ligger i en meny, så att raden inte har en vägg av knappar. */}
        {posting && clip.status === "godkand" && (
          <>
            {approvedAction}
            <MoreMenu clip={clip} busy={busy} onUndo={() => patch({ status: "renderad" })} onReload={onReload}>
              {moreAction}
            </MoreMenu>
          </>
        )}
        {clip.status === "postad" && !hasPosts && posting && (
          <>
            <Button className="h-auto min-h-9 py-2 whitespace-normal" disabled={busy} onClick={() => patch({ status: "inskickad" })}>
              <Send aria-hidden />
              Submitted in Content Rewards
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch({ status: "godkand" })}>
              Undo posting
            </Button>
          </>
        )}
        {clip.status === "nekad" && (
          <Button className="h-9 justify-start" variant="outline" disabled={busy} onClick={() => patch({ status: "renderad" })}>
            Undo no
          </Button>
        )}

        {keyHint && (
          <p role="status" className="rounded-lg border border-line bg-panel px-2.5 py-2 text-[13px] text-muted-foreground">
            {keyHint}
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
      </div>
    </article>
  );
}
