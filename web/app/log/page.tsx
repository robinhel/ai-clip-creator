import type { Metadata } from "next";
import { ClipLogClient } from "@/components/ClipLogClient";
import { serverApi } from "@/lib/server-api";

export const metadata: Metadata = { title: "Statistik · Klipp" };

// Data hämtas vid varje besök, så att sidan syns med innehåll innan JavaScript har laddats.
export const dynamic = "force-dynamic";

export default async function LogPage() {
  const [summary, clips] = await Promise.all([serverApi.summary(), serverApi.clips()]);
  return <ClipLogClient initialSummary={summary} initialClips={clips} />;
}
