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
  /** remove: nekar klippet. posted: sparar klippet som postat utan länk, så att det inte hamnar i Skicka in. */
  action: "remove" | "posted";
  onRemoved: (clip: CampaignClip, cancelled: number) => void;
}

const ACTIONS = {
  remove: {
    status: "nekad",
    icon: Trash2,
    label: "Remove",
    question: "Remove the clip?",
    confirm: "Are you sure? The clip is removed from Ready to post and is not posted.",
    yes: "Yes, remove",
  },
  posted: {
    status: "postad_utan_lank",
    icon: Check,
    label: "Posted elsewhere (no link)",
    question: "Mark the clip as posted without a link?",
    confirm: "Are you sure? The clip is saved as posted without a link, so it does not go to Submit and cannot be submitted in Content Rewards.",
    yes: "Yes, mark as posted",
  },
} as const;

/** Ta bort eller Postad utan länk på ett godkänt klipp i Att posta, efter en fråga. Filen och raden i klipploggen finns kvar. */
export function RemoveFromQueue({ clip, pending, action, onRemoved }: Props) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploading = pending.some((p) => p.status === "running");
  const waiting = pending.filter((p) => p.status === "waiting").length;
  const text = ACTIONS[action];
  const Icon = text.icon;

  async function leave() {
    setBusy(true);
    setError(null);
    try {
      // Servern avbryter de väntande uppladdningarna i samma steg, och nekar medan klippet laddas upp.
      onRemoved(await api.campaign.clips.patch(clip.id, { status: text.status }), waiting);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the clip");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {!confirm && (
        <Button
          className={action === "remove" ? "h-9 w-full" : "h-auto min-h-8 w-full justify-start py-1.5 whitespace-normal"}
          size={action === "remove" ? "default" : "sm"}
          variant={action === "remove" ? "outline" : "ghost"}
          disabled={uploading || busy}
          title={uploading ? "The clip is uploading now. Wait until the upload is done." : undefined}
          onClick={() => setConfirm(true)}
        >
          <Icon aria-hidden />
          {text.label}
        </Button>
      )}
      {uploading && <p className="text-xs text-muted-foreground">Uploading now. {text.label} works when the upload is done.</p>}
      {confirm && (
        <div role="group" aria-label={text.question} className="space-y-2 rounded-lg border border-[#4d1d1d] bg-[#1c0f10] p-2.5 text-sm">
          <p className="text-[#fca5a5]">
            {text.confirm}
            {waiting > 0 && ` ${waiting === 1 ? "The scheduled upload is" : `The ${waiting} scheduled uploads are`} cancelled.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="destructive" size="sm" disabled={busy} onClick={leave}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {text.yes}
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(false)}>
              Cancel
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
