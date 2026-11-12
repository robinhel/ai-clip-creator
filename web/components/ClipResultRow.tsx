"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, CheckCircle2, Download, ExternalLink, Pencil, Send, ThumbsDown, ThumbsUp, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { CampaignStatusBadge, PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import { clock, cn } from "@/lib/utils";
import type { CampaignClip, CampaignClipPatch, CampaignFormat, ClipTranscript, Platform } from "@/lib/types";

// Längsta caption på TikTok och Instagram.
const CAPTION_MAX = 2200;

/** Färg efter betyg, som i skissen: grönt från 80, gult från 60. */
export function scoreTone(score: number | null): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-good";
  if (score >= 60) return "text-warn";
  return "text-bad";
}

function NoticeList({ tone, title, items }: { tone: "error" | "warning"; title: string; items: string[] }) {
  if (items.length === 0) return null;
  const Icon = tone === "error" ? XCircle : AlertTriangle;
  return (
    <div
      className={cn(
        "space-y-1 rounded-lg border p-3 text-sm",
        tone === "error" ? "border-[#4d1d1d] bg-[#1c0f10] text-[#fca5a5]" : "border-[#5a4416] bg-[#1b160b] text-warn"
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
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta transkriptet"));
  }

  return (
    <details onToggle={(e) => load(e.currentTarget.open)} className="group">
      <summary className="cursor-pointer text-[13px] text-muted-foreground select-none hover:text-foreground">
        Det han säger
      </summary>
      <div className="mt-1.5 max-h-48 overflow-y-auto text-sm leading-relaxed text-muted-foreground">
        {error ? (
          <p className="text-bad">{error}</p>
        ) : !transcript ? (
          <p>Hämtar transkriptet…</p>
        ) : transcript.text ? (
          <p className="whitespace-pre-line">{transcript.text}</p>
        ) : (
          <p>Transkriptet saknas för det här klippet.</p>
        )}
      </div>
    </details>
  );
}

interface Props {
  clip: CampaignClip;
  /** Klippets nummer i listan, som i OpusClip. */
  number: number;
  formats: CampaignFormat[];
  /** Länk till projektet, för klipp som visas utanför projektsidan. */
  runId?: number;
  onUpdate: (clip: CampaignClip) => void;
}

/** Ett klipp per rad: betyg, video, Claudes motivering, caption, regelkontroll och knappar. */
export function ClipResultRow({ clip, number, formats, runId, onUpdate }: Props) {
  const [caption, setCaption] = useState(clip.caption ?? "");
  const [editing, setEditing] = useState(false);
  const [platform, setPlatform] = useState<Platform | "">(clip.platform ?? "");
  const [url, setUrl] = useState(clip.url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const captionDirty = caption !== (clip.caption ?? "");
  const canEditCaption = ["renderad", "underkand", "godkand"].includes(clip.status);
  const needsFormat = formats.length > 0 && !clip.format;
  const formatName = formats.find((f) => f.id === clip.format)?.name ?? clip.format;
  const hasProblems = clip.problems.length > 0;
  const length = clip.start_s != null && clip.end_s != null ? clip.end_s - clip.start_s : null;

  async function patch(body: CampaignClipPatch) {
    setBusy(true);
    setError(null);
    try {
      onUpdate(await api.campaign.clips.patch(clip.id, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att spara");
    } finally {
      setBusy(false);
    }
  }

  const approveBlock = hasProblems
    ? "Klippet har problem som måste rättas först"
    : needsFormat
      ? "Välj format först"
      : undefined;
  const postBlock = hasProblems
    ? "Klippet har problem som måste rättas först"
    : captionDirty
      ? "Spara eller ångra captionen först"
      : !platform || !url.trim()
        ? "Välj plattform och klistra in länken först"
        : undefined;

  return (
    <article
      data-clip-id={clip.id}
      className="grid grid-cols-[56px_minmax(0,1fr)] gap-x-4 gap-y-4 border-b border-line py-6 lg:grid-cols-[56px_230px_minmax(0,1fr)_200px] lg:gap-x-5"
    >
      <div className="row-span-2 pt-0.5 text-center" aria-label={clip.score == null ? "Inget betyg" : `Betyg ${clip.score} av 99`}>
        <p className={cn("text-[32px] leading-none font-extrabold tabular-nums", scoreTone(clip.score))}>
          {clip.score ?? "–"}
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">{clip.score == null ? "inget betyg" : "betyg"}</p>
      </div>

      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 lg:col-span-3">
        <h3 className="text-[19px] leading-snug font-semibold break-words">
          <span className="mr-2 font-normal text-muted-foreground tabular-nums">#{number}</span>
          {clip.title || <em className="text-muted-foreground">utan titel</em>}
        </h3>
        <CampaignStatusBadge status={clip.status} />
        {clip.format && <Badge variant="outline">{formatName}</Badge>}
      </header>

      <div className="relative col-start-2 aspect-[9/16] w-full max-w-[230px] overflow-hidden rounded-xl bg-black lg:col-start-auto">
        <video
          // #t=0.1 visar en bildruta i stället för svart innan uppspelning.
          src={`${api.campaign.videoUrl(clip)}#t=0.1`}
          controls
          playsInline
          preload="metadata"
          className="h-full w-full object-contain"
          aria-label={`Klipp: ${clip.title}`}
        />
        {length != null && (
          <span className="pointer-events-none absolute top-2 right-2 rounded-md bg-black/70 px-1.5 text-xs tabular-nums">
            {clock(length)}
          </span>
        )}
      </div>

      <div className="col-start-2 flex min-w-0 flex-col gap-3 lg:col-start-auto">
        {clip.reason && (
          <p className="rounded-[10px] border border-[#2c2440] bg-[#16131f] px-3 py-2.5 text-[13px] text-[#cfc8ea]">
            <span className="font-medium text-[#e4defb]">Claude: </span>
            {clip.reason}
          </p>
        )}

        {needsFormat && clip.status !== "postad" && (
          <div className="space-y-1.5">
            <label htmlFor={`format-${clip.id}`} className="text-[13px] text-muted-foreground">
              Format
            </label>
            <NativeSelect
              id={`format-${clip.id}`}
              value=""
              disabled={busy}
              onChange={(e) => patch({ format: e.target.value })}
            >
              <option value="" disabled>
                Välj format…
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
              autoFocus
            />
          ) : (
            <p id={`caption-${clip.id}`} className="rounded-[10px] border border-line bg-panel px-3 py-2.5 text-sm break-words whitespace-pre-line">
              {clip.caption || <span className="text-muted-foreground">Ingen caption än.</span>}
            </p>
          )}
          {editing && clip.status === "godkand" && (
            <p className="text-xs text-muted-foreground">Ändrar du captionen måste klippet godkännas igen.</p>
          )}
          {editing && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy || !captionDirty} onClick={() => patch({ caption })}>
                Spara caption
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setCaption(clip.caption ?? "");
                  setEditing(false);
                }}
              >
                Ångra
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
              Regelkontrollen är grön
            </span>
          )}
        </div>
        <NoticeList tone="error" title="Regelkontrollen hittade problem" items={clip.problems} />
        <NoticeList tone="warning" title="Varningar" items={clip.warnings} />

        {clip.status === "underkand" && (
          <p className="text-sm text-muted-foreground">
            Rätta problemen och gör klippen kampanjklara igen
            {runId ? (
              <>
                {" "}på{" "}
                <Link href={`/runs/${runId}`} className="underline underline-offset-4 hover:text-foreground">
                  projektsidan
                </Link>
              </>
            ) : null}
            . Är problemet bara i captionen räcker det att ändra och spara den.
          </p>
        )}

        {clip.status === "postad" && clip.url && (
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="text-muted-foreground">{clip.platform ? PLATFORM_LABEL[clip.platform] : "Postad"}:</span>
            <a
              href={clip.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-w-0 items-center gap-1 break-all underline underline-offset-4"
            >
              {clip.url}
              <ExternalLink className="size-3.5 shrink-0" aria-hidden />
            </a>
          </p>
        )}

        <Transcript clipId={clip.id} />
      </div>

      <div className="col-start-2 flex flex-col gap-2 lg:col-start-auto">
        {(clip.status === "renderad" || clip.status === "underkand") && (
          <div className="flex gap-2">
            <Button
              className="h-9 flex-1"
              variant="outline"
              disabled={busy || clip.status !== "renderad" || !!approveBlock}
              title={clip.status === "underkand" ? "Rätta problemen först" : approveBlock}
              onClick={() => patch(captionDirty ? { status: "godkand", caption } : { status: "godkand" })}
            >
              <ThumbsUp aria-hidden />
              Ja
            </Button>
            <Button className="h-9 flex-1 text-muted-foreground" variant="outline" disabled={busy} onClick={() => patch({ status: "nekad" })}>
              <ThumbsDown aria-hidden />
              Nej
            </Button>
          </div>
        )}
        {clip.status === "renderad" && approveBlock && (
          <p className="text-xs text-muted-foreground">{approveBlock}.</p>
        )}
        {canEditCaption && !editing && (
          <Button className="h-9 justify-start" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
            <Pencil aria-hidden />
            Ändra caption
          </Button>
        )}
        {/* Kopiera alltid den sparade captionen, den som regelkontrollen har godkänt. */}
        <CopyButton
          text={clip.caption ?? ""}
          label={captionDirty ? "Spara först" : "Kopiera caption"}
          disabled={captionDirty}
          className="h-9 justify-start"
        />
        <a
          href={api.campaign.downloadUrl(clip)}
          download
          className={buttonVariants({ variant: "outline", className: "h-9 justify-start" })}
        >
          <Download aria-hidden />
          Ladda ner
        </a>

        {clip.status === "godkand" && (
          <div className="mt-1 space-y-2 border-t border-line pt-3">
            <NativeSelect
              aria-label="Plattform"
              value={platform}
              onChange={(e) => setPlatform(e.target.value as Platform | "")}
              disabled={busy}
            >
              <option value="" disabled>
                Plattform…
              </option>
              {Object.entries(PLATFORM_LABEL).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </NativeSelect>
            <Input
              aria-label="Länk till det postade klippet"
              type="url"
              placeholder="https://www.tiktok.com/@…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
            />
            <Button
              className="h-9 w-full"
              disabled={busy || !!postBlock}
              title={postBlock}
              onClick={() => patch({ status: "postad", platform: platform as Platform, url: url.trim() })}
            >
              <Check aria-hidden />
              Markera som postad
            </Button>
            <Button className="w-full" size="sm" variant="ghost" disabled={busy} onClick={() => patch({ status: "renderad" })}>
              Ångra godkännande
            </Button>
          </div>
        )}
        {clip.status === "postad" && (
          <Button className="h-auto min-h-9 py-2 whitespace-normal" disabled={busy} onClick={() => patch({ status: "inskickad" })}>
            <Send aria-hidden />
            Inskickad i Content Rewards
          </Button>
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
