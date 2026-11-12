import type { Metadata } from "next";
import { QueueClient } from "@/components/QueueClient";

export const metadata: Metadata = { title: "Kön · clipsmith" };

export default function QueuePage() {
  return <QueueClient />;
}
