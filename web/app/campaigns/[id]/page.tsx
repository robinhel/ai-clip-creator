import type { Metadata } from "next";
import { CampaignDetailClient } from "@/components/CampaignDetailClient";

export const metadata: Metadata = { title: "Kampanj · Klipp" };

type Props = { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string | string[] }> };

export default async function CampaignPage({ params, searchParams }: Props) {
  const [{ id }, { tab }] = await Promise.all([params, searchParams]);
  return <CampaignDetailClient id={id} tab={typeof tab === "string" ? tab : undefined} />;
}
