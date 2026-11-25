import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_OFF, demoFetch, resetDemo } from "./index";
import type { CampaignClip, CampaignProfile, CampaignProject, CampaignRunInfo, CampaignSummary, TranscriptionSetting } from "../types";
import type { Timeline } from "../timeline";

afterEach(() => {
  resetDemo();
  vi.unstubAllGlobals();
});

describe("demoFetch", () => {
  it("never calls fetch", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    await demoFetch("/campaign/summary");
    await demoFetch("/campaign/clips", { method: "PATCH" }).catch(() => {});
    await demoFetch("/campaign/keys", { method: "PUT", body: "{}" }).catch(() => {});
    expect(spy).not.toHaveBeenCalled();
  });

  it("uses a made-up channel and the same data every time", async () => {
    const profiles = await demoFetch<CampaignProfile[]>("/campaign/profiles");
    expect(profiles.map((p) => p.channel)).toContain("nova_plays");
    const a = await demoFetch<Timeline>("/campaign/runs/101/timeline");
    resetDemo();
    const b = await demoFetch<Timeline>("/campaign/runs/101/timeline");
    expect(a).toEqual(b);
    expect(a.picks.length).toBeGreaterThan(0);
    expect(a.chat_density.every(([t, v]) => t >= 0 && t <= a.duration_s && v >= 0 && v <= 1)).toBe(true);
  });

  it("filters clips like the server", async () => {
    const all = await demoFetch<CampaignClip[]>("/campaign/clips");
    const review = await demoFetch<CampaignClip[]>("/campaign/clips?status=renderad");
    const run = await demoFetch<CampaignClip[]>("/campaign/clips?run_id=101");
    expect(review.length).toBeGreaterThan(0);
    expect(review.every((c) => c.status === "renderad")).toBe(true);
    expect(run.length).toBeGreaterThan(0);
    expect(run.length).toBeLessThan(all.length);
  });

  it("approving a clip changes only the memory, and the counts follow", async () => {
    const [clip] = await demoFetch<CampaignClip[]>("/campaign/clips?status=renderad");
    const before = await demoFetch<CampaignSummary>("/campaign/summary");
    const saved = await demoFetch<CampaignClip>(`/campaign/clips/${clip.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "godkand" }),
    });
    expect(saved.status).toBe("godkand");
    const after = await demoFetch<CampaignSummary>("/campaign/summary");
    expect(after.counts.godkand).toBe((before.counts.godkand ?? 0) + 1);
    expect(after.counts.renderad).toBe((before.counts.renderad ?? 0) - 1);
  });

  it("refuses what needs the server with a clear text", async () => {
    await expect(demoFetch("/campaign/keys", { method: "PUT", body: "{}" })).rejects.toThrow(DEMO_OFF);
    await expect(demoFetch("/campaign/runs", { method: "POST", body: "{}" })).rejects.toThrow(DEMO_OFF);
  });

  it("has a project for own files that opens, with its clips and a timeline without chat", async () => {
    const [listed] = await demoFetch<CampaignProject[]>("/campaign/projects");
    expect(listed).toBeDefined();
    const project = await demoFetch<CampaignProject>(`/campaign/projects/${listed.id}`);
    expect(project.files.length).toBeGreaterThan(0);
    const vods = new Set(project.files.map((f) => f.vod_id));
    const clips = await demoFetch<CampaignClip[]>(`/campaign/clips?project_id=${project.id}`);
    expect(clips.length).toBeGreaterThan(0);
    expect(clips.every((c) => vods.has(c.vod_id))).toBe(true);
    const runs = await demoFetch<CampaignRunInfo[]>("/campaign/runs");
    expect(runs.find((r) => r.run_id === project.files[0].run_id)?.project_id).toBe(project.id);
    const timeline = await demoFetch<Timeline>(`/campaign/runs/${project.files[0].run_id}/timeline`);
    expect(timeline.picks.length).toBe(clips.length);
    expect(timeline.chat_density).toEqual([]);
    await expect(demoFetch("/campaign/projects/9999")).rejects.toThrow("The project does not exist.");
  });

  it("answers 404 for an unknown run", async () => {
    await expect(demoFetch<CampaignRunInfo>("/campaign/runs/9999")).rejects.toThrow("The project does not exist.");
  });

  it("switches the transcription in memory only, without downloading", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    const start = await demoFetch<TranscriptionSetting>("/campaign/transcription");
    expect(start.engine).toBe("berget");
    const local = await demoFetch<TranscriptionSetting>("/campaign/transcription", {
      method: "PUT",
      body: JSON.stringify({ engine: "local", model: "medium" }),
    });
    expect(local).toMatchObject({ engine: "local", model: "medium" });
    expect(local.models.find((m) => m.id === "medium")).toMatchObject({ size_gb: 1.5, downloaded: false });
    expect((await demoFetch<TranscriptionSetting>("/campaign/transcription")).engine).toBe("local");
    for (const wrong of [{ engine: "cloud" }, { engine: "local", berget_key: false }, {}, []]) {
      await expect(demoFetch("/campaign/transcription", { method: "PUT", body: JSON.stringify(wrong) })).rejects.toThrow();
    }
    expect((await demoFetch<TranscriptionSetting>("/campaign/transcription")).berget_key).toBe(true);
    resetDemo();
    expect((await demoFetch<TranscriptionSetting>("/campaign/transcription")).engine).toBe("berget");
    expect(spy).not.toHaveBeenCalled();
  });
});
