import type { Metadata } from "next";
import { ScoutClient } from "@/components/ScoutClient";

export const metadata: Metadata = { title: "Kampanjbevakning · Klipp" };

export default function ScoutPage() {
  return <ScoutClient />;
}
