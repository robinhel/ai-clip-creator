import type { Metadata } from "next";
import { CampaignsClient } from "@/components/CampaignsClient";

export const metadata: Metadata = { title: "Kampanjer · Klipp" };

export default function CampaignsPage() {
  return <CampaignsClient />;
}
