"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, Download, ExternalLink, Send, X, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { CampaignStatusBadge, PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignClip, CampaignClipPatch, CampaignFormat, Platform } from "@/lib/types";

// Längsta caption på TikTok och Instagram.
const CAPTION_MAX = 2200;

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return h ? `${h}:${m.toString().padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function NoticeList({ tone, title, items }: { tone: "error" | "warning"; title: string; items: string[] }) {
  if (items.length === 0) return null;
  const Icon = tone === "error" ? XCircle : AlertTriangle;
  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm space-y-1",
        tone === "error"
          ? "border-destructive/40 bg-destructive/5 text-destructive"
          : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
      )}
    >
      <p className="flex items-center gap-1.5 font-medium">
        <Icon className="size-4" />
        {title}
      </p>
      <ul className="list-disc pl-6 space-y-0.5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

interface Props {
  clip: CampaignClip;
  formats: CampaignFormat[];
  runId?: number;
  onUpdate: (clip: CampaignClip) => void;
}

export function QueueClipCard({ clip, formats, runId, onUpdate }: Props) {
  const [caption, setCaption] = useState(clip.caption ?? "");
  const [platform, setPlatform] = useState<Platform | "">(clip.platform ?? "");
  const [url, setUrl] = useState(clip.url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const captionDirty = caption !== (clip.caption ?? "");
  const canEditCaption = ["renderad", "underkand", "godkand"].includes(clip.status);
  const needsFormat = formats.length > 0 && !clip.format;
  const formatName = formats.find((f) => f.id === clip.format)?.name ?? clip.format;
  const hasProblems = clip.problems.length > 0;

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

  return (
    <Card className="flex-col gap-5 p-4 sm:flex-row" data-clip-id={clip.id}>
      <div className="mx-auto aspect-[9/16] w-full max-w-60 shrink-0 overflow-hidden rounded-lg bg-black sm:mx-0 sm:w-48 lg:w-56">
        <video
          // #t=0.1 visar en bildruta i stället för svart innan uppspelning.
          src={`${api.campaign.videoUrl(clip)}#t=0.1`}
          controls
          playsInline
          preload="metadata"
          className="h-full w-full object-contain"
          aria-label={`Klipp: ${clip.title}`}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <CampaignStatusBadge status={clip.status} />
            {clip.format && <Badge variant="outline">{formatName}</Badge>}
          </div>
          <h3 className="text-base font-semibold leading-snug break-words">
            {clip.title || <em className="text-muted-foreground">utan titel</em>}
          </h3>
          <p className="text-xs text-muted-foreground">
            {clip.profile} · VOD {clip.vod_id}
            {clip.start_s != null && clip.end_s != null && (
              <>
                {" "}· {formatTime(clip.start_s)}–{formatTime(clip.end_s)} (
                {Math.round(clip.end_s - clip.start_s)} s)
              </>
            )}
          </p>
          {clip.signals.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {clip.signals.map((signal) => (
                <Badge key={signal} variant="secondary" className="font-normal">
                  {signal}
                </Badge>
              ))}
            </div>
          )}
        </div>

        {needsFormat && clip.status !== "postad" && (
          <div className="space-y-1.5">
            <label htmlFor={`format-${clip.id}`} className="text-sm font-medium">
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
            <label htmlFor={`caption-${clip.id}`} className="text-sm font-medium">
              Caption
            </label>
            <span
              className={cn(
                "text-xs tabular-nums text-muted-foreground",
                caption.length > CAPTION_MAX && "font-medium text-destructive"
              )}
            >
              {caption.length} / {CAPTION_MAX}
            </span>
          </div>
          <Textarea
            id={`caption-${clip.id}`}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            readOnly={!canEditCaption}
            disabled={busy}
            className="min-h-24"
          />
          <div className="flex flex-wrap gap-2">
            <CopyButton text={caption} label="Kopiera caption" />
            {captionDirty && canEditCaption && (
              <>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => patch({ caption })}>
                  Spara caption
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setCaption(clip.caption ?? "")}>
                  Ångra
                </Button>
              </>
            )}
          </div>
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
                  körningen
                </Link>
              </>
            ) : null}
            . Är problemet bara i captionen räcker det att ändra och spara den.
          </p>
        )}

        {clip.status === "godkand" && (
          <div className="space-y-3 rounded-lg border bg-muted/40 p-3">
            <div className="flex flex-wrap gap-2">
              <a href={api.campaign.downloadUrl(clip)} download className={buttonVariants({ size: "sm", variant: "outline" })}>
                <Download />
                Ladda ner filen
              </a>
            </div>
            <div className="grid gap-2">
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
                placeholder="https://www.tiktok.com/@…/video/…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>
        )}

        {clip.status === "postad" && clip.url && (
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="text-muted-foreground">
              {clip.platform ? PLATFORM_LABEL[clip.platform] : "Postad"}:
            </span>
            <a
              href={clip.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-w-0 items-center gap-1 break-all underline underline-offset-4"
            >
              {clip.url}
              <ExternalLink className="size-3.5 shrink-0" />
            </a>
          </p>
        )}

        {error && (
          <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </p>
        )}

        <div className="mt-auto flex flex-wrap gap-2 pt-1">
          {clip.status === "renderad" && (
            <Button
              disabled={busy || hasProblems || needsFormat}
              title={
                hasProblems
                  ? "Klippet har problem som måste rättas först"
                  : needsFormat
                    ? "Välj format först"
                    : undefined
              }
              onClick={() => patch(captionDirty ? { status: "godkand", caption } : { status: "godkand" })}
            >
              <Check />
              Godkänn
            </Button>
          )}
          {(clip.status === "renderad" || clip.status === "underkand") && (
            <Button variant="destructive" disabled={busy} onClick={() => patch({ status: "nekad" })}>
              <X />
              Neka
            </Button>
          )}
          {clip.status === "godkand" && (
            <>
              <Button
                disabled={busy || !platform || !url.trim()}
                onClick={() => patch({ status: "postad", platform: platform as Platform, url: url.trim() })}
              >
                <Check />
                Markera som postad
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => patch({ status: "renderad" })}>
                Ångra godkännande
              </Button>
            </>
          )}
          {clip.status === "postad" && (
            <Button disabled={busy} onClick={() => patch({ status: "inskickad" })}>
              <Send />
              Inskickad i Content Rewards
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
