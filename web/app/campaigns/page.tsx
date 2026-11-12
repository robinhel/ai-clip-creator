import type { Metadata } from "next";
import { CampaignsClient } from "@/components/CampaignsClient";
import { TikTokCard } from "@/components/TikTokCard";

export const metadata: Metadata = { title: "Kampanjer · Klipp" };

type SearchParams = Promise<{ tiktok?: string | string[]; orsak?: string | string[] }>;

export default async function CampaignsPage({ searchParams }: { searchParams: SearchParams }) {
  const { tiktok, orsak } = await searchParams;
  return (
    <CampaignsClient>
      <TikTokCard result={String(tiktok ?? "")} reason={String(orsak ?? "")} />
    </CampaignsClient>
  );
}
