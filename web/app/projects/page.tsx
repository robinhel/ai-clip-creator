import type { Metadata } from "next";
import { ProjectsClient } from "@/components/ProjectsClient";

export const metadata: Metadata = { title: "Projekt · Klipp" };

export default function ProjectsPage() {
  return <ProjectsClient />;
}
