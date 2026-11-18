import type { LiveWatch, Peak, PeakStatus } from "./liveApi";

export function statusText(status: PeakStatus): string {
  throw new Error("inte byggt än");
}

export function timeText(iso: string): string {
  throw new Error("inte byggt än");
}

export type WatchState = "av" | "vantar" | "live";

export function watchState(w: LiveWatch): WatchState {
  throw new Error("inte byggt än");
}

export function newestFirst(peaks: Peak[]): Peak[] {
  throw new Error("inte byggt än");
}
