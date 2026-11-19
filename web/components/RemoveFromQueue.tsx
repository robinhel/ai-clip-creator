"use client";

import { useState } from "react";
import { Check, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { CampaignClip, ScheduledPost } from "@/lib/types";

interface Props {
  clip: CampaignClip;
  /** Klippets schemalagda poster som väntar eller laddas upp nu. */
  pending: ScheduledPost[];
  onRemoved: (clip: CampaignClip, cancelled: number) => void;
}

/** Postad och Ta bort på ett godkänt klipp i Att posta. Postad sparar klippet som postat utan länk, så att det inte
 * hamnar i Skicka in. Ta bort nekar klippet efter en fråga. Filen och raden i klipploggen finns kvar i båda fallen. */
export function RemoveFromQueue({ clip, pending, onRemoved }: Props) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploading = pending.some((p) => p.status === "running");
  const waiting = pending.filter((p) => p.status === "waiting").length;

  async function leave(status: "nekad" | "postad_utan_lank") {
    setBusy(true);
    setError(null);
    try {
      // Servern avbryter de väntande uppladdningarna i samma steg, och nekar medan klippet laddas upp.
      onRemoved(await api.campaign.clips.patch(clip.id, { status }), waiting);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klippet gick inte att ändra");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {!confirm && (
        <div className="grid grid-cols-2 gap-2">
          <Button
            className="h-9"
            variant="outline"
            disabled={uploading || busy}
            title={uploading ? "Klippet laddas upp nu. Vänta tills uppladdningen är klar." : "Postat utan länk. Klippet hamnar inte i Skicka in."}
            onClick={() => leave("postad_utan_lank")}
          >
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
            Postad
          </Button>
          <Button
            className="h-9"
            variant="outline"
            disabled={uploading || busy}
            title={uploading ? "Klippet laddas upp nu. Vänta tills uppladdningen är klar." : undefined}
            onClick={() => setConfirm(true)}
          >
            <Trash2 aria-hidden />
            Ta bort
          </Button>
        </div>
      )}
      {uploading && <p className="text-xs text-muted-foreground">Laddas upp nu. Postad och Ta bort går när uppladdningen är klar.</p>}
      {confirm && (
        <div role="group" aria-label="Ta bort klippet?" className="space-y-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm">
          <p className="text-[#fca5a5]">
            Är du säker? Klippet tas bort från Att posta och postas inte.
            {waiting > 0 && ` ${waiting === 1 ? "Den schemalagda uppladdningen" : `De ${waiting} schemalagda uppladdningarna`} avbryts.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="destructive" size="sm" disabled={busy} onClick={() => leave("nekad")}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              Ja, ta bort
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(false)}>
              Avbryt
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}
    </div>
  );
}
