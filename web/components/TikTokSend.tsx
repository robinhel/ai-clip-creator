"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TikTokUpload } from "@/lib/types";

// Senast valda TikTok-kontot. Bara open_id, aldrig någon token.
export const PICKED_TIKTOK = "tiktok-konto";
export const TIKTOK_STUDIO = "https://www.tiktok.com/tiktokstudio/upload";
const POLL_MS = 10_000;

/** Utkast som väntar i TikTok-inkorgen eller är på väg dit. */
export const draftWaiting = (upload: TikTokUpload | null) =>
  !!upload && upload.mode !== "direct" && (upload.status === "uploading" || upload.status === "uploaded");

export function tiktokUploadText(upload: TikTokUpload): string {
  if (upload.mode === "direct") {
    if (upload.status === "processing") return "Publiceras på TikTok";
    if (upload.status === "published")
      return upload.post_url ? "Publicerat på TikTok" : "Publicerat som privat (Bara jag). Inlägget har ingen länk.";
  }
  if (upload.status === "uploaded") return upload.display_name ? `Utkast i inkorgen hos ${upload.display_name}` : "Utkast i TikTok-inkorgen";
  if (upload.status === "uploading") return "Laddas upp till TikTok";
  if (upload.status === "failed") return `Misslyckades: ${upload.error ?? "okänt fel"}`;
  return `TikTok: ${upload.status}`;
}

/** Klippets senaste uppladdning till TikTok. Medan TikTok publicerar: läs om var 10:e sekund, också efter ett misslyckat anrop. */
export function useTikTokUpload(clipId: number, enabled = true) {
  const [upload, setUpload] = useState<TikTokUpload | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    api.campaign.clips.tiktok(clipId).then(setUpload).catch(() => {});
  }, [clipId, enabled]);

  const publishing = upload?.mode === "direct" && upload.status === "processing";
  useEffect(() => {
    if (!publishing) return;
    const timer = setTimeout(() => {
      api.campaign.clips
        .tiktok(clipId)
        .then((u) => u && setUpload(u))
        .catch(() => {})
        .finally(() => setTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [publishing, tick, clipId]);

  return { upload, setUpload };
}
