import type { Metadata } from "next";
import { ClipLogClient } from "@/components/ClipLogClient";

export const metadata: Metadata = { title: "Klipplogg · clipsmith" };

export default function LogPage() {
  return <ClipLogClient />;
}
