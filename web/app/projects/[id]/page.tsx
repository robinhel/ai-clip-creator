import type { Metadata } from "next";
import { ProjectClient } from "@/components/ProjectClient";

export const metadata: Metadata = { title: "Project · Clips" };

type Props = { params: Promise<{ id: string }> };

/** Kampanjens projekt för egna filer: filerna och alla klippen. */
export default async function ProjectPage({ params }: Props) {
  const { id } = await params;
  return <ProjectClient id={Number(id)} />;
}
