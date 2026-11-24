import type { Metadata } from "next";
import { UploadsClient } from "@/components/UploadsClient";

export const metadata: Metadata = { title: "Uploads · Clips" };

export default function UploadsPage() {
  return <UploadsClient />;
}
