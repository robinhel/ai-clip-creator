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
const one = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 });

/** "6,2 GB", eller "350 MB" under 1 GB. */
export function diskSize(bytes: number): string {
  return bytes >= GB ? `${one.format(bytes / GB)} GB` : `${Math.max(1, Math.round(bytes / MB))} MB`;
}

/** En egen fil har filens namn som VOD-id. Twitch-VOD:ar har bara siffror. */
const isOwnFile = (run: VodRun) => !/^\d+$/.test(run.vod_id);

function Consequences({ own }: { own: boolean }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
      <li>Klippen, transkriptet, chatten och klipploggen finns kvar.</li>
      <li>Redigera, Ny version och postningen fungerar som förut. De använder klippens egna filer.</li>
      {own ? (
        <>
          <li className="text-warn">
            ⚠️ Det här är en egen fil. Filen i work/ är den enda kopian i appen. Kör igen går inte efteråt.
          </li>
          <li>Kampanjens chatt kan inte spela upp videon eller klippa nya klipp ur den.</li>
        </>
      ) : (
        <>
          <li>Kör igen hämtar hela VOD:en från Twitch igen.</li>
          <li>Kampanjens chatt kan inte spela upp videon. Nya klipp ur chatten hämtar bara sin del från Twitch.</li>
        </>
      )}
      <li>Att skära om klipp i ett nytt bildläge (python -m campaign reframe) kräver VOD-filen.</li>
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
      setError(err instanceof Error ? err.message : "Kunde inte ta bort VOD-filen");
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="gap-4 p-5 sm:max-w-md">
        <div>
          <DialogTitle className="text-lg font-semibold">Ta bort VOD-filen?</DialogTitle>
          <DialogDescription className="mt-1">
            Det frigör {diskSize(bytes)} på disken. Bara VOD-filen och delarna från hämtningen tas bort.
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
            Nej, behåll den
          </Button>
          <Button variant="destructive" className="h-9" onClick={remove} disabled={busy}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            Ja, ta bort VOD-filen
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
        VOD-filen är borttagen. {freed ? `${diskSize(freed)} frigjordes.` : "Den var redan borttagen."}
      </p>
    );
  }
  if (!bytes || running) return null;

  return (
    <>
      <Button variant="outline" className={cn(small ? "h-7 text-[13px]" : "h-9")} onClick={() => setOpen(true)}>
        <HardDrive aria-hidden />
        Ta bort VOD-filen, {diskSize(bytes)}
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
