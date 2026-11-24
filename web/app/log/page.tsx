import type { Metadata } from "next";
import { ClipLogClient } from "@/components/ClipLogClient";
import { parseFilter } from "@/lib/statistik";
import { serverApi } from "@/lib/server-api";

export const metadata: Metadata = { title: "Stats · Clips" };

// Data hämtas vid varje besök, så att sidan syns med innehåll innan JavaScript har laddats.
export const dynamic = "force-dynamic";

type Query = string | string[] | undefined;
type Props = { searchParams: Promise<{ status?: Query; kampanj?: Query }> };

const one = (value: Query) => (typeof value === "string" ? value : undefined);

export default async function LogPage({ searchParams }: Props) {
  const [summary, clips, { status, kampanj }] = await Promise.all([serverApi.summary(), serverApi.clips(true), searchParams]);
  return (
    <ClipLogClient
      initialSummary={summary}
      initialClips={clips}
      initialFilter={parseFilter(one(status))}
      initialCampaign={one(kampanj) ?? ""}
      now={Date.now()}
    />
  );
}
