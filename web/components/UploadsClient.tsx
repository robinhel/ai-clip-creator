"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DropZone } from "@/components/uploads/DropZone";
import { OwnClips } from "@/components/uploads/OwnClips";
import { SavePanel } from "@/components/uploads/SavePanel";
import { LoadError } from "@/components/uploads/ui";
import { useLeaveWarning } from "@/components/uploads/useLeaveWarning";
import { isUnsaved, isWaiting, itemProblem, rememberChoices, useItems, type Item } from "@/components/uploads/useItems";
import { useUploadData, useUploadOptions } from "@/components/uploads/useUploadData";
import { VideoEditor, type EditorActions } from "@/components/uploads/VideoEditor";
import { VideoList } from "@/components/uploads/VideoList";
import { takeHandedOverFiles } from "@/lib/handoff";
import { START_CHOICES, alreadyWaiting, frameQuery } from "@/lib/uploads";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";

const showsThumb = (item: Item) => !!item.draft && item.state !== "done";

/** Den lilla bilden i listan: första bilden med videons bildläge. */
function thumbUrl(item: Item, profile: string): string | null {
  if (!showsThumb(item) || !profile) return null;
  return uploadsApi.frameUrl(item.draft!.draft_id, frameQuery({ ...START_CHOICES, framing: item.choices.framing }, profile, "", 0));
}

/** Andra videor som inte är klara, för Använd på alla. */
const othersThan = (items: Item[], current: Item | null) => items.filter((i) => i.key !== current?.key && i.state !== "done").length;

/** Utan valen ännu: som förut. Kräver kampanjen sin text bränns rubriken inte in. */
const burnsTitleOf = (opts: DraftOptions | null) => opts?.hook_text ?? true;

/** Det första som stoppar Gör om N videor, med videons namn. */
function firstBlock(items: Item[], opts: DraftOptions | null): string | null {
  for (const item of items.filter(isWaiting)) {
    const { problem } = itemProblem(item, opts);
    if (problem) return `${item.file.name}: ${problem}`;
  }
  return null;
}

/** Spara en video eller alla som väntar, en i taget. made räknar klara klipp i omgången. */
function useSaving(list: ReturnType<typeof useItems>, profile: string, loadClips: () => void) {
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState(0);

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
    for (const item of list.items.filter(isWaiting)) await saveOne(item);
    setBusy(false);
  }

  return { busy, made, saveOne, makeAll };
}

/** Videorna, den valda videon och kampanjens senaste val. */
function useSelection(list: ReturnType<typeof useItems>, profile: string) {
  const { items, add, changeProfile } = list;
  const [selected, setSelected] = useState<string | null>(null);
  const current = items.find((i) => i.key === selected) ?? items[0] ?? null;

  function addFiles(files: ArrayLike<File> | null) {
    const added = add(files, profile);
    if (added.length > 0) setSelected((key) => key ?? added[0].key);
  }

  useEffect(() => {
    // Videor som Robin drog in på Hem. Valen följer kampanjen när den är vald.
    const added = add(takeHandedOverFiles(), "");
    if (added.length > 0) setSelected(added[0].key);
  }, [add]);

  // Byte av kampanj: overlayn börjar om, och nya videor får kampanjens senaste val.
  useEffect(() => {
    if (profile) changeProfile(profile);
  }, [profile, changeProfile]);

  return { current, setSelected, addFiles };
}

interface VideosProps {
  list: ReturnType<typeof useItems>;
  current: Item | null;
  profile: string;
  burnsTitle: boolean;
  onSelect: (key: string) => void;
  onFiles: (files: ArrayLike<File> | null) => void;
}

/** Dra in videor och listan med videorna. */
function Videos({ list, current, profile, burnsTitle, onSelect, onFiles }: VideosProps) {
  const { items } = list;
  return (
    <div className="min-w-0 space-y-3">
      <DropZone onFiles={onFiles} rejected={list.rejected} />
      {items.length > 0 && !burnsTitle && (
        <p className="text-xs text-muted-foreground">Kampanjen bränner inte in rubriken i videon. Rubriken hamnar bara först i captionen.</p>
      )}
      {items.length > 0 && (
        <VideoList
          items={items}
          selected={current?.key ?? null}
          burnsTitle={burnsTitle}
          thumbFor={(item) => thumbUrl(item, profile)}
          onSelect={onSelect}
          onTitle={(key, title) => list.update(key, { title })}
          onRemove={list.remove}
          onRetry={list.retryUpload}
          onRemoveDone={list.removeDone}
        />
      )}
    </div>
  );
}

export function UploadsClient() {
  const { profiles, profile, setProfile, clips, setClips, loadError, profilesLoaded, load, loadClips } = useUploadData();
  const { opts, error: optsError, retry: retryOpts } = useUploadOptions(profile);
  const list = useItems();
  const { items } = list;
  const { current, setSelected, addFiles } = useSelection(list, profile);
  const { busy, made, saveOne, makeAll } = useSaving(list, profile, loadClips);
  const [showSafe, setShowSafe] = useState(true);

  useEffect(load, [load]);

  const actions: EditorActions = {
    setChoice: (key, name, value) => {
      list.setChoice(key, name, value);
      // De senaste valen sparas per kampanj när Robin ändrar ett val, inte när han bara väljer en video.
      const item = items.find((i) => i.key === key);
      if (item) rememberChoices(profile, { ...item.choices, [name]: value });
    },
    setTimes: list.setTimes,
    setAt: list.setAt,
    preview: (item) => list.preview(item, profile),
    save: saveOne,
    applyToAll: list.applyToAll,
  };

  const approved = clips.filter((c) => c.status === "godkand").length;
  useLeaveWarning(busy || items.some(isUnsaved));
  const burnsTitle = burnsTitleOf(opts);

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
        <Videos list={list} current={current} profile={profile} burnsTitle={burnsTitle} onSelect={setSelected} onFiles={addFiles} />
        <SavePanel
          profiles={profiles}
          profile={profile}
          onProfile={setProfile}
          profilesLoaded={profilesLoaded}
          source={opts?.source_allowed}
          busy={busy}
          waiting={items.filter(isWaiting).length}
          uploading={items.filter((i) => i.upload === "uploading").length}
          made={made}
          block={firstBlock(items, opts)}
          onMakeAll={makeAll}
        />
      </section>

      {current ? (
        <VideoEditor
          item={current}
          profile={profile}
          opts={opts}
          optsError={optsError}
          onOptsRetry={retryOpts}
          busy={busy}
          others={othersThan(items, current)}
          showSafe={showSafe}
          onShowSafe={setShowSafe}
          actions={actions}
        />
      ) : (
        optsError && <LoadError error={optsError} onRetry={retryOpts} />
      )}

      <OwnClips
        clips={clips}
        profiles={profiles}
        formats={profiles.find((p) => p.name === profile)?.formats ?? []}
        loadError={loadError}
        onUpdate={(changed) => setClips((all) => all.map((c) => (c.id === changed.id ? changed : c)))}
        onReload={loadClips}
      />
    </main>
  );
}
