"use client";

import { useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { CampaignClip, ScheduledPost } from "@/lib/types";

interface Props {
  clip: CampaignClip;
  /** Klippets schemalagda poster som väntar eller laddas upp nu. */
  pending: ScheduledPost[];
  onRemoved: (clip: CampaignClip, cancelled: number) => void;
}

/** Ta bort ett godkänt klipp från Att posta. Klippet blir nekat; filen och raden i klipploggen finns kvar. */
export function RemoveFromQueue({ clip, pending, onRemoved }: Props) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploading = pending.some((p) => p.status === "running");
  const waiting = pending.filter((p) => p.status === "waiting").length;

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      // Servern avbryter de väntande uppladdningarna i samma steg, och nekar medan klippet laddas upp.
      onRemoved(await api.campaign.clips.patch(clip.id, { status: "nekad" }), waiting);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Klippet gick inte att ta bort");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {!confirm && (
        <Button
          className="w-full text-muted-foreground"
          size="sm"
          variant="ghost"
          disabled={uploading}
          title={uploading ? "Klippet laddas upp nu. Vänta tills uppladdningen är klar." : undefined}
          onClick={() => setConfirm(true)}
        >
          <Trash2 aria-hidden />
          Ta bort
        </Button>
      )}
      {uploading && <p className="text-xs text-muted-foreground">Laddas upp nu. Ta bort går när uppladdningen är klar.</p>}
      {confirm && (
        <div role="group" aria-label="Ta bort klippet?" className="space-y-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm">
          <p className="text-[#fca5a5]">
            Ta bort klippet från Att posta? Det postas inte.
            {waiting > 0 && ` ${waiting === 1 ? "Den schemalagda uppladdningen" : `De ${waiting} schemalagda uppladdningarna`} avbryts.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="destructive" size="sm" disabled={busy} onClick={remove}>
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
