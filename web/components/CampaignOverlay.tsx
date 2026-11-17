"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ImageUp, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StackedPreview } from "@/components/StackedPreview";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfileDetail, OverlayMode, ReframeInfo } from "@/lib/types";

const MAX_BYTES = 10 * 1024 * 1024;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
// Textstorleken i klippet (font_size i profilen, standard 46) i en bild som är 1080 bred.
const FONT_PX = 46;

export function overlayRequirement(mode: OverlayMode): string {
  return mode === "full"
    ? "PNG i 1080 x 1920 px som täcker hela klippet, genomskinlig där videon ska synas. En banner går också. Den läggs i skarven mellan kameran och spelet."
    : `PNG som är 1080 px bred. Den läggs ${mode === "top" ? "överst" : "nederst"} i klippet.`;
}

/** Felet för filen, eller null om den går att ladda upp. Samma kontroller som servern. */
async function checkPng(file: File, mode: OverlayMode): Promise<string | null> {
  if (file.size > MAX_BYTES) return `Filen är ${(file.size / 1024 / 1024).toFixed(1)} MB. Den får vara högst 10 MB.`;
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  if (!PNG_SIGNATURE.every((byte, i) => head[i] === byte)) return "Filen är inte en PNG. Spara bilden som PNG och försök igen.";
  let size: { width: number; height: number };
  try {
    const bitmap = await createImageBitmap(file);
    size = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
  } catch {
    return "Bilden gick inte att läsa. Spara den som PNG igen och försök en gång till.";
  }
  // En banner (minst dubbelt så bred som hög) gör servern om till 1080 x 1920.
  if (mode === "full" && (size.width !== 1080 || size.height !== 1920) && size.width < 2 * size.height) {
    return `Bilden är ${size.width} x ${size.height} px. En overlay som täcker hela klippet måste vara 1080 x 1920 px, eller en banner som är minst dubbelt så bred som hög.`;
  }
  if (mode !== "full" && size.width !== 1080) {
    return `Bilden är ${size.width} px bred. Den måste vara 1080 px bred.`;
  }
  return null;
}

interface Props {
  profile: CampaignProfileDetail;
  onUpdate: (profile: CampaignProfileDetail) => void;
}

export function CampaignOverlay({ profile, onUpdate }: Props) {
  const [reframe, setReframe] = useState<ReframeInfo | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [dragging, setDragging] = useState(false);

  const { overlay, safe_zone: safe, on_screen_text: text } = profile;
  const camera = profile.facecam?.camera ?? true;

  useEffect(() => {
    api.campaign.profile.reframe(profile.name).then(setReframe).catch(() => setReframe(null));
  }, [profile.name]);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setSaved(false);
    const problem = await checkPng(file, overlay.mode);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    try {
      onUpdate(await api.campaign.profile.uploadOverlay(profile.name, file));
      setVersion(Date.now());
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att ladda upp bilden");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Ta bort overlay-bilden? Nya klipp får ingen overlay.")) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      onUpdate(await api.campaign.deleteOverlay(profile.name));
      setVersion(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Det gick inte att ta bort bilden");
    } finally {
      setBusy(false);
    }
  }

  // Servern sparar en banner som en hel bild, så förhandsvisningen visar alltid serverns bild.
  const shown = overlay.exists ? `${api.campaign.proxied(overlay.url)}?v=${version}` : null;
  const fontSize = `${(FONT_PX / 1080) * 100}cqw`;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">{overlayRequirement(overlay.mode)}</p>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFile(e.dataTransfer.files[0]);
          }}
          className={cn(
            "flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-line bg-panel px-6 py-12 text-center transition-colors focus-within:border-brand hover:border-[#3a3a44]",
            dragging && "border-brand bg-[#16131f]",
            busy && "pointer-events-none opacity-60"
          )}
        >
          {busy ? <Loader2 className="size-7 animate-spin text-brand" aria-hidden /> : <ImageUp className="size-7 text-brand" aria-hidden />}
          <span className="font-medium">{busy ? "Laddar upp…" : "Släpp overlay-bilden här eller välj en fil"}</span>
          <span className="text-[13px] text-muted-foreground">
            PNG, högst 10 MB. {overlay.mode === "full" ? "1080 x 1920 px eller en banner." : "1080 px bred."}
          </span>
          <input
            type="file"
            accept="image/png"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              handleFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>

        {error && (
          <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        {saved && !error && (
          <p role="status" className="flex items-center gap-2 rounded-lg border border-[#174a2a] bg-[#0d1a12] px-3 py-2 text-sm text-good">
            <CheckCircle2 className="size-4 shrink-0" aria-hidden />
            <span>
              Overlay-bilden är sparad. Nya klipp får den. För gamla klipp: öppna projektet och tryck Gör klippen kampanjklara
              igen.{" "}
              <Link href={`/campaigns/${profile.name}?tab=material`} className="font-medium underline underline-offset-4">
                Kampanjens projekt
              </Link>
            </span>
          </p>
        )}

        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
          <dt className="text-muted-foreground">Bild</dt>
          <dd>
            {overlay.exists
              ? `${overlay.file}${overlay.width && overlay.height ? `, ${overlay.width} x ${overlay.height} px` : ""}`
              : overlay.required
                ? "Saknas. Kampanjen kräver en overlay, annars blir klippen underkända."
                : "Ingen. Kampanjen kräver ingen overlay."}
          </dd>
          {text.text && (
            <>
              <dt className="text-muted-foreground">Text</dt>
              <dd>
                {overlay.exists
                  ? `”${text.text}” ritas inte när overlay-bilden finns. Bilden har kampanjens text.`
                  : `”${text.text}” ritas ${text.position === "top" ? "överst" : "nederst"} i klippet.`}
              </dd>
            </>
          )}
          <dt className="text-muted-foreground">Läge</dt>
          <dd>{{ full: "Hela klippet", top: "Överst", bottom: "Nederst" }[overlay.mode]}</dd>
          <dt className="text-muted-foreground">Säkra zoner</dt>
          <dd className="tabular-nums">
            {Math.round(safe.top * 100)} % överst och {Math.round(safe.bottom * 100)} % nederst döljs av TikTok
          </dd>
        </dl>
        {overlay.exists && (
          <Button variant="outline" size="sm" onClick={remove} disabled={busy}>
            <Trash2 aria-hidden />
            Ta bort bilden
          </Button>
        )}
      </div>

      <figure>
        <StackedPreview
          src={reframe ? api.campaign.profile.frameUrl(profile.name) : null}
          webcam={reframe?.webcam_rect ?? null}
          gameplay={reframe?.gameplay_rect ?? null}
          split={reframe?.split_ratio ?? 0.4}
          single={!camera}
        >
          {shown && (
            // eslint-disable-next-line @next/next/no-img-element -- overlay-bilden ska visas exakt som den är.
            <img
              src={shown}
              alt="Overlay-bilden"
              className={cn(
                "absolute inset-x-0 w-full",
                overlay.mode === "full" && "inset-y-0 h-full",
                overlay.mode === "top" && "top-0",
                overlay.mode === "bottom" && "bottom-0"
              )}
            />
          )}
          <div className="absolute inset-x-0 top-0 border-b border-dashed border-white/40 bg-black/45" style={{ height: `${safe.top * 100}%` }} />
          <div className="absolute inset-x-0 bottom-0 border-t border-dashed border-white/40 bg-black/45" style={{ height: `${safe.bottom * 100}%` }}>
            {/* I hörnet, så att etiketten inte täcker overlayens egen text. */}
            <span className="absolute top-1 left-1 rounded bg-black/70 px-1 text-[10px] text-white/80">Döljs av TikTok</span>
          </div>
          {/* Samma regel som klippet (campaign/finish.py): den vita texten ritas bara utan overlay. */}
          {text.text && !shown && (
            <p
              className="absolute inset-x-0 text-center leading-none font-bold text-white"
              style={{
                fontSize,
                [text.position === "top" ? "top" : "bottom"]: `${(text.position === "top" ? safe.top : safe.bottom) * 100}%`,
                textShadow: "0 0 3px #000, 0 0 3px #000, 0 0 2px #000",
              }}
            >
              {text.text}
            </p>
          )}
        </StackedPreview>
        <figcaption className="mt-2 text-xs text-muted-foreground">
          Förhandsvisning i 9:16{camera ? "" : ", utan kamera"}. Skuggade band döljs av TikToks knappar och text.
        </figcaption>
      </figure>
    </div>
  );
}
