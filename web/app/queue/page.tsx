import type { Metadata } from "next";
import { QueueClient } from "@/components/QueueClient";

export const metadata: Metadata = { title: "Klippen · Klipp" };

type Query = string | string[] | undefined;

// ?tab=granska, ?tab=posta eller ?tab=skicka-in. Utan tab väljer sidan själv.
export default async function QueuePage({ searchParams }: { searchParams: Promise<{ tab?: Query }> }) {
  const { tab } = await searchParams;
  return <QueueClient tab={typeof tab === "string" ? tab : undefined} />;
}
