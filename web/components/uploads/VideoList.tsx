"use client";

import { Film, Loader2, X } from "lucide-react";
import { formatDuration, formatSize } from "@/lib/uploads";
import { cn } from "@/lib/utils";
import { field } from "./ui";
import type { Item } from "./useItems";

function ItemStatus({ item }: { item: Item }) {
  if (item.upload === "uploading") return <Spinning text={`Uploading ${item.percent} %`} />;
  if (item.upload === "failed") return <>{item.uploadError}</>;
  if (item.state === "busy") return <Spinning text="Making the video…" />;
  if (item.previewing) return <Spinning text="Previewing…" />;
  if (item.state === "done") return <>Klar: {item.message}</>;
  return <>{item.message ?? (item.preview ? "Previewed, not saved" : "Waiting")}</>;
}

function Spinning({ text }: { text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {text}
    </span>
  );
}

/** Den lilla bilden: bildens första ruta som klippet blir, eller en ikon medan videon laddas upp. */
function Thumb({ src }: { src: string | null }) {
  return (
    <span className="relative grid aspect-[9/16] w-9 shrink-0 place-items-center overflow-hidden rounded-md border border-line bg-black">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- bilden kommer från servern via proxyn.
        <img src={src} alt="" className="absolute inset-0 size-full object-cover" />
      ) : (
        <Film className="size-4 text-muted-foreground" aria-hidden />
      )}
    </span>
  );
}

function meta(item: Item): string {
  const parts = [item.draft ? formatDuration(item.draft.duration_s) : null, formatSize(item.file.size)];
  return parts.filter(Boolean).join(" · ");
}

function RowAction({ item, onRemove, onRetry }: { item: Item; onRemove: () => void; onRetry: () => void }) {
  if (item.upload === "uploading") {
    return (
      <button type="button" onClick={onRemove} className="h-8 rounded-lg border border-line px-2.5 text-xs font-medium hover:bg-white/5">
        Cancel
      </button>
    );
  }
  if (item.state === "busy") return null;
  return (
    <span className="flex items-center gap-2">
      {item.upload === "failed" && (
        <button type="button" onClick={onRetry} className="h-8 rounded-lg border border-line px-2.5 text-xs font-medium hover:bg-white/5">
          Upload again
        </button>
      )}
      <button type="button" aria-label={`Remove ${item.file.name} from the list`} onClick={onRemove} className="text-muted-foreground hover:text-foreground">
        <X className="size-4" aria-hidden />
      </button>
    </span>
  );
}

interface RowProps {
  item: Item;
  selected: boolean;
  thumb: string | null;
  burnsTitle: boolean;
  onSelect: () => void;
  onTitle: (title: string) => void;
  onRemove: () => void;
  onRetry: () => void;
}

function ItemRow({ item, selected, thumb, burnsTitle, onSelect, onTitle, onRemove, onRetry }: RowProps) {
  const failed = item.upload === "failed" || item.state === "error";
  return (
    <li
      onClick={onSelect}
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border px-3 py-2 text-sm",
        selected ? "border-brand bg-brand/10" : "border-line hover:border-muted-foreground"
      )}
    >
      <button type="button" aria-pressed={selected} onClick={onSelect} className="flex min-w-0 flex-1 basis-40 items-center gap-3 text-left">
        <Thumb src={thumb} />
        <span className="min-w-0">
          <span className="block truncate font-medium" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="block text-xs text-muted-foreground tabular-nums">{meta(item)}</span>
        </span>
      </button>
      <input
        aria-label={`Headline for ${item.file.name}`}
        placeholder={burnsTitle ? "Headline (burned in at the start, optional)" : "Headline for the caption (optional)"}
        value={item.title}
        onChange={(e) => onTitle(e.target.value)}
        onFocus={onSelect}
        disabled={item.state === "busy" || item.state === "done"}
        className={cn(field, "mt-0 w-full min-w-0 basis-full sm:w-64 sm:basis-auto")}
      />
      <span role={failed ? "alert" : undefined} className={cn("min-w-0 text-xs break-words", failed ? "text-[#fca5a5]" : "text-muted-foreground")}>
        <ItemStatus item={item} />
      </span>
      <span className="ml-auto" onClick={(e) => e.stopPropagation()}>
        <RowAction item={item} onRemove={onRemove} onRetry={onRetry} />
      </span>
    </li>
  );
}

interface ListProps {
  items: Item[];
  selected: string | null;
  burnsTitle: boolean;
  thumbFor: (item: Item) => string | null;
  onSelect: (key: string) => void;
  onTitle: (key: string, title: string) => void;
  onRemove: (item: Item) => void;
  onRetry: (item: Item) => void;
  onRemoveDone: () => void;
}

/** Videorna i listan, med bild, namn, längd, storlek, läge och rubrik. */
export function VideoList({ items, selected, burnsTitle, thumbFor, onSelect, onTitle, onRemove, onRetry, onRemoveDone }: ListProps) {
  const done = items.filter((i) => i.state === "done").length;
  return (
    <div className="space-y-2">
      <ul className="space-y-2" aria-label="Videos">
        {items.map((item) => (
          <ItemRow
            key={item.key}
            item={item}
            selected={item.key === selected}
            thumb={thumbFor(item)}
            burnsTitle={burnsTitle}
            onSelect={() => onSelect(item.key)}
            onTitle={(title) => onTitle(item.key, title)}
            onRemove={() => onRemove(item)}
            onRetry={() => onRetry(item)}
          />
        ))}
      </ul>
      {done > 0 && (
        <button type="button" onClick={onRemoveDone} className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground">
          Remove {done === 1 ? "the finished video" : `the ${done} finished videos`} from the list
        </button>
      )}
    </div>
  );
}
