import type { Metadata } from "next";
import { NewCampaignClient } from "@/components/NewCampaignClient";

export const metadata: Metadata = { title: "Ny kampanj · Klipp" };

type Props = { searchParams: Promise<{ channel?: string | string[]; streamer?: string | string[] }> };

export default async function NewCampaignPage({ searchParams }: Props) {
  const { channel, streamer } = await searchParams;
  return (
    <NewCampaignClient
      channel={typeof channel === "string" ? channel : ""}
      streamer={typeof streamer === "string" ? streamer : ""}
    />
  );
}
