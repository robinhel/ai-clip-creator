"use client";

import { useState } from "react";
import { HardDrive, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { deleteRunVod } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignRunInfo } from "@/lib/types";

/** Det som behövs av en körning: ett projekt från GET /campaign/runs, eller en fil i ett projekt för egna filer. */
type VodRun = Pick<CampaignRunInfo, "run_id" | "vod_id" | "status" | "vod_bytes">;

const MB = 1024 ** 2;
const GB = 1024 ** 3;
const one = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** "6,2 GB", eller "350 MB" under 1 GB. */
export function diskSize(bytes: number): string {
  return bytes >= GB ? `${one.format(bytes / GB)} GB` : `${Math.max(1, Math.round(bytes / MB))} MB`;
}

/** En egen fil har filens namn som VOD-id. Twitch-VOD:ar har bara siffror. */
const isOwnFile = (run: VodRun) => !/^\d+$/.test(run.vod_id);

function Consequences({ own }: { own: boolean }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
      <li>The clips, the transcript, the chat and the clip log are kept.</li>
      <li>Edit, New version and posting work as before. They use the clips' own files.</li>
      {own ? (
        <>
          <li className="text-warn">
            ⚠️ This is your own file. The file in work/ is the only copy in the app. Run again does not work afterwards.
          </li>
          <li>The campaign's chat cannot play the video or cut new clips from it.</li>
        </>
      ) : (
        <>
          <li>Run again downloads the whole VOD from Twitch again.</li>
          <li>The campaign's chat cannot play the video. New clips from the chat only download their part from Twitch.</li>
        </>
      )}
      <li>Recutting clips in a new layout (python -m campaign reframe) needs the VOD file.</li>
    </ul>
  );
}

/** Frågan med vad som slutar fungera, och borttagningen. onDone får antalet byte som frigjordes. */
function DeleteVodDialog({ run, bytes, onClose, onDone }: { run: VodRun; bytes: number; onClose: () => void; onDone: (freed: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      onDone((await deleteRunVod(run.run_id)).freed_bytes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the VOD file");
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="gap-4 p-5 sm:max-w-md">
        <div>
          <DialogTitle className="text-lg font-semibold">Delete the VOD file?</DialogTitle>
          <DialogDescription className="mt-1">
            It frees {diskSize(bytes)} on disk. Only the VOD file and the download parts are deleted.
          </DialogDescription>
        </div>
        <Consequences own={isOwnFile(run)} />
        {error && (
          <p role="alert" className="text-sm text-[#fca5a5]">
            {error}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" className="h-9" onClick={onClose} disabled={busy}>
            No, keep it
          </Button>
          <Button variant="destructive" className="h-9" onClick={remove} disabled={busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Yes, delete the VOD file
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Ta bort projektets VOD-fil och delar för att frigöra plats på disken. Frågar först och säger vad som slutar
 * fungera. Visas bara när filen finns och projektet inte pågår.
 */
export function DeleteVod({ run, onDeleted, small }: { run: VodRun; onDeleted?: () => void; small?: boolean }) {
  const [open, setOpen] = useState(false);
  const [freed, setFreed] = useState<number | null>(null);
  const bytes = run.vod_bytes;
  const running = run.status === "pending" || run.status === "running";

  if (freed !== null) {
    return (
      <p role="status" className="text-[13px] text-good">
        The VOD file is deleted. {freed ? `${diskSize(freed)} freed.` : "It was already deleted."}
      </p>
    );
  }
  if (!bytes || running) return null;

  return (
    <>
      <Button variant="outline" className={cn(small ? "h-7 text-[13px]" : "h-9")} onClick={() => setOpen(true)}>
        <HardDrive aria-hidden />
        Delete the VOD file, {diskSize(bytes)}
      </Button>
      {open && (
        <DeleteVodDialog
          run={run}
          bytes={bytes}
          onClose={() => setOpen(false)}
          onDone={(n) => {
            setFreed(n);
            setOpen(false);
            onDeleted?.();
          }}
        />
      )}
    </>
  );
}
