import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pollWhileVisible } from "./poll";

/** En låtsas-document med visibilityState som testet styr. */
function fakeDocument(state: "visible" | "hidden") {
  const target = new EventTarget();
  return Object.assign(target, { visibilityState: state as DocumentVisibilityState });
}

describe("pollWhileVisible", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("läser om var 15:e sekund medan fliken syns", () => {
    const run = vi.fn();
    const doc = fakeDocument("visible");
    const stop = pollWhileVisible(run, 15_000, doc, new EventTarget());
    expect(run).not.toHaveBeenCalled(); // sidan läser själv när den öppnas
    vi.advanceTimersByTime(14_999);
    expect(run).toHaveBeenCalledTimes(0);
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    expect(run).toHaveBeenCalledTimes(3);
    stop();
    vi.advanceTimersByTime(60_000);
    expect(run).toHaveBeenCalledTimes(3);
  });

  it("läser inte när fliken är dold, och direkt när den syns igen", () => {
    const run = vi.fn();
    const doc = fakeDocument("hidden");
    pollWhileVisible(run, 15_000, doc, new EventTarget());
    vi.advanceTimersByTime(60_000);
    expect(run).not.toHaveBeenCalled();

    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(15_000);
    expect(run).toHaveBeenCalledTimes(2);

    doc.visibilityState = "hidden";
    doc.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(60_000);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("läser direkt när fönstret får fokus, men bara en gång när fokus och synlighet kommer samtidigt", () => {
    const run = vi.fn();
    const doc = fakeDocument("visible");
    const win = new EventTarget();
    pollWhileVisible(run, 15_000, doc, win);
    vi.advanceTimersByTime(5_000);
    win.dispatchEvent(new Event("focus"));
    expect(run).toHaveBeenCalledTimes(1);
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(run).toHaveBeenCalledTimes(1);
  });
});
