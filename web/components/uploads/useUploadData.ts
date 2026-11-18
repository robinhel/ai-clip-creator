"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";
import type { CampaignClip, CampaignProfile } from "@/lib/types";
import { errorText } from "./ui";

/** Kampanjerna och de egna klippen. ?kampanj=<id> väljer kampanjen, till exempel från kampanjsidan. */
export function useUploadData() {
  const [profiles, setProfiles] = useState<CampaignProfile[]>([]);
  const [profile, setProfile] = useState("");
  const [clips, setClips] = useState<CampaignClip[]>([]);
  // Servern svarar inte: visas i stället för "Ingen aktiv kampanj" och en tom lista.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profilesLoaded, setProfilesLoaded] = useState(false);

  const loadClips = useCallback(() => {
    api.campaign.clips
      .list({ vod_id: "upload" })
      .then(setClips)
      .catch((err) => setLoadError(errorText(err, "Kunde inte hämta de egna videorna")));
  }, []);

  const load = useCallback(() => {
    setLoadError(null);
    const wanted = new URLSearchParams(window.location.search).get("kampanj");
    api.campaign
      .profiles()
      .then((list) => {
        const active = list.filter((p) => p.active);
        setProfiles(active);
        setProfile((current) => current || (active.find((p) => p.name === wanted) ?? active[0])?.name || "");
        setProfilesLoaded(true);
      })
      .catch((err) => setLoadError(errorText(err, "Kunde inte hämta kampanjerna")));
    loadClips();
  }, [loadClips]);

  return { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips };
}

/** Valen som kampanjen erbjuder, med tak och säker zon. Ett fel visas med Försök igen. */
export function useUploadOptions(profile: string) {
  const [opts, setOpts] = useState<DraftOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tries, setTries] = useState(0);
  useEffect(() => {
    if (!profile) return;
    let live = true;
    setOpts(null);
    setError(null);
    uploadsApi
      .options(profile)
      .then((o) => live && setOpts(o))
      .catch((err) => live && setError(errorText(err, "Kunde inte hämta valen för kampanjen.")));
    return () => {
      live = false;
    };
  }, [profile, tries]);
  return { opts, error, retry: () => setTries((n) => n + 1) };
}
