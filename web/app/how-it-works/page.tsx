import type { Metadata } from "next";
import { HowItWorksClient } from "@/components/HowItWorksClient";

export const metadata: Metadata = { title: "How it works · Clips" };

export default function HowItWorksPage() {
  return <HowItWorksClient />;
}
