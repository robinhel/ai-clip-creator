import type { Metadata } from "next";
import { UploadsClient } from "@/components/UploadsClient";

export const metadata: Metadata = { title: "Egna videor · Klipp" };

export default function UploadsPage() {
  return <UploadsClient />;
}
