"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProfileField } from "@/components/uploads/ChoiceFields";
import { DropZone } from "@/components/uploads/DropZone";
import { OwnClips } from "@/components/uploads/OwnClips";
import { LoadError, errorText } from "@/components/uploads/ui";
import { useLeaveWarning } from "@/components/uploads/useLeaveWarning";
import { isUnsaved, isWaiting, itemProblem, rememberChoices, useItems, type Item } from "@/components/uploads/useItems";
import { VideoEditor, type EditorActions } from "@/components/uploads/VideoEditor";
import { VideoList } from "@/components/uploads/VideoList";
import { api } from "@/lib/api";
import { takeHandedOverFiles } from "@/lib/handoff";
import { START_CHOICES, alreadyWaiting, frameQuery } from "@/lib/uploads";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";
import type { CampaignClip, CampaignProfile } from "@/lib/types";

function makeLabel(busy: boolean, waiting: number): string {
  if (busy) return "Gör om…";
  return waiting > 1 ? `Gör om ${waiting} videor` : "Gör om videon";
}

/** Texten under knappen när videorna är klara, med länken till Klippen. */
function MadeNote({ made }: { made: number }) {
  return (
    <p role="status" className="text-xs text-good">
      {made === 1 ? "1 klipp är klart." : `${made} klipp är klara.`} Granska nedan, eller i{" "}
      <Link href="/queue?tab=granska" className="underline underline-offset-4">
        Klippen, Att granska
      </Link>
      .
    </p>
  );
}

function NoCampaign() {
  return (
    <p className="text-xs text-muted-foreground">
      Ingen aktiv kampanj.{" "}
      <Link href="/campaigns" className="underline underline-offset-4 hover:text-foreground">
        Slå på en under Kampanjer
      </Link>
      .
    </p>
  );
}

/** Kampanjerna och de egna klippen. ?kampanj=<id> väljer kampanjen, till exempel från kampanjsidan. */
function useUploadData() {
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
function useUploadOptions(profile: string) {
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

/** Den lilla bilden i listan: första bilden med videons bildläge. */
function thumbUrl(item: Item, profile: string): string | null {
  if (!item.draft || !profile || item.state === "done") return null;
  return uploadsApi.frameUrl(item.draft.draft_id, frameQuery({ ...START_CHOICES, framing: item.choices.framing }, profile, "", 0));
}

/** Det första som stoppar Gör om N videor, med videons namn. */
function firstBlock(items: Item[], opts: DraftOptions | null): string | null {
  for (const item of items.filter(isWaiting)) {
    const { problem } = itemProblem(item, opts);
    if (problem) return `${item.file.name}: ${problem}`;
  }
  return null;
}

export function UploadsClient() {
  const { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips } = useUploadData();
  const { opts, error: optsError, retry: retryOpts } = useUploadOptions(profile);
  const list = useItems();
  const { items, add, changeProfile } = list;
  const [selected, setSelected] = useState<string | null>(null);
  const [showSafe, setShowSafe] = useState(true);
  const [busy, setBusy] = useState(false);
  // Klipp som blev klara i den här omgången, för länken till Klippen.
  const [made, setMade] = useState(0);
  const current = items.find((i) => i.key === selected) ?? items[0] ?? null;

  function addFiles(files: ArrayLike<File> | null) {
    const added = add(files, profile);
    if (added.length > 0) setSelected((key) => key ?? added[0].key);
  }

  useEffect(() => {
    load();
    // Videor som Robin drog in på Hem. Valen följer kampanjen när den är vald.
    const added = add(takeHandedOverFiles(), "");
    if (added.length > 0) setSelected(added[0].key);
  }, [load, add]);

  // Byte av kampanj: overlayn börjar om, och nya videor får kampanjens senaste val.
  useEffect(() => {
    if (profile) changeProfile(profile);
  }, [profile, changeProfile]);

  // De senaste valen sparas per kampanj när Robin ändrar dem.
  const currentChoices = current && !current.fresh ? current.choices : null;
  useEffect(() => {
    if (currentChoices) rememberChoices(profile, currentChoices);
  }, [currentChoices, profile]);

  async function saveOne(item: Item) {
    if (await list.save(item, profile)) {
      setMade((n) => n + 1);
      loadClips();
    }
  }

  async function makeAll() {
    setBusy(true);
    setMade(0);
    // En i taget: servern kodar om varje video, och det tar datorns kraft.
    for (const item of items.filter(isWaiting)) await saveOne(item);
    setBusy(false);
  }

  const actions: EditorActions = {
    setChoice: list.setChoice,
    setTimes: list.setTimes,
    setAt: list.setAt,
    preview: (item) => list.preview(item, profile),
    save: saveOne,
    applyToAll: list.applyToAll,
  };

  const approved = clips.filter((c) => c.status === "godkand").length;
  useLeaveWarning(busy || items.some(isUnsaved));
  const formats = profiles.find((p) => p.name === profile)?.formats ?? [];
  const waiting = items.filter(isWaiting).length;
  const uploading = items.filter((i) => i.upload === "uploading").length;
  // Utan valen ännu: som förut. Kräver kampanjen sin text bränns rubriken inte in.
  const burnsTitle = opts?.hook_text ?? true;
  const block = firstBlock(items, opts);
  const others = current ? items.filter((i) => i.key !== current.key && i.state !== "done").length : 0;

  return (
    <main className="w-full max-w-[1180px] px-4 py-9 sm:px-6 md:px-11">
      <h1 className="text-3xl font-bold tracking-tight">Egna videor</h1>
      <p className="mt-1.5 text-muted-foreground">
        Dra in videor. Välj en video för att se bilden och ändra valen. Säg ja till ett klipp, så hamnar det i{" "}
        <Link href="/queue?tab=posta" className="text-brand underline-offset-4 hover:underline">
          Klippen, Att posta
        </Link>
        {alreadyWaiting(approved)}.
      </p>

      {loadError && <LoadError error={loadError} onRetry={load} />}

      <section className="mt-6 grid gap-6 rounded-2xl border border-line bg-panel p-4 sm:p-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-3">
          <DropZone onFiles={addFiles} rejected={list.rejected} />
          {items.length > 0 && !burnsTitle && (
            <p className="text-xs text-muted-foreground">
              Kampanjen bränner inte in rubriken i videon. Rubriken hamnar bara först i captionen.
            </p>
          )}
          {items.length > 0 && (
            <VideoList
              items={items}
              selected={current?.key ?? null}
              burnsTitle={burnsTitle}
              thumbFor={(item) => thumbUrl(item, profile)}
              onSelect={setSelected}
              onTitle={(key, title) => list.update(key, { title })}
              onRemove={list.remove}
              onRetry={list.retryUpload}
              onRemoveDone={list.removeDone}
            />
          )}
        </div>

        <div className="min-w-0 space-y-4">
          <ProfileField profiles={profiles} profile={profile} onProfile={setProfile} source={opts?.source_allowed} busy={busy} />
          <Button className="h-10 w-full" disabled={busy || waiting === 0 || !profile || !!block} onClick={makeAll}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
            {makeLabel(busy, waiting)}
          </Button>
          <p className="text-xs text-muted-foreground">
            Sparar alla videor som väntar, en i taget, med valen för varje video. Videor med fel görs om vid nästa tryck.
            {uploading > 0 && ` ${uploading} laddas fortfarande upp.`}
          </p>
          {profilesLoaded && !profile && <NoCampaign />}
          {!busy && made > 0 && <MadeNote made={made} />}
          {block && <p className="text-xs break-words text-[#fca5a5]">{block}</p>}
        </div>
      </section>

      {current && (
        <VideoEditor
          item={current}
          profile={profile}
          opts={opts}
          optsError={optsError}
          onOptsRetry={retryOpts}
          busy={busy}
          others={others}
          showSafe={showSafe}
          onShowSafe={setShowSafe}
          actions={actions}
        />
      )}
      {!current && optsError && <LoadError error={optsError} onRetry={retryOpts} />}

      <OwnClips
        clips={clips}
        profiles={profiles}
        formats={formats}
        loadError={loadError}
        onUpdate={(changed) => setClips((all) => all.map((c) => (c.id === changed.id ? changed : c)))}
        onReload={loadClips}
      />
    </main>
  );
}
