"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CampaignProfile, CampaignRunInfo, Run } from "@/lib/types";

interface Props {
  onCreated: (run: Run, info: CampaignRunInfo) => void;
}

/** "90", "1:30" eller "1:02:30" till sekunder. Tom text ger undefined, fel format NaN. */
export function parseTime(value: string): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  if (!/^\d+(:\d{1,2}){0,2}$/.test(text)) return NaN;
  return text.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

/** VOD-id ur ett id eller en länk som twitch.tv/videos/2345678901. */
export function parseVodId(value: string): string | null {
  const text = value.trim();
  if (/^\d{6,15}$/.test(text)) return text;
  return text.match(/videos\/(\d{6,15})/)?.[1] ?? null;
}

export function NewRunDialog({ onCreated }: Props) {
  const [open, setOpen] = useState(false);
  const [profiles, setProfiles] = useState<CampaignProfile[] | null>(null);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [profileName, setProfileName] = useState("");
  const [vodId, setVodId] = useState("");
  const [game, setGame] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api.campaign
      .profiles()
      .then((data) => {
        if (cancelled) return;
        setProfiles(data);
        setProfilesError(null);
        setProfileName((current) => current || (data.find((p) => p.ready) ?? data[0])?.name || "");
      })
      .catch((err) => {
        if (!cancelled) setProfilesError(err instanceof Error ? err.message : "Kunde inte hämta kampanjerna");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const profile = profiles?.find((p) => p.name === profileName);
  const vod = parseVodId(vodId);
  const vodError = vodId.trim() && !vod ? "Skriv VOD-id (6–15 siffror) eller klistra in länken till VOD:en." : null;
  const startS = parseTime(start);
  const endS = parseTime(end);
  const timeError =
    Number.isNaN(startS) || Number.isNaN(endS)
      ? "Skriv tiden som sekunder eller t:mm:ss."
      : startS !== undefined && endS !== undefined && endS <= startS
        ? "Sluttiden måste vara efter starttiden."
        : null;
  const canSubmit = !!profile?.ready && !!vod && !timeError && !loading;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setVodId("");
      setGame("");
      setStart("");
      setEnd("");
      setError(null);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !profile || !vod) return;
    setLoading(true);
    setError(null);
    const body = {
      profile: profile.name,
      vod_id: vod,
      game: game.trim() || undefined,
      start_s: startS,
      end_s: endS,
    };
    try {
      const run = await api.campaign.runs.create(body);
      onCreated(run, { run_id: run.id, profile: body.profile, game: body.game ?? null, vod_id: body.vod_id });
      handleOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kunde inte starta körningen");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={<Button />}>Ny körning</DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Ny körning</DialogTitle>
          <DialogDescription>
            Verktyget hämtar VOD:en, letar fram bra stunder och gör kampanjklara klipp.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="run-profile" className="text-sm font-medium">
              Kampanj
            </label>
            {profilesError ? (
              <p className="text-sm text-destructive">{profilesError}</p>
            ) : profiles === null ? (
              <p className="text-sm text-muted-foreground">Hämtar kampanjer…</p>
            ) : profiles.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Det finns inga kampanjer. Lägg en profil i mappen profiles/.
              </p>
            ) : (
              <NativeSelect
                id="run-profile"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              >
                {profiles.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.campaign || p.name}
                    {p.ready ? "" : " (går inte att köra)"}
                  </option>
                ))}
              </NativeSelect>
            )}
            {profile && (
              <p className="text-xs text-muted-foreground">
                {[profile.streamer, profile.channel && `twitch.tv/${profile.channel}`, profile.language]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
            {profile && profile.problems.length > 0 && (
              <div
                className={cn(
                  "rounded-lg border p-3 text-xs space-y-1",
                  profile.ready
                    ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
                    : "border-destructive/40 bg-destructive/5 text-destructive"
                )}
              >
                <p className="flex items-center gap-1.5 font-medium">
                  <AlertTriangle className="size-3.5" />
                  {profile.ready ? "Det här saknas i kampanjen" : "Kampanjen går inte att köra"}
                </p>
                <ul className="list-disc pl-5 space-y-0.5">
                  {profile.problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <div className="space-y-1.5">
            <label htmlFor="run-vod" className="text-sm font-medium">
              VOD-id eller länk
            </label>
            <Input
              id="run-vod"
              placeholder="t.ex. 2345678901"
              value={vodId}
              onChange={(e) => setVodId(e.target.value)}
              required
              autoFocus
              aria-invalid={!!vodError}
            />
            {vodError && <p className="text-xs text-destructive">{vodError}</p>}
          </div>
          <div className="space-y-1.5">
            <label htmlFor="run-game" className="text-sm font-medium">
              Spelets namn <span className="font-normal text-muted-foreground">(valfritt)</span>
            </label>
            <Input
              id="run-game"
              placeholder="t.ex. Fortnite"
              value={game}
              onChange={(e) => setGame(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label htmlFor="run-start" className="text-sm font-medium">
                Start <span className="font-normal text-muted-foreground">(valfri)</span>
              </label>
              <Input
                id="run-start"
                inputMode="numeric"
                placeholder="0:00:00"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="run-end" className="text-sm font-medium">
                Slut <span className="font-normal text-muted-foreground">(valfri)</span>
              </label>
              <Input
                id="run-end"
                inputMode="numeric"
                placeholder="hela VOD:en"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
          </div>
          {timeError && <p className="text-sm text-destructive">{timeError}</p>}
          {error && (
            <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Avbryt
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {loading ? "Startar…" : "Starta"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
