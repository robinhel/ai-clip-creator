"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import type { CampaignProfile } from "@/lib/types";

export function CampaignsClient() {
  const [profiles, setProfiles] = useState<CampaignProfile[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaign
      .profiles()
      .then(setProfiles)
      .catch((err) => setError(err instanceof Error ? err.message : "Kunde inte hämta kampanjerna"));
  }, []);

  return (
    <main className="w-full max-w-[1180px] px-6 py-9 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Kampanjer</h1>
      <p className="mt-1.5 mb-6 text-muted-foreground">
        Varje kampanj hör till en Twitch-kanal. En VOD kan bara bli klipp för kampanjer med samma kanal.
      </p>
      {error && (
        <p role="alert" className="rounded-lg border border-[#4d1d1d] bg-[#1c0f10] px-3 py-2 text-sm text-[#fca5a5]">
          {error}
        </p>
      )}
      {profiles === null && !error && <p className="text-sm text-muted-foreground">Hämtar kampanjerna…</p>}
      {profiles?.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted-foreground">
          Inga kampanjer än. Lägg en kampanjprofil i mappen profiles på servern.
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {profiles?.map((p) => (
          <article key={p.name} className="rounded-2xl border border-line bg-panel p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold">{p.streamer}</h2>
                <p className="text-sm break-words text-muted-foreground">{p.campaign}</p>
              </div>
              {p.ready && p.problems.length === 0 ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-[#174a2a] px-2.5 py-0.5 text-xs text-good">
                  <CheckCircle2 className="size-3.5" aria-hidden />
                  Redo
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full border border-[#5a4416] px-2.5 py-0.5 text-xs text-warn">
                  <AlertTriangle className="size-3.5" aria-hidden />
                  {p.ready ? "Redo, med varningar" : "Inte redo"}
                </span>
              )}
            </div>
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Kanal</dt>
              <dd>twitch.tv/{p.channel}</dd>
              <dt className="text-muted-foreground">Språk</dt>
              <dd>{p.language}</dd>
              <dt className="text-muted-foreground">Klipplängd</dt>
              <dd className="tabular-nums">
                {p.clip_min_s != null && p.clip_max_s != null ? `${p.clip_min_s}–${p.clip_max_s} s` : "okänd"}
              </dd>
              <dt className="text-muted-foreground">Format</dt>
              <dd>{p.formats.length ? p.formats.map((f) => f.name).join(", ") : "inga"}</dd>
            </dl>
            {p.problems.length > 0 && (
              <ul className="mt-4 space-y-1.5 text-sm">
                {p.problems.map((problem) => (
                  <li key={problem} className="flex gap-2 rounded-lg border border-[#5a4416] bg-[#1b160b] px-3 py-2 text-warn">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                    {problem}
                  </li>
                ))}
              </ul>
            )}
          </article>
        ))}
      </div>
    </main>
  );
}
