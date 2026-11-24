import { describe, expect, it } from "vitest";
import { hm, timelineLayout, type Timeline } from "./timeline";

const empty: Timeline = {
  duration_s: 0,
  chat_density: [],
  audio_energy: [],
  hype: [],
  viewer_clips: [],
  candidates: [],
  picks: [],
};

describe("hm", () => {
  it("writes hours and minutes", () => {
    expect(hm(0)).toBe("0:00");
    expect(hm(59)).toBe("0:00");
    expect(hm(3600 + 5 * 60)).toBe("1:05");
    expect(hm(13_950)).toBe("3:52");
  });
});

describe("timelineLayout", () => {
  // 3 h 52 min 30 s: not a round number of hours, so the last tick is not at the edge.
  const data: Timeline = {
    ...empty,
    duration_s: 13_950,
    chat_density: [
      [0, 0],
      [6975, 1],
      [13_950, 0.5],
    ],
    audio_energy: [
      [0, 1],
      [13_950, 0],
    ],
    hype: [6975],
    viewer_clips: [13_950],
    picks: [
      { start_s: 600, end_s: 645, title: "Early" },
      { start_s: 13_920, end_s: 13_990, title: "Right at the end" },
    ],
  };
  const layout = timelineLayout(data, { width: 1000, height: 100 });

  it("maps time to x over the whole width", () => {
    expect(layout.ticks[0].x).toBe(0);
    expect(layout.ticks[1].x).toBeCloseTo((1800 / 13_950) * 1000);
    expect(layout.picks[0].x).toBeCloseTo((600 / 13_950) * 1000);
    const late = layout.picks[1];
    expect(late.x + late.width).toBe(1000); // a pick that ends after the VOD stops at the edge
  });

  it("puts the highest chat density at the top and closes the area at the bottom", () => {
    expect(layout.chatLine).toBe("M0,100 L500,0 L1000,50");
    expect(layout.chatArea).toBe("M0,100 L500,0 L1000,50 L1000,100 L0,100 Z");
    expect(layout.audioLine).toBe("M0,0 L1000,100");
  });

  it("gives the hype words and viewer clips as x", () => {
    expect(layout.hype).toEqual([500]);
    expect(layout.viewerClips).toEqual([1000]);
  });

  it("ticks every 30 minutes, labelled h:mm, never past the end", () => {
    expect(layout.ticks.map((t) => t.label)).toEqual(["0:00", "0:30", "1:00", "1:30", "2:00", "2:30", "3:00", "3:30"]);
    expect(layout.ticks.at(-1)?.x).toBeCloseTo((12_600 / 13_950) * 1000);
  });

  it("keeps a pick near the end inside the plot and anchors its label to the right", () => {
    const [early, late] = layout.picks;
    expect(early.anchor).toBe("start");
    expect(early.x).toBeCloseTo((600 / 13_950) * 1000);
    expect(early.time).toBe("0:10:00–0:10:45");
    expect(late.anchor).toBe("end");
    expect(late.x + late.width).toBeLessThanOrEqual(1000);
    expect(late.width).toBeGreaterThanOrEqual(4); // too short to see becomes at least 4 units wide
    expect(late.title).toBe("Right at the end");
  });

  it("shows a label only when it does not run into the label before it", () => {
    const close = timelineLayout(
      { ...data, picks: [
        { start_s: 12_400, end_s: 12_430, title: "Jump scare in the vault" },
        { start_s: 13_880, end_s: 13_925, title: "Last-second extract" },
        { start_s: 600, end_s: 640, title: "Early" },
      ] },
      { width: 1000, height: 100 }
    );
    expect(close.picks.map((p) => p.showLabel)).toEqual([true, false, true]);
  });

  it("draws nothing for a VOD without a length", () => {
    const none = timelineLayout(empty, { width: 1000, height: 100 });
    expect(none.chatArea).toBe("");
    expect(none.ticks).toEqual([]);
    expect(none.picks).toEqual([]);
  });
});
