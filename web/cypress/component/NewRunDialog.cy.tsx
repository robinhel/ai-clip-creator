import { NewRunDialog, parseTime } from "../../components/NewRunDialog";
import { api } from "../../lib/api";
import type { CampaignProfile, Run } from "../../lib/types";

const fakeRun: Run = {
  id: 1,
  vod_id: "123456",
  channel: "",
  status: "pending",
  stage: null,
  error: null,
  prompt_version: "v1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  clip_count: 0,
};

const ready: CampaignProfile = {
  name: "exempel",
  streamer: "Exempelstreamer",
  campaign: "Exempelkampanj",
  language: "en",
  channel: "exempel",
  formats: [],
  problems: [],
  ready: true,
};

const blocked: CampaignProfile = {
  ...ready,
  name: "trasig",
  campaign: "Trasig kampanj",
  problems: ["Overlay-bilden saknas"],
  ready: false,
};

describe("parseTime", () => {
  it("reads seconds, m:ss and h:mm:ss", () => {
    expect(parseTime("")).to.equal(undefined);
    expect(parseTime("90")).to.equal(90);
    expect(parseTime("1:30")).to.equal(90);
    expect(parseTime("1:02:30")).to.equal(3750);
    expect(parseTime("abc")).to.be.NaN;
  });
});

describe("NewRunDialog", () => {
  beforeEach(() => {
    cy.stub(api.campaign, "profiles").resolves([ready, blocked]);
  });

  it("dialog is closed by default", () => {
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("VOD-id").should("not.exist");
  });

  it("opens on trigger click and lists the campaigns", () => {
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("Ny körning").click();
    cy.contains("VOD-id").should("be.visible");
    cy.get("select option").should("have.length", 2);
  });

  it("submit disabled when VOD-id is empty", () => {
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("Ny körning").click();
    cy.get("button[type=submit]").should("be.disabled");
  });

  it("blocks a campaign that is not ready and shows why", () => {
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("Ny körning").click();
    cy.get("select").select("trasig");
    cy.get("#run-vod").type("123456");
    cy.contains("Overlay-bilden saknas").should("be.visible");
    cy.get("button[type=submit]").should("be.disabled");
  });

  it("starts the run through the campaign API and calls onCreated", () => {
    cy.stub(api.campaign.runs, "create").as("create").resolves(fakeRun);
    const onCreated = cy.spy().as("onCreated");
    cy.mount(<NewRunDialog onCreated={onCreated} />);
    cy.contains("Ny körning").click();
    cy.get("#run-vod").type("123456");
    cy.get("#run-game").type("Fortnite");
    cy.get("#run-start").type("1:00");
    cy.get("button[type=submit]").click();
    cy.get("@create").should("have.been.calledWith", {
      profile: "exempel",
      vod_id: "123456",
      game: "Fortnite",
      start_s: 60,
      end_s: undefined,
    });
    cy.get("@onCreated").should("have.been.calledOnce");
  });

  it("shows API error inside the dialog", () => {
    cy.stub(api.campaign.runs, "create").rejects(new Error("En körning pågår redan."));
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("Ny körning").click();
    cy.get("#run-vod").type("123456");
    cy.get("button[type=submit]").click();
    cy.contains("En körning pågår redan.").should("be.visible");
  });

  it("Avbryt closes dialog without calling create", () => {
    cy.stub(api.campaign.runs, "create").as("create");
    cy.mount(<NewRunDialog onCreated={cy.spy()} />);
    cy.contains("Ny körning").click();
    cy.contains("Avbryt").click();
    cy.contains("VOD-id").should("not.exist");
    cy.get("@create").should("not.have.been.called");
  });
});
