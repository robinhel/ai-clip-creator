import type { Metadata } from "next";
import { SettingsClient } from "@/components/SettingsClient";

export const metadata: Metadata = { title: "Inställningar · Klipp" };

type SearchParams = Promise<{ tiktok?: string | string[]; orsak?: string | string[] }>;

export default async function SettingsPage({ searchParams }: { searchParams: SearchParams }) {
  const { tiktok, orsak } = await searchParams;
  return <SettingsClient tiktokResult={String(tiktok ?? "")} tiktokReason={String(orsak ?? "")} />;
}
