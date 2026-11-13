"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Monitor, Send, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { accountName, tiktokAccounts } from "@/components/TikTokCard";
import { TikTokDirectPost } from "@/components/TikTokDirectPost";
import { api } from "@/lib/api";
import type { CampaignClip, TikTokStatus, TikTokUpload } from "@/lib/types";

// Utkast som väntar i TikTok-inkorgen eller är på väg dit.
const WAITING = ["uploading", "uploaded"];
// Senast valda kontot. Bara open_id, aldrig någon token.
const PICKED = "tiktok-konto";
const STUDIO = "https://www.tiktok.com/tiktokstudio/upload";
const POLL_MS = 10_000;

function uploadText(upload: TikTokUpload): string {
  if (upload.mode === "direct") {
    if (upload.status === "processing") return "Publiceras på TikTok";
    if (upload.status === "published")
      return upload.post_url ? "Publicerat på TikTok" : "Publicerat. Privata inlägg har ingen offentlig länk.";
  }
  if (upload.status === "uploaded") return upload.display_name ? `Utkast i ${upload.display_name}` : "Väntar i TikTok-inkorgen";
  if (upload.status === "uploading") return "Laddas upp till TikTok";
  if (upload.status === "failed") return `Misslyckades: ${upload.error ?? "okänt fel"}`;
  return `TikTok: ${upload.status}`;
}

interface Props {
  clip: CampaignClip;
  captionDirty: boolean;
  hasProblems: boolean;
  /** Klippet efter att den publicerade länken har sparats som postlänk. */
  onUpdate?: (clip: CampaignClip) => void;
}

/** Skicka ett godkänt klipp som utkast till ett TikTok-kontos inkorg, eller posta det på datorn. Klippets status ändras inte. */
export function TikTokSend({ clip, captionDirty, hasProblems, onUpdate }: Props) {
  // shortcut: varje rad läser läget själv, så räknaren för utkast i andra rader uppdateras inte efter en uppladdning. Servern stoppar ändå vid 5.
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [upload, setUpload] = useState<TikTokUpload | null>(null);
  const [picked, setPicked] = useState("");
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [desktop, setDesktop] = useState<string | null>(null);
  const [direct, setDirect] = useState(false);
  const [pollTick, setPollTick] = useState(0);

  useEffect(() => {
    api.campaign.tiktok.status().then(setStatus).catch(() => setStatus(null));
    api.campaign.clips.tiktok(clip.id).then(setUpload).catch(() => {});
    setPicked(localStorage.getItem(PICKED) ?? "");
  }, [clip.id]);

  // Medan TikTok publicerar: fråga om läget var 10:e sekund, också efter ett misslyckat anrop.
  const publishing = upload?.mode === "direct" && upload.status === "processing";
  useEffect(() => {
    if (!publishing) return;
    const timer = setTimeout(() => {
      api.campaign.clips
        .tiktok(clip.id)
        .then((u) => u && setUpload(u))
        .catch(() => {})
        .finally(() => setPollTick((t) => t + 1));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [publishing, pollTick, clip.id]);

  const accounts = status ? tiktokAccounts(status).filter((a) => a.connected) : [];
  // Det sparade kontot om det fortfarande är kopplat, annars det första.
  const account = accounts.find((a) => a.open_id === picked) ?? accounts[0];

  async function send() {
    setSending(true);
    setError(null);
    setMessage(null);
    try {
      // Ett konto: ingen kropp, servern väljer det. Flera: det valda.
      const result = await api.campaign.clips.sendToTikTok(clip.id, accounts.length > 1 ? { open_id: account?.open_id } : undefined);
      setUpload({ ...result, display_name: result.display_name ?? account?.display_name ?? null });
      setMessage(result.message ?? null);
      api.campaign.tiktok.status().then(setStatus).catch(() => {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte skicka klippet till TikTok");
    } finally {
      setSending(false);
    }
  }

  async function postOnDesktop() {
    setError(null);
    // Kopieringen startar medan sidan har fokus (Chrome nekar annars), fliken öppnas i samma klick, sedan väntar vi.
    // Alltid den sparade captionen, den som regelkontrollen har godkänt.
    const copying = navigator.clipboard.writeText(clip.caption ?? "");
    // Inte "noopener" i window.open: då svarar den alltid null, och felet nedan går inte att visa.
    const tab = window.open(STUDIO, "_blank");
    if (tab) tab.opener = null;
    let copied = true;
    try {
      await copying;
    } catch {
      copied = false;
    }
    const a = document.createElement("a");
    a.href = api.campaign.downloadUrl(clip);
    a.download = "";
    a.click();
    setDesktop(
      copied
        ? "Captionen är kopierad och filen laddas ner. Dra filen till TikTok Studio och klistra in captionen."
        : "Filen laddas ner, men webbläsaren lät inte appen kopiera captionen. Kopiera den med knappen ovanför."
    );
    if (!tab) setError("Webbläsaren stoppade den nya fliken. Öppna tiktok.com/tiktokstudio/upload själv.");
  }

  const full = !!account && account.pending_24h >= (status?.pending_limit ?? 5);
  // Det som stoppar båda knapparna.
  const clipBlock = hasProblems
    ? "Klippet har problem som måste rättas först."
    : captionDirty
      ? "Spara eller ångra captionen först."
      : null;
  const block = !account
    ? null // förklaras med en länk nedan
    : full
      ? `${status?.pending_limit ?? 5} utkast väntar redan i inkorgen för ${accountName(account)}. Posta eller ta bort något i TikTok-appen först.`
      : clipBlock;
  const waiting = !!upload && upload.mode !== "direct" && WAITING.includes(upload.status);
  // Direct Post kräver scopet video.publish. Gäller inte gränsen för väntande utkast.
  const canDirect = !!account?.can_publish && !!account.open_id;

  return (
    <div className="space-y-1.5">
      {accounts.length > 1 && (
        <select
          aria-label="TikTok-konto"
          value={account?.open_id}
          onChange={(e) => {
            setPicked(e.target.value);
            localStorage.setItem(PICKED, e.target.value);
          }}
          disabled={sending}
          className="h-9 w-full rounded-lg border border-line bg-panel px-2.5 text-sm outline-none focus:border-brand [&>option]:bg-panel"
        >
          {accounts.map((a) => (
            <option key={a.open_id} value={a.open_id}>
              {accountName(a)}
            </option>
          ))}
        </select>
      )}
      {/* Under varandra: kolumnen med knappar är för smal för två bredvid varandra. */}
      <div className="flex flex-col gap-1.5">
        <Button className="h-9 w-full" disabled={sending || !canDirect || !!clipBlock} onClick={() => setDirect(true)}>
          <Send aria-hidden />
          Publicera direkt
        </Button>
        <Button
          className="h-9 w-full"
          variant="outline"
          disabled={sending || !account || !!block}
          onClick={send}
        >
          {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
          {sending ? "Skickar..." : waiting ? "Skicka igen" : "Som utkast"}
        </Button>
        <Button
          className="h-9 w-full"
          variant="outline"
          disabled={!!clipBlock}
          onClick={postOnDesktop}
          title="Kopierar captionen, laddar ner filen och öppnar TikTok Studio"
        >
          <Monitor aria-hidden />
          Posta på datorn
        </Button>
      </div>
      {status && !account && (
        <p className="text-xs text-muted-foreground">
          Inget TikTok-konto är kopplat.{" "}
          <Link href="/settings" className="text-brand underline-offset-4 hover:underline">
            Koppla TikTok under Inställningar
          </Link>
          .
        </p>
      )}
      {account && !canDirect && (
        <p className="text-xs text-muted-foreground">
          Koppla kontot igen under{" "}
          <Link href="/settings" className="text-brand underline-offset-4 hover:underline">
            Inställningar
          </Link>{" "}
          för att publicera direkt.
        </p>
      )}
      {(block ?? (!account && clipBlock)) && <p className="text-xs text-muted-foreground">{block ?? clipBlock}</p>}
      {desktop && (
        <p role="status" className="rounded-lg border border-line bg-background p-2.5 text-xs text-muted-foreground">
          {desktop}
        </p>
      )}
      {upload && !message && (
        <p className={`text-xs ${upload.status === "failed" ? "text-bad" : "text-muted-foreground"}`}>
          {uploadText(upload)}
          {upload.mode === "direct" && upload.status === "published" && upload.post_url && (
            <>
              {" "}
              <a href={upload.post_url} target="_blank" rel="noreferrer" className="text-brand underline-offset-4 hover:underline">
                Öppna på TikTok
              </a>
            </>
          )}
        </p>
      )}
      {message && (
        <div role="status" className="space-y-1 rounded-lg border border-[#174a2a] bg-[#0d1a12] p-2.5 text-xs text-good">
          {upload && <p className="font-medium">{uploadText(upload)}</p>}
          <p>{message} Kopiera captionen först, den följer inte med till TikTok.</p>
        </div>
      )}
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-xs text-[#fca5a5]">
          {error}
        </p>
      )}
      {direct && account && (
        <TikTokDirectPost
          clip={clip}
          account={account}
          upload={upload}
          onSent={(u) => {
            setMessage(null);
            setUpload(u);
          }}
          onClose={(updated) => {
            setDirect(false);
            if (updated) onUpdate?.(updated);
          }}
        />
      )}
    </div>
  );
}
