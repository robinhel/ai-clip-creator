"use client";

import { Copy, Eye, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { frameQuery, previewOutdated, type Choices } from "@/lib/uploads";
import { uploadsApi, type DraftOptions } from "@/lib/uploadsApi";
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
        Använd på alla kopierar valen, inte rubriken.
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

/** Den valda videon: 9:16-bilden, tidslinjen, valen och knapparna. */
export function VideoEditor({ item, profile, opts, optsError, onOptsRetry, busy, others, showSafe, onShowSafe, actions }: Props) {
  if (item.state === "done") return <Saved item={item} />;
  const locked = busy || item.state === "busy" || item.previewing;
  const set: SetChoice = (name, value) => actions.setChoice(item.key, name, value);
  const { check, problem } = itemProblem(item, opts);
  const outdated = previewOutdated(item.preview?.key, item.choices, profile, item.title);
  const frameUrl = item.draft && profile ? uploadsApi.frameUrl(item.draft.draft_id, frameQuery(item.choices, profile, item.title, clipTime(item))) : null;
  const fieldProps = { choices: item.choices, set, busy: locked };
  return (
    <section className="mt-6 rounded-2xl border border-line bg-panel p-5" aria-label={`Valen för ${item.file.name}`}>
      <h2 className="text-lg font-semibold break-words">{item.file.name}</h2>
      <div className="mt-4 grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
        <div className="space-y-3">
          <PreviewPane
            frameUrl={frameUrl}
            render={item.preview?.render ?? null}
            outdated={outdated}
            previewing={item.previewing}
            safe={opts?.safe_zone ?? null}
            showSafe={showSafe}
            onShowSafe={onShowSafe}
          />
          <Actions item={item} blocked={!!problem} locked={locked} others={others} outdated={outdated} actions={actions} />
        </div>
        <div className="min-w-0 space-y-4">
          {item.draft && check ? (
            <Timeline
              duration={item.draft.duration_s}
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
          ) : (
            <p className="text-sm text-muted-foreground">Tidslinjen visas när videon är uppladdad.</p>
          )}
          {problem && check?.ok !== false && <p className="text-xs text-[#fca5a5]">{problem}</p>}
          {optsError && <LoadError error={optsError} onRetry={onOptsRetry} className="" />}
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="min-w-0 space-y-4">
              <PictureFields {...fieldProps} />
            </div>
            <div className="min-w-0 space-y-4">{opts && <CampaignFields opts={opts} {...fieldProps} />}</div>
          </div>
        </div>
      </div>
    </section>
  );
}
