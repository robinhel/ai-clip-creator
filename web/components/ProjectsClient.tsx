"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ProjectCard } from "@/components/ProjectCard";
import { api } from "@/lib/api";
import type { CampaignRunInfo } from "@/lib/types";

export function ProjectsClient() {
  const [runs, setRuns] = useState<CampaignRunInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaign.runs
      .list()
      .then(setRuns)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta projekten"));
  }, []);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Projekt</h1>
      <p className="mt-1.5 mb-6 text-muted-foreground">
        En stream per projekt. Öppna ett projekt för att se förloppet eller granska klippen.
      </p>
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {runs === null && !error && <p className="text-sm text-muted-foreground">Hämtar projekten…</p>}
      {runs?.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga projekt än.{" "}
          <Link href="/" className="text-brand underline-offset-4 hover:underline">
            Klistra in en Twitch-länk
          </Link>{" "}
          för att göra det första.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {runs?.map((run) => <ProjectCard key={run.run_id} run={run} />)}
      </div>
    </main>
  );
}
