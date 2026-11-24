"use client";

import Link from "next/link";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { CampaignProfile } from "@/lib/types";
import { ProfileField } from "./ChoiceFields";

function makeLabel(busy: boolean, waiting: number): string {
  if (busy) return "Making…";
  return waiting > 1 ? `Make ${waiting} videos` : "Make the video";
}

/** Texten under knappen när videorna är klara, med länken till Klippen. */
function MadeNote({ made }: { made: number }) {
  return (
    <p role="status" className="text-xs text-good">
      {made === 1 ? "1 clip is done." : `${made} clips are done.`} Review below, or in{" "}
      <Link href="/queue?tab=granska" className="underline underline-offset-4">
        Clips, To review
      </Link>
      .
    </p>
  );
}

function NoCampaign() {
  return (
    <p className="text-xs text-muted-foreground">
      No active campaign.{" "}
      <Link href="/campaigns" className="underline underline-offset-4 hover:text-foreground">
        Turn one on under Campaigns
      </Link>
      .
    </p>
  );
}

interface Props {
  profiles: CampaignProfile[];
  profile: string;
  onProfile: (name: string) => void;
  profilesLoaded: boolean;
  source: string | null | undefined;
  busy: boolean;
  waiting: number;
  uploading: number;
  made: number;
  /** Det första som stoppar knappen, med videons namn. */
  block: string | null;
  onMakeAll: () => void;
}

/** Knappen går inte när något pågår, inget väntar, kampanj saknas eller en video har fel val. */
const cannotMake = ({ busy, waiting, profile, block }: Props) => busy || waiting === 0 || !profile || !!block;

/** Texterna under knappen. */
function PanelNotes({ profile, profilesLoaded, busy, made, block }: Props) {
  return (
    <>
      {profilesLoaded && !profile && <NoCampaign />}
      {!busy && made > 0 && <MadeNote made={made} />}
      {block && <p className="text-xs break-words text-[#fca5a5]">{block}</p>}
    </>
  );
}

/** Kampanjen och knappen Gör om N videor, som sparar alla videor som väntar. */
export function SavePanel(props: Props) {
  const { profiles, profile, onProfile, source, busy, waiting, uploading, onMakeAll } = props;
  return (
    <div className="min-w-0 space-y-4">
      <ProfileField profiles={profiles} profile={profile} onProfile={onProfile} source={source} busy={busy} />
      <Button className="h-10 w-full" disabled={cannotMake(props)} onClick={onMakeAll}>
        {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
        {makeLabel(busy, waiting)}
      </Button>
      <p className="text-xs text-muted-foreground">
        Saves all waiting videos, one at a time, with the choices for each video. Videos with errors are redone on the next press.
        {uploading > 0 && ` ${uploading} still uploading.`}
      </p>
      <PanelNotes {...props} />
    </div>
  );
}
