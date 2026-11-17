type Visibility = EventTarget & { visibilityState: DocumentVisibilityState };

/** Två händelser närmare än så räknas som en: fokus och visibilitychange kommer ofta samtidigt. */
const SAME_MOMENT_MS = 2_000;

/**
 * Kör `run` var `everyMs` medan fliken syns, och direkt när den syns igen eller fönstret får fokus.
 * Ingen körning när fliken är dold. Den första läsningen gör sidan själv. Returnerar en funktion som stoppar.
 */
export function pollWhileVisible(
  run: () => void,
  everyMs: number,
  doc: Visibility = document,
  win: EventTarget = window
): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  let last = Date.now();

  const tick = () => {
    last = Date.now();
    run();
  };
  const start = () => {
    if (timer === null) timer = setInterval(tick, everyMs);
  };
  const stop = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };
  const now = () => {
    if (doc.visibilityState !== "visible" || Date.now() - last < SAME_MOMENT_MS) return;
    // Nästa läsning kommer en hel period efter den här.
    stop();
    tick();
    start();
  };
  const onVisibility = () => {
    if (doc.visibilityState === "visible") now();
    else stop();
  };

  if (doc.visibilityState === "visible") start();
  doc.addEventListener("visibilitychange", onVisibility);
  win.addEventListener("focus", now);
  return () => {
    stop();
    doc.removeEventListener("visibilitychange", onVisibility);
    win.removeEventListener("focus", now);
  };
}
