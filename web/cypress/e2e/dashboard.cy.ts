import runs from "../fixtures/runs.json";
import type { CampaignProfile, Run } from "../../lib/types";

const [doneRun, pendingRun] = runs as Run[];
const newRun: Run = {
  ...doneRun,
  id: 3,
  vod_id: "9999999999",
  status: "pending",
  clip_count: 0,
};

const profile: CampaignProfile = {
  name: "exempel",
  streamer: "Exempelstreamer",
  campaign: "Exempelkampanj",
  language: "en",
  channel: "exempel",
  formats: [],
  problems: [],
  ready: true,
};

describe("Dashboard page", () => {
  it("shows empty state when no runs exist", () => {
    cy.intercept("GET", "/api/runs", { body: [] }).as("getRuns");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("Inga körningar än.").should("be.visible");
  });

  it("renders a RunCard for each run returned", () => {
    cy.intercept("GET", "/api/runs", { body: runs }).as("getRuns");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains(doneRun.vod_id).should("be.visible");
    cy.contains(pendingRun.vod_id).should("be.visible");
  });

  it("shows Swedish status badges for pending and done runs", () => {
    cy.intercept("GET", "/api/runs", { body: runs }).as("getRuns");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("Klar").should("be.visible");
    cy.contains("Väntar").should("be.visible");
  });

  it("shows the campaign name on runs that belong to a campaign", () => {
    cy.intercept("GET", "/api/runs", { body: runs }).as("getRuns");
    cy.intercept("GET", "/api/campaign/runs", {
      body: [{ run_id: doneRun.id, profile: "exempel", game: "Fortnite", vod_id: doneRun.vod_id }],
    });
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("exempel · Fortnite").should("be.visible");
  });

  it("opens Ny körning dialog on button click", () => {
    cy.intercept("GET", "/api/runs", { body: [] }).as("getRuns");
    cy.intercept("GET", "/api/campaign/profiles", { body: [profile] });
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("Ny körning").click();
    cy.contains("VOD-id").should("be.visible");
  });

  it("creates a run and shows new card at top of list", () => {
    cy.intercept("GET", "/api/runs", { body: [] }).as("getRuns");
    cy.intercept("GET", "/api/campaign/profiles", { body: [profile] });
    cy.intercept("POST", "/api/campaign/runs", { statusCode: 201, body: newRun }).as("postRun");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("Ny körning").click();
    cy.get("#run-vod").type("9999999999");
    cy.get("button[type=submit]").click();
    cy.wait("@postRun");
    cy.contains("9999999999").should("be.visible");
  });

  it("shows the server's message in the dialog when a run is already going", () => {
    cy.intercept("GET", "/api/runs", { body: [] }).as("getRuns");
    cy.intercept("GET", "/api/campaign/profiles", { body: [profile] });
    cy.intercept("POST", "/api/campaign/runs", {
      statusCode: 429,
      body: { detail: "En körning pågår redan." },
    }).as("postRun");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains("Ny körning").click();
    cy.get("#run-vod").type("9999999999");
    cy.get("button[type=submit]").click();
    cy.wait("@postRun");
    cy.contains("En körning pågår redan.").should("be.visible");
  });

  it("navigates to run detail page when a run card is clicked", () => {
    cy.intercept("GET", "/api/runs", { body: runs }).as("getRuns");
    cy.visit("/");
    cy.wait("@getRuns");
    cy.contains(doneRun.vod_id).closest("a").click();
    cy.url().should("include", `/runs/${doneRun.id}`);
  });
});
