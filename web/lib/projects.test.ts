import { describe, expect, it } from "vitest";
import { fileState, latestAttempt, projectEntries, projectSize, waitingText } from "./projects";
import type { CampaignProject, CampaignRunInfo, ProjectFile } from "./types";

function run(over: Partial<CampaignRunInfo> & { run_id: number }): CampaignRunInfo {
  return {
    vod_id: "2925991945",
    profile: "timmy",
    game: null,
    focus: null,
    min_s: null,
    max_s: null,
    title: null,
    channel_name: null,
    thumbnail_url: null,
    duration_s: null,
    created: null,
    status: "done",
    stage: null,
    pct: null,
    error: null,
    clip_counts: {},
    project_id: null,
    origin: null,
    ...over,
  };
}

function file(over: Partial<ProjectFile> & { run_id: number }): ProjectFile {
  return {
    vod_id: `fil-${over.run_id}`,
    title: null,
    status: "done",
    stage: null,
    pct: null,
    error: null,
    duration_s: 120,
    vod_bytes: null,
    created: "2026-11-10T10:00:00",
    ...over,
  };
}

function project(over: Partial<CampaignProject> & { id: number }): CampaignProject {
  return {
    profile: "blizzard",
    streamer: "Blizzard",
    title: "Egna filer för Blizzard",
    created: "2026-11-01T10:00:00",
    updated: "2026-11-10T10:00:00",
    active: false,
    pct: null,
    poster_url: null,
    clip_counts: {},
    files: [],
    ...over,
  };
}

describe("projectEntries", () => {
  it("visar inte körningar med project_id som egna kort, men varje projekt som ett kort", () => {
    const runs = [
      run({ run_id: 1, created: "2026-11-02T10:00:00" }),
      run({ run_id: 2, vod_id: "klipp.mp4", project_id: 7, created: "2026-11-03T10:00:00" }),
      run({ run_id: 3, vod_id: "annat.mp4", project_id: 7, created: "2026-11-04T10:00:00" }),
    ];
    const entries = projectEntries(runs, [project({ id: 7 })]);
    expect(entries.map((e) => e.key)).toEqual(["project-7", "run-1"]);
  });

  it("visar Twitch-körningar som kort, som i dag", () => {
    const runs = [run({ run_id: 4 }), run({ run_id: 5, vod_id: "123456789", project_id: null })];
    const entries = projectEntries(runs, []);
    expect(entries.map((e) => e.kind)).toEqual(["run", "run"]);
    expect(entries.map((e) => e.key).sort()).toEqual(["run-4", "run-5"]);
  });

  it("blandar projekt och körningar efter tid, senaste först", () => {
    const runs = [
      run({ run_id: 1, created: "2026-11-05T10:00:00" }),
      run({ run_id: 2, created: "2026-11-12T10:00:00" }),
    ];
    const projects = [project({ id: 9, created: "2026-11-01T10:00:00", updated: "2026-11-10T10:00:00" })];
    // Ett nyare projekt före en äldre körning, och en nyare körning före projektet.
    expect(projectEntries(runs, projects).map((e) => e.key)).toEqual(["run-2", "project-9", "run-1"]);
  });

  it("sorterar ett projekt utan updated efter created", () => {
    const runs = [run({ run_id: 1, created: "2026-11-05T10:00:00" })];
    const projects = [project({ id: 3, created: "2026-11-06T10:00:00", updated: null })];
    expect(projectEntries(runs, projects).map((e) => e.key)).toEqual(["project-3", "run-1"]);
  });
});

describe("waitingText", () => {
  it("säger vad som väntar, eller null när inget väntar", () => {
    expect(waitingText({ renderad: 1 })).toBe("1 att granska");
    expect(waitingText({ renderad: 2, underkand: 1 })).toBe("2 att granska, 1 att rätta");
    expect(waitingText({ godkand: 4 })).toBeNull();
  });
});

describe("projectSize", () => {
  it("räknar filer och klipp med rätt böjning", () => {
    expect(projectSize(project({ id: 1, files: [file({ run_id: 1 })], clip_counts: { renderad: 1 } }))).toBe("1 fil, 1 klipp");
    expect(
      projectSize(project({ id: 1, files: [file({ run_id: 1 }), file({ run_id: 2 })], clip_counts: { renderad: 3, godkand: 2 } }))
    ).toBe("2 filer, 5 klipp");
  });

  it("säger bara klippen när projektet bara har uppladdningar", () => {
    expect(projectSize(project({ id: 1, files: [], clip_counts: { godkand: 2 } }))).toBe("2 klipp");
  });
});

describe("fileState", () => {
  it("säger läget för en fil med steget och förloppet", () => {
    expect(fileState(file({ run_id: 1, status: "running", stage: "transcribe", pct: 41.6 }))).toBe("Transkriberar 42 %");
    expect(fileState(file({ run_id: 1, status: "pending" }))).toBe("I kö");
    expect(fileState(file({ run_id: 1, status: "failed" }))).toBe("Misslyckades");
    expect(fileState(file({ run_id: 1, status: "done" }))).toBe("Klar");
  });
});

describe("latestAttempt", () => {
  it("hittar det senaste försöket med samma stream och kampanj", () => {
    const list = [run({ run_id: 8 }), run({ run_id: 5 }), run({ run_id: 6, profile: "annan" })];
    expect(latestAttempt(list, run({ run_id: 5 }))).toBe(8);
  });

  it("väljer inte en körning som kampanjens chatt startade", () => {
    const list = [run({ run_id: 9, origin: "chat" }), run({ run_id: 5 })];
    expect(latestAttempt(list, run({ run_id: 5 }))).toBe(5);
  });

  it("ger null för en körning från chatten, den ersätts inte av ett nytt försök", () => {
    const list = [run({ run_id: 9 }), run({ run_id: 5, origin: "chat" })];
    expect(latestAttempt(list, run({ run_id: 5, origin: "chat" }))).toBeNull();
  });
});
