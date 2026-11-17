import type { Metadata } from "next";
import { NewCampaignClient, fromQuery } from "@/components/NewCampaignClient";
import type { Platform } from "@/lib/types";

export const metadata: Metadata = { title: "Ny kampanj · Klipp" };

type Query = string | string[] | undefined;
type Keys = "channel" | "streamer" | "campaign" | "source" | "cpm" | "min_views" | "max_payout" | "platforms" | "scout";
type Props = { searchParams: Promise<Partial<Record<Keys, Query>>> };

const one = (value: Query) => (typeof value === "string" ? value : undefined);
const PLATFORMS: Platform[] = ["tiktok", "youtube_shorts", "instagram_reels"];

/** Hem fyller i ?channel= och ?streamer=. Bevakning fyller i namn, betalning, plattformar och ?scout=. */
export default async function NewCampaignPage({ searchParams }: Props) {
  const q = await searchParams;
  const platforms = (one(q.platforms) ?? "").split(",").filter((p): p is Platform => PLATFORMS.includes(p as Platform));
  return (
    <NewCampaignClient
      channel={one(q.channel) ?? ""}
      streamer={one(q.streamer) ?? ""}
      campaign={one(q.campaign)}
      source={one(q.source) === "local" ? "local" : "twitch"}
      cpm={fromQuery(one(q.cpm))}
      minViews={one(q.min_views)?.match(/^\d+$/) ? one(q.min_views) : undefined}
      maxPayout={fromQuery(one(q.max_payout))}
      platforms={platforms}
      scoutRef={one(q.scout)}
    />
  );
}
