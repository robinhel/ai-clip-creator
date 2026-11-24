"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";
import type { CampaignClip, CampaignProfile, MusicFile } from "@/lib/types";
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
      .catch((err) => setLoadError(errorText(err, "Could not load the uploads")));
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
      .catch((err) => setLoadError(errorText(err, "Could not load the campaigns")));
    loadClips();
  }, [loadClips]);

  return { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips };
}

/** En låt som är uppladdad till kampanjen `to`. */
export interface UploadedMusic {
  to: string;
  music: MusicFile;
}

/** Valen som kampanjen erbjuder, med tak och säker zon. Ett fel visas med Försök igen. */
export function useUploadOptions(profile: string) {
  const [opts, setOpts] = useState<DraftOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tries, setTries] = useState(0);
  const shown = useRef(profile); // kampanjen som visas nu, för en uppladdning som blir klar efter ett byte
  useEffect(() => {
    shown.current = profile;
    if (!profile) return;
    let live = true;
    setOpts(null);
    setError(null);
    uploadsApi
      .options(profile)
      .then((o) => live && setOpts(o))
      .catch((err) => live && setError(errorText(err, "Could not load the choices for the campaign.")));
    return () => {
      live = false;
    };
  }, [profile, tries]);
  /** En uppladdad låt i kampanjens lista, utan att hämta valen igen. Byttes kampanjen under uppladdningen ligger
   * låten i den gamla kampanjens mapp: listan ändras inte. Returnerar om låten lades till. */
  const addMusic = useCallback(({ to, music }: UploadedMusic) => {
    if (to !== shown.current) return false;
    setOpts((o) => o && { ...o, music: [...(o.music ?? []), music] });
    return true;
  }, []);
  return { opts, error, retry: () => setTries((n) => n + 1), addMusic };
}
