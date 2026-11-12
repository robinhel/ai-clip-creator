import { RunCard } from "../../components/RunCard";
import type { Run } from "../../lib/types";

const base: Run = {
  id: 1,
  vod_id: "2763505810",
  channel: "chuyelwuero",
  status: "done",
  stage: null,
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  clip_count: 0,
};

describe("RunCard", () => {
  it("shows Väntar badge and 0 klipp", () => {
    cy.mount(<RunCard run={{ ...base, status: "pending" }} />);
    cy.contains("Väntar").should("be.visible");
    cy.contains("0 klipp").should("be.visible");
  });

  it("shows Kör badge and stage label", () => {
    cy.mount(<RunCard run={{ ...base, status: "running", stage: "transcribe" }} />);
    cy.contains("Kör").should("be.visible");
    cy.contains("transcribe…").should("be.visible");
  });

  it("shows Klar badge with clip count", () => {
    cy.mount(<RunCard run={{ ...base, status: "done", clip_count: 5 }} />);
    cy.contains("Klar").should("be.visible");
    cy.contains("5 klipp").should("be.visible");
  });

  it("shows the campaign name when the run belongs to a campaign", () => {
    const campaign = { run_id: 1, profile: "exempelkampanj", game: "Fortnite", vod_id: base.vod_id };
    cy.mount(<RunCard run={base} campaign={campaign} />);
    cy.contains("exempelkampanj · Fortnite").should("be.visible");
  });

  it("shows Misslyckades badge with error text", () => {
    cy.mount(<RunCard run={{ ...base, status: "failed", error: "oom" }} />);
    cy.contains("Misslyckades").should("be.visible");
    cy.contains("oom").should("be.visible");
  });

  it("renders anchor linking to the run detail page", () => {
    cy.mount(<RunCard run={{ ...base, status: "done" }} />);
    cy.get("a").should("have.attr", "href", "/runs/1");
  });
});
