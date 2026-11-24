import type { Metadata } from "next";
import { ProjectsClient } from "@/components/ProjectsClient";

export const metadata: Metadata = { title: "Projects · Clips" };

export default function ProjectsPage() {
  return <ProjectsClient />;
}
