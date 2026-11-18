"use client";

import { useCallback, useRef, useState } from "react";
import {
  EXTRA_S,
  START_CHOICES,
  choicesForm,
  clipLength,
  copyChoices,
  lengthCheck,
  loadLastChoices,
  previewKey,
  rejectText,
  saveLastChoices,
  splitVideoFiles,
  stickerProblemText,
  type Choices,
  type LengthCheck,
} from "@/lib/uploads";
import { UploadAborted, uploadDraft, uploadsApi, type Draft, type DraftOptions, type DraftRender } from "@/lib/uploadsApi";
import { errorText } from "./ui";

/** En video i listan: först uppladdad som utkast, sedan sparad som klipp. */
export type Item = {
  key: string;
  file: File;
  title: string;
  choices: Choices;
  /** Valen är kampanjens senaste. De byts när kampanjen byts, tills Robin ändrar något på videon. */
  fresh: boolean;
  upload: "uploading" | "ready" | "failed";
  percent: number;
  uploadError?: string;
  draft?: Draft;
  /** Spara: väntar, sparas, klar eller fel. */
  state: "waiting" | "busy" | "done" | "error";
  message?: string;
  /** Bildens tid i källvideon, markören på tidslinjen. */
  atS: number;
  previewing: boolean;
  preview?: { render: DraftRender; key: string };
  previewError?: string;
};

export const isWaiting = (i: Item) => i.upload === "ready" && (i.state === "waiting" || i.state === "error");
/** Inte sparad än: varningen innan sidan lämnas gäller den. */
export const isUnsaved = (i: Item) => i.state !== "done";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** De senaste valen i kampanjen, eller startvalen. */
export function lastChoices(profile: string): Choices {
  const s = storage();
  return profile && s ? loadLastChoices(s, profile) : { ...START_CHOICES };
}

/** Sparar valen som kampanjens senaste. */
export function rememberChoices(profile: string, c: Choices): void {
  const s = storage();
  if (profile && s) saveLastChoices(s, profile, c);
}

/** Längden mot kampanjens gränser och det som stoppar Förhandsvisa och Spara. */
export function itemProblem(item: Item, opts: DraftOptions | null): { check: LengthCheck | null; problem: string | null } {
  const check =
    item.draft && opts ? lengthCheck(clipLength(item.choices, item.draft.duration_s) + EXTRA_S, opts.min_seconds, opts.max_seconds) : null;
  const lengthText = check && !check.ok ? check.text : null;
  return { check, problem: lengthText ?? stickerProblemText(item.choices) };
}

/** Bildens tid i det färdiga klippet: markören i källvideon, efter starten och hastigheten. */
export const clipTime = (item: Item) => Math.round((Math.max(item.atS - item.choices.startS, 0) / item.choices.speed) * 100) / 100;

function newItem(file: File, profile: string): Item {
  const key = `${file.name}-${file.size}-${Math.random()}`;
  return { key, file, title: "", choices: lastChoices(profile), fresh: true, upload: "uploading", percent: 0, state: "waiting", atS: 0, previewing: false };
}

/** Videorna i listan och det som går att göra med dem. */
export function useItems() {
  const [items, setItems] = useState<Item[]>([]);
  const [rejected, setRejected] = useState<string | null>(null);
  const aborts = useRef(new Map<string, () => void>());

  const update = useCallback(
    (key: string, change: Partial<Item> | ((item: Item) => Partial<Item>)) =>
      setItems((list) => list.map((item) => (item.key === key ? { ...item, ...(typeof change === "function" ? change(item) : change) } : item))),
    []
  );

  const startUpload = useCallback(
    (item: Item) => {
      update(item.key, { upload: "uploading", percent: 0, uploadError: undefined });
      const { done, abort } = uploadDraft(item.file, (percent) => update(item.key, { percent }));
      aborts.current.set(item.key, abort);
      done
        .then((draft) => update(item.key, { upload: "ready", draft }))
        .catch((err) => {
          // Avbryt tar bort videon ur listan.
          if (err instanceof UploadAborted) setItems((list) => list.filter((i) => i.key !== item.key));
          else update(item.key, { upload: "failed", uploadError: errorText(err, "Uppladdningen misslyckades.") });
        })
        .finally(() => aborts.current.delete(item.key));
    },
    [update]
  );

  const add = useCallback(
    (files: ArrayLike<File> | null, profile: string): Item[] => {
      const { videos, rejected: names } = splitVideoFiles(files ?? []);
      setRejected(rejectText(names));
      const added = videos.map((file) => newItem(file, profile));
      setItems((list) => [...list, ...added]);
      added.forEach(startUpload);
      return added;
    },
    [startUpload]
  );

  function remove(item: Item) {
    aborts.current.get(item.key)?.();
    // Ett sparat utkast är redan borta på servern.
    if (item.draft && item.state !== "done") uploadsApi.remove(item.draft.draft_id).catch(() => {});
    setItems((list) => list.filter((i) => i.key !== item.key));
  }

  const removeDone = () => setItems((list) => list.filter(isUnsaved));

  function setChoice<K extends keyof Choices>(key: string, name: K, value: Choices[K]) {
    update(key, (item) => ({ choices: { ...item.choices, [name]: value }, fresh: false }));
  }

  function setTimes(key: string, startS: number, endS: number, atS: number) {
    update(key, (item) => ({ choices: { ...item.choices, startS, endS }, atS, fresh: false }));
  }

  /** Använd på alla: valen till videorna som inte är klara, utan rubriken. */
  function applyToAll(from: Item) {
    setItems((list) =>
      list.map((item) =>
        item.key === from.key || item.state === "done" || item.state === "busy"
          ? item
          : { ...item, choices: copyChoices(from.choices, item.draft?.duration_s), fresh: false }
      )
    );
  }

  /** Byte av kampanj: overlayn börjar om, och videor med kampanjens senaste val får den nya kampanjens. */
  const changeProfile = useCallback((profile: string) => {
    setItems((list) =>
      list.map((item) => {
        if (item.state === "done") return item;
        if (item.fresh) return { ...item, choices: lastChoices(profile) };
        return { ...item, choices: { ...item.choices, overlay: "" } };
      })
    );
  }, []);

  async function preview(item: Item, profile: string) {
    if (!item.draft) return;
    update(item.key, { previewing: true, previewError: undefined });
    const key = previewKey(item.choices, profile, item.title);
    try {
      const render = await uploadsApi.render(item.draft.draft_id, choicesForm(item.choices, profile, item.title));
      update(item.key, { preview: { render, key } });
    } catch (err) {
      update(item.key, { previewError: errorText(err, "Förhandsvisningen gick inte att göra.") });
    } finally {
      update(item.key, { previewing: false });
    }
  }

  /** Spara klippet. Servern gör om videon först om valen inte är de förhandsvisade. true när det gick. */
  async function save(item: Item, profile: string): Promise<boolean> {
    if (!item.draft) return false;
    update(item.key, { state: "busy", message: undefined });
    try {
      const clip = await uploadsApi.save(item.draft.draft_id, choicesForm(item.choices, profile, item.title));
      update(item.key, { state: "done", message: `Klipp #${clip.id}` });
      return true;
    } catch (err) {
      update(item.key, { state: "error", message: errorText(err, "Gick inte att göra om") });
      return false;
    }
  }

  const setAt = (key: string, atS: number) => update(key, { atS });

  return { items, rejected, add, remove, removeDone, update, setChoice, setTimes, setAt, applyToAll, changeProfile, preview, save, retryUpload: startUpload };
}
