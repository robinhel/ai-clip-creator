import type { Metadata } from "next";
import { ProjectClient } from "@/components/ProjectClient";

export const metadata: Metadata = { title: "Projekt · Klipp" };

type Props = { params: Promise<{ id: string }> };

/** Kampanjens projekt för egna filer: filerna och alla klippen. */
export default async function ProjectPage({ params }: Props) {
  const { id } = await params;
  return <ProjectClient id={Number(id)} />;
}
