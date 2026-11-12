import type { Metadata } from "next";
import { QueueClient } from "@/components/QueueClient";

export const metadata: Metadata = { title: "Att posta · Klipp" };

export default function QueuePage() {
  return <QueueClient />;
}
