import run from "../fixtures/run.json";
import clips from "../fixtures/clips.json";
import type { Run, Clip } from "../../lib/types";

const doneRun = run as Run;
const sampleClips = clips as Clip[];

describe("Run detail page", () => {
  beforeEach(() => {
    // Körningarna i testerna nedan hör inte till någon kampanj, om inte testet säger annat.
    cy.intercept("GET", "/api/campaign/runs", { body: [] });
  });

  it("shows campaign clips without Godkänn or Neka for a campaign run", () => {
    cy.intercept("GET", "/api/runs/1", { body: doneRun }).as("getRun");
    cy.intercept("GET", "/api/runs/1/clips", { body: sampleClips });
    cy.intercept("GET", "/api/campaign/runs", {
      body: [{ run_id: 1, profile: "exempel", game: null, vod_id: doneRun.vod_id }],
    });
    cy.intercept("GET", "/api/campaign/clips*", {
      body: [
        {
          id: 7, vod_id: doneRun.vod_id, profile: "exempel", start_s: 10, end_s: 40,
          title: "Kampanjklipp ett", format: null, signals: [], filename: "a.mp4", caption: "",
          status: "godkand", problems: [], warnings: [], review: null, platform: null, url: null,
          views: null, likes: null, comments: null, shares: null, note: null,
          created: "2026-01-01T00:00:00Z", updated: "2026-01-01T00:00:00Z",
          engagement: null, engagement_min: null, video_url: "/campaign/clips/7/file",
        },
      ],
    });
    cy.visit("/runs/1");
    cy.wait("@getRun");
    cy.contains("Kampanjklipp ett").should("be.visible");
    cy.contains("Öppna i kön").should("have.attr", "href", "/queue");
    cy.contains("button", "Godkänn").should("not.exist");
    cy.contains("Epic play").should("not.exist");
  });

  it("shows run VOD ID and Klar badge", () => {
    cy.intercept("GET", "/api/runs/1", { body: doneRun }).as("getRun");
    cy.intercept("GET", "/api/runs/1/clips", { body: sampleClips }).as("getClips");
    cy.visit("/runs/1");
    cy.wait("@getRun");
    cy.contains(doneRun.vod_id).should("be.visible");
    cy.contains("Klar").should("be.visible");
  });

  it("renders a ClipCard for each clip returned", () => {
    cy.intercept("GET", "/api/runs/1", { body: doneRun }).as("getRun");
    cy.intercept("GET", "/api/runs/1/clips", { body: sampleClips }).as("getClips");
    cy.visit("/runs/1");
    cy.wait("@getClips");
    cy.contains("Klipp (3)").should("be.visible");
    cy.contains("Epic play").should("be.visible");
    cy.contains("Funny moment").should("be.visible");
  });

  it("Godkänn button updates the clip badge to Godkänd", () => {
    const updatedClip = { ...sampleClips[0], approved: true };
    cy.intercept("GET", "/api/runs/1", { body: doneRun }).as("getRun");
    cy.intercept("GET", "/api/runs/1/clips", { body: sampleClips }).as("getClips");
    cy.intercept("PATCH", "/api/clips/1", { body: updatedClip }).as("patchClip");
    cy.visit("/runs/1");
    cy.wait("@getClips");
    cy.contains("Epic play").parents("[data-slot=card]").contains("Godkänn").click();
    cy.wait("@patchClip");
    cy.contains("Epic play").parents("[data-slot=card]").contains("Godkänd").should("be.visible");
  });

  it("shows error message and Back button for a 404 run", () => {
    cy.intercept("GET", "/api/runs/99", {
      statusCode: 404,
      body: { detail: "Not Found" },
    }).as("getRun");
    cy.intercept("GET", "/api/runs/99/clips", { body: [] }).as("getClips");
    cy.visit("/runs/99");
    cy.wait("@getRun");
    cy.contains("Not Found").should("be.visible");
    cy.contains("Tillbaka").should("be.visible");
  });

  it("shows Förlopp section for an active run", () => {
    const runningRun: Run = { ...doneRun, status: "running" };
    cy.intercept("GET", "/api/runs/1", { body: runningRun }).as("getRun");
    cy.intercept("GET", "/api/runs/1/clips", { body: [] }).as("getClips");
    cy.visit("/runs/1");
    cy.wait("@getRun");
    cy.contains("Förlopp").should("be.visible");
  });
});
