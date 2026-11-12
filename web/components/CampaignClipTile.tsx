import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { CampaignStatusBadge } from "@/components/CampaignStatusBadge";
import { api } from "@/lib/api";
import type { CampaignClip, CampaignStatus } from "@/lib/types";

// Statusar som har en egen del i Kön. Övriga klipp följs i klipploggen.
const IN_QUEUE: CampaignStatus[] = ["renderad", "underkand", "godkand", "postad"];

/** Kompakt kort för ett kampanjklipp på körningens sida. Besluten tas i Kön. */
export function CampaignClipTile({ clip }: { clip: CampaignClip }) {
  const inQueue = IN_QUEUE.includes(clip.status);
  return (
    <Card size="sm" className="pt-0" data-clip-id={clip.id}>
      <div className="aspect-[9/16] w-full bg-black">
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
      <CardContent className="flex flex-1 flex-col gap-2">
        <CampaignStatusBadge status={clip.status} />
        <p className="font-medium leading-snug break-words">
          {clip.title || <em className="text-muted-foreground">utan titel</em>}
        </p>
        <Link
          href={inQueue ? "/queue" : "/log"}
          className="mt-auto text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          {inQueue ? "Öppna i kön" : "Se i klipploggen"}
        </Link>
      </CardContent>
    </Card>
  );
}
