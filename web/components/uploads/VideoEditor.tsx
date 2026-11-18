"use client";

import { Copy, Eye, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { frameQuery, previewOutdated, type Choices, type LengthCheck } from "@/lib/uploads";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";
import type { MusicFile } from "@/lib/types";
import { AudioFields } from "./AudioFields";
import { CampaignFields, PictureFields, type SetChoice } from "./ChoiceFields";
import { PreviewPane } from "./PreviewPane";
import { Timeline } from "./Timeline";
import { LoadError } from "./ui";
import { clipTime, itemProblem, type Item } from "./useItems";

export interface EditorActions {
  setChoice: <K extends keyof Choices>(key: string, name: K, value: Choices[K]) => void;
  setTimes: (key: string, startS: number, endS: number, atS: number) => void;
  setAt: (key: string, atS: number) => void;
  preview: (item: Item) => void;
  save: (item: Item) => void;
  applyToAll: (item: Item) => void;
  /** En uppladdad låt till kampanjens lista. */
  addMusic: (m: MusicFile) => void;
}

interface Props {
  item: Item;
  profile: string;
  opts: DraftOptions | null;
  optsError: string | null;
  onOptsRetry: () => void;
  /** Gör om N videor pågår. */
  busy: boolean;
  /** Andra videor som inte är klara, för Använd på alla. */
  others: number;
  showSafe: boolean;
  onShowSafe: (on: boolean) => void;
  actions: EditorActions;
}

function Actions({ item, blocked, locked, others, outdated, actions }: { item: Item; blocked: boolean; locked: boolean; others: number; outdated: boolean; actions: EditorActions }) {
  const off = !item.draft || locked || blocked;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" className="h-9" disabled={off} onClick={() => actions.preview(item)}>
          {item.previewing ? <Loader2 className="animate-spin" aria-hidden /> : <Eye aria-hidden />}
          Förhandsvisa
        </Button>
        <Button className="h-9" disabled={off} onClick={() => actions.save(item)}>
          {item.state === "busy" ? <Loader2 className="animate-spin" aria-hidden /> : <Save aria-hidden />}
          Spara klippet
        </Button>
      </div>
      {others > 0 && (
        <Button variant="outline" className="h-9 w-full" disabled={locked} onClick={() => actions.applyToAll(item)}>
          <Copy aria-hidden />
          Använd på alla ({others})
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        {item.preview && !outdated
          ? "Spara använder klippet du ser."
          : "Spara gör klippet med valen ovan. Förhandsvisa först om du vill se hela klippet."}{" "}
        Använd på alla kopierar valen, inte rubriken, start och slut.
      </p>
      {item.previewError && (
        <p role="alert" className="text-xs break-words text-[#fca5a5]">
          {item.previewError}
        </p>
      )}
    </div>
  );
}

function Saved({ item }: { item: Item }) {
  return (
    <section className="mt-6 rounded-2xl border border-line bg-panel p-5 text-sm" aria-label={`Valen för ${item.file.name}`}>
      <h2 className="text-lg font-semibold break-words">{item.file.name}</h2>
      <p className="mt-2 text-good">Klar: {item.message}. Klippet finns under Dina egna klipp nedan.</p>
    </section>
  );
}

function frameUrlFor(item: Item, profile: string): string | null {
  if (!item.draft || !profile) return null;
  return uploadsApi.frameUrl(item.draft.draft_id, frameQuery(item.choices, profile, item.title, clipTime(item)));
}

/** Tidslinjen när videon är uppladdad och kampanjens gränser är kända. */
function EditorTimeline({ item, opts, check, locked, actions }: { item: Item; opts: DraftOptions | null; check: LengthCheck | null; locked: boolean; actions: EditorActions }) {
  // check finns bara när utkastet och kampanjens val finns (itemProblem).
  if (!check) return <p className="text-sm text-muted-foreground">Tidslinjen visas när videon är uppladdad.</p>;
  return (
    <Timeline
      duration={item.draft!.duration_s}
      startS={item.choices.startS}
      endS={item.choices.endS}
      atS={item.atS}
      speed={item.choices.speed}
      min={opts!.min_seconds}
      max={opts!.max_seconds}
      check={check}
      disabled={locked}
      onTimes={(startS, endS, atS) => actions.setTimes(item.key, startS, endS, atS)}
      onAt={(atS) => actions.setAt(item.key, atS)}
    />
  );
}

/** Valen i två kolumner: bilden till vänster, det kampanjen styr till höger. */
function EditorFields({ item, opts, profile, locked, actions }: { item: Item; opts: DraftOptions | null; profile: string; locked: boolean; actions: EditorActions }) {
  const set: SetChoice = (name, value) => actions.setChoice(item.key, name, value);
  const fieldProps = { choices: item.choices, set, busy: locked };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-4">
        <PictureFields {...fieldProps} />
      </div>
      <div className="min-w-0 space-y-4">
        {opts && <CampaignFields opts={opts} {...fieldProps} />}
        {opts && <AudioFields opts={opts} profile={profile} onMusicAdded={actions.addMusic} {...fieldProps} />}
      </div>
    </div>
  );
}

/** Det som bestämmer vad redigeraren visar och tillåter. */
function editorState(item: Item, opts: DraftOptions | null, profile: string, busy: boolean) {
  const { check, problem } = itemProblem(item, opts);
  return {
    locked: busy || item.state === "busy" || item.previewing,
    check,
    problem,
    // Längden står redan under tidslinjen; här bara klistermärkets fel.
    otherProblem: check?.ok === false ? null : problem,
    outdated: previewOutdated(item.preview?.key, item.choices, profile, item.title),
  };
}

/** Den valda videon: 9:16-bilden, tidslinjen, valen och knapparna. */
export function VideoEditor({ item, profile, opts, optsError, onOptsRetry, busy, others, showSafe, onShowSafe, actions }: Props) {
  if (item.state === "done") return <Saved item={item} />;
  const { locked, check, problem, otherProblem, outdated } = editorState(item, opts, profile, busy);
  return (
    <section className="mt-6 rounded-2xl border border-line bg-panel p-4 sm:p-5" aria-label={`Valen för ${item.file.name}`}>
      <h2 className="text-lg font-semibold break-words">{item.file.name}</h2>
      <div className="mt-4 grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
        <div className="space-y-3">
          <PreviewPane
            key={item.key}
            frameUrl={frameUrlFor(item, profile)}
            render={item.preview?.render}
            outdated={outdated}
            previewing={item.previewing}
            silent={item.choices.audio !== "keep"}
            safe={opts?.safe_zone}
            showSafe={showSafe}
            onShowSafe={onShowSafe}
          />
          <Actions item={item} blocked={!!problem} locked={locked} others={others} outdated={outdated} actions={actions} />
        </div>
        <div className="min-w-0 space-y-4">
          <EditorTimeline item={item} opts={opts} check={check} locked={locked} actions={actions} />
          {otherProblem && <p className="text-xs text-[#fca5a5]">{otherProblem}</p>}
          {optsError && <LoadError error={optsError} onRetry={onOptsRetry} className="" />}
          <EditorFields item={item} opts={opts} profile={profile} locked={locked} actions={actions} />
        </div>
      </div>
    </section>
  );
}
