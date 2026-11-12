"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { TikTokStatus, TikTokUpload } from "@/lib/types";

// Utkast som väntar i TikTok-inkorgen eller är på väg dit.
const WAITING = ["uploading", "uploaded"];

function uploadText(upload: TikTokUpload): string {
  if (upload.status === "uploaded") return "Väntar i TikTok-inkorgen";
  if (upload.status === "uploading") return "Laddas upp till TikTok";
  if (upload.status === "failed") return `Misslyckades: ${upload.error ?? "okänt fel"}`;
  return `TikTok: ${upload.status}`;
}

interface Props {
  clipId: number;
  captionDirty: boolean;
  hasProblems: boolean;
}

/** Skicka ett godkänt klipp som utkast till TikTok-inkorgen. Klippets status ändras inte. */
export function TikTokSend({ clipId, captionDirty, hasProblems }: Props) {
  // shortcut: varje rad läser läget själv, så räknaren för utkast i andra rader uppdateras inte efter en uppladdning. Servern stoppar ändå vid 5.
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [upload, setUpload] = useState<TikTokUpload | null>(null);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaign.tiktok.status().then(setStatus).catch(() => setStatus(null));
    api.campaign.clips.tiktok(clipId).then(setUpload).catch(() => {});
  }, [clipId]);

  async function send() {
    setSending(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.campaign.clips.sendToTikTok(clipId);
      setUpload(result);
      setMessage(result.message ?? null);
      api.campaign.tiktok.status().then(setStatus).catch(() => {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte skicka klippet till TikTok");
    } finally {
      setSending(false);
    }
  }

  const connected = !!status?.connected;
  const full = connected && status.pending_24h >= status.pending_limit;
  const block = !connected
    ? null // förklaras med en länk nedan
    : full
      ? `${status.pending_limit} utkast väntar redan i TikTok-inkorgen. Posta eller ta bort något i appen först.`
      : hasProblems
        ? "Klippet har problem som måste rättas först."
        : captionDirty
          ? "Spara eller ångra captionen först."
          : null;
  const waiting = !!upload && WAITING.includes(upload.status);

  return (
    <div className="space-y-1.5">
      <Button
        className="h-9 w-full"
        variant={waiting ? "outline" : "default"}
        disabled={sending || !connected || !!block}
        onClick={send}
      >
        {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
        {sending ? "Skickar..." : waiting ? "Skicka igen" : "Skicka till TikTok"}
      </Button>
      {status && !connected && (
        <p className="text-xs text-muted-foreground">
          TikTok är inte kopplat.{" "}
          <Link href="/campaigns" className="text-brand underline-offset-4 hover:underline">
            Koppla TikTok under Kampanjer
          </Link>
          .
        </p>
      )}
      {block && <p className="text-xs text-muted-foreground">{block}</p>}
      {upload && !message && (
        <p className={`text-xs ${upload.status === "failed" ? "text-bad" : "text-muted-foreground"}`}>{uploadText(upload)}</p>
      )}
      {message && (
        <p role="status" className="rounded-lg border border-[#174a2a] bg-[#0d1a12] p-2.5 text-xs text-good">
          {message} Kopiera captionen först, den följer inte med till TikTok.
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-xs text-[#fca5a5]">
          {error}
        </p>
      )}
    </div>
  );
}
