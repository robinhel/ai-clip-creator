import type { Metadata } from "next";
import { SettingsClient } from "@/components/SettingsClient";

export const metadata: Metadata = { title: "Inställningar · Klipp" };

export default function SettingsPage() {
  return <SettingsClient />;
}
