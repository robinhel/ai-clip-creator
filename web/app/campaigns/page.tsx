import type { Metadata } from "next";
import { CampaignsClient } from "@/components/CampaignsClient";

export const metadata: Metadata = { title: "Kampanjer · Klipp" };

type Props = { searchParams: Promise<{ flik?: string | string[] }> };

/** ?flik=nya öppnar Nya kampanjer, det som förut var Bevakning. */
export default async function CampaignsPage({ searchParams }: Props) {
  const { flik } = await searchParams;
  return <CampaignsClient tab={flik === "nya" ? "nya" : "mina"} />;
}
